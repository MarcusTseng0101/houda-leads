"""方案 B（使用者 2026-10-20 選的：不用 LLM，加規則）：分清楚「做什麼」和「提到什麼」。"""
import datetime as dt

from houda_leads import channel, classify, cli, extract, match
from houda_leads.rules import Industry, Keyword

PAGE = """<html><body>
<nav class="main-menu"><a href="/app">產品應用</a> 電源供應 UPS 不斷電 儲能</nav>
<div id="content"><h1>範例半導體股份有限公司</h1>
<p>本公司專業生產整流二極體、功率電晶體與肖特基二極體。</p>
<p>產品應用於電源供應器、UPS 與充電樁。</p></div>
<footer>統一編號 12345678 總機 02-1234-5678</footer></body></html>"""


def test_content_text_drops_menu_but_full_text_keeps_footer():
    c = extract.content_text(PAGE)
    assert "儲能" not in c and "統一編號" not in c
    full = extract.visible_text(PAGE)
    assert "統一編號 12345678" in full and "02-1234-5678" in full


def test_content_text_falls_back_when_everything_looks_like_menu():
    html = '<html><body><div class="header-menu"><p>我們專業製造 UPS 不斷電系統與儲能設備，歡迎洽詢。</p></div></body></html>'
    assert "不斷電" in extract.content_text(html)


def test_application_context_is_only_a_mention(cfg):
    inds = [Industry("電源", (Keyword("ups", True), Keyword("充電樁", False))),
            Industry("元件", (Keyword("二極體", False),))]
    t = [extract.PageText("u", extract.content_text(PAGE))]
    c = classify.classify(t, inds, cfg)
    assert c.industry == "元件"
    assert c.mentions.get("電源") == ["ups", "充電樁"]


def test_same_keyword_outside_application_counts(cfg):
    inds = [Industry("電源", (Keyword("ups", True),))]
    t = [extract.PageText("u", "產品應用於 UPS。本公司製造 UPS 不斷電系統。")]
    assert classify.classify(t, inds, cfg).industry == "電源"


def test_evidence_never_crosses_removed_element(cfg):
    html = '<html><body><p>我們製造<nav class="menu">儲能</nav>工業用 UPS 不斷電系統</p></body></html>'
    t = [extract.PageText("u", extract.content_text(html))]
    c = classify.classify(t, [Industry("電源", (Keyword("ups", True),))], cfg)
    q = c.evidence[0][0]
    assert extract.SENTINEL not in q and "我們製造" not in q and "UPS" in q


def test_text_variants(cfg):
    inds = [Industry("金屬", (Keyword("鈑金", False), Keyword("五金製品", False)))]
    c = classify.classify([extract.PageText("u", "專業板金加工與五金製品。")], inds, cfg)
    assert c.industry == "金屬" and set(c.hits) == {"鈑金", "五金製品"}
    assert "板金" in c.evidence[0][0]                     # 證據是原文（板金），不是換過的字


def test_component_signal_needs_two_words(cfg):
    assert channel.component_signal("整流二極體與功率電晶體", cfg)
    assert not channel.component_signal("本公司生產電源供應器，使用二極體", cfg)


def test_component_maker_routed_out(R, cfg):
    site = {"meta": {"公司名稱": "範例半導體股份有限公司", "統編": "12345678", "url": "https://x.example.tw/"},
            "pages": [extract.Page("https://x.example.tw/", PAGE)]}
    r = cli.process(site, R, cfg, dt.date(2026, 10, 20))
    assert r["分流"] == "通路與合作" and r["角色旗標"] == match.ROLE_COMPONENT
    assert r["官網確認"] == "統編"                         # 頁尾的統編還是用得到


def test_extra_keywords_loaded_and_marked(R):
    ind = next(i for i in R.industries if i.name == "金屬、模具與塑膠零件製造")
    extra = [k.text for k in ind.keywords if k.extra]
    assert "die casting" in extra and "sheet metal" in extra
    assert not any(k.extra for k in ind.keywords if k.text == "鈑金")    # 原表的字不會被標成補充
