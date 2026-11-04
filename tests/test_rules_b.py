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


def test_english_plural_matches_but_boundary_kept(cfg):
    inds = [Industry("配電", (Keyword("control panel", True),)), Industry("EMS", (Keyword("ems", True),))]
    c = classify.classify([extract.PageText("u", "We build bespoke control panels and switchboards.")], inds, cfg)
    assert c.industry == "配電"
    c = classify.classify([extract.PageText("u", "Integrated systems for factories.")], inds, cfg)
    assert c.industry == ""                    # "systems" 還是不能中 "ems"


def test_registry_hint_breaks_tie_only_with_enough_hits(cfg):
    inds = [Industry("電源", (Keyword("ups", True), Keyword("battery", True))),
            Industry("配電", (Keyword("switchgear", True), Keyword("control panel", True)))]
    t = [extract.PageText("u", "We make UPS systems, battery packs, switchgear and control panels.")]
    assert classify.classify(t, inds, cfg).industry == ""                       # 2:2 分不出來
    c = classify.classify(t, inds, cfg, ["配電"])
    assert c.industry == "配電" and c.method == "關鍵字＋登記產業"
    t = [extract.PageText("u", "UPS, battery, inverter, charger, rectifier. Also switchgear.")]
    inds[0] = Industry("電源", tuple(Keyword(k, True) for k in ("ups", "battery", "inverter", "charger", "rectifier")))
    inds[1] = Industry("配電", (Keyword("switchgear", True),))
    c = classify.classify(t, inds, cfg, ["配電"])
    assert c.industry != "配電"                     # 1 比 5：登記代碼不能蓋過網站上的證據


def test_hinted_industries_from_codes(R):
    assert R.hinted_industries("GB", ["27120"]) == ["配電盤與電機控制"]
    assert set(R.hinted_industries("TW", ["28", "281"])) == {"配電盤與電機控制", "馬達、變壓器與電力設備"}
    assert R.hinted_industries("GB", ["10710"]) == []


def test_registry_hint_needs_two_hits_and_no_hinted_tie(cfg):
    inds = [Industry("電源", (Keyword("battery", True),)), Industry("照明", (Keyword("led", True),)),
            Industry("機械", (Keyword("plc", True),))]
    t = [extract.PageText("u", "Battery powered LED, made by Hook plc.")]       # 1:1:1（HOOKPOD 的情況）
    assert classify.classify(t, inds, cfg, ["機械"]).industry == ""
    inds = [Industry("A", (Keyword("ups", True), Keyword("battery", True))),
            Industry("B", (Keyword("switchgear", True), Keyword("busbar", True)))]
    t = [extract.PageText("u", "UPS and battery systems, switchgear and busbar.")]
    assert classify.classify(t, inds, cfg, ["A", "B"]).industry == ""           # 兩個都有提示又平手


def test_exhibition_names_are_only_mentions(cfg):
    inds = [Industry("自動化", (Keyword("industrial automation", True), Keyword("automation", True)))]
    t = [extract.PageText("u", "Meet Tex Year at Taipei International Industrial Automation Exhibition. 2019 參展 自動化")]
    c = classify.classify(t, inds, cfg)
    assert c.industry == "" and "自動化" in c.mentions



def test_keywords_only_match_their_own_language(cfg):
    inds = [Industry("保全", (Keyword("保全", False, False, "zh"),)),
            Industry("空調", (Keyword("空調機器", False, True, "ja"), Keyword("hvac", True)))]
    t = [extract.PageText("u", "環境保全に取り組みます。空調機器とHVACを製造しています。")]
    c = classify.classify(t, inds, cfg, lang="ja")
    assert c.industry == "空調" and "保全" not in c.all_hits          # 中文「保全」不比日文網站
    c = classify.classify([extract.PageText("u", "專業保全系統。")], inds, cfg, lang="zh")
    assert c.industry == "保全"


def test_japanese_for_and_introduced_are_mentions(cfg):
    inds = [Industry("半導體", (Keyword("半導体製造装置", False, True, "ja"),)),
            Industry("測試", (Keyword("測定器", False, True, "ja"),))]
    t = [extract.PageText("u", "半導体製造装置向けの部品を製造。精密測定器を導入しております。")]
    c = classify.classify(t, inds, cfg, lang="ja")
    assert c.industry == "" and set(c.mentions) == {"半導體", "測試"}


def test_single_keyword_hit_is_not_a_lead(R, cfg):
    html = "<html><body><p>範例電機股份有限公司 統一編號 12345678</p><p>本公司製造電源供應器。</p></body></html>"
    one = {"meta": {"公司名稱": "範例電機股份有限公司", "統編": "12345678", "url": "https://x.example.tw/"},
           "pages": [extract.Page("https://x.example.tw/", html)]}
    r = cli.process(one, R, cfg, dt.date(2026, 10, 26))
    assert r["標準化產業"] and r["命中關鍵字"] == "電源供應器"
    assert r["分流"] == "需人工確認" and "證據太少" in r["備註"]


def test_specific_hinted_industry_beats_generic_machinery(cfg):
    """登記 292 同時提示「機械」和「半導體」：網站說得出是半導體設備，就不歸泛用的機械（2026-11-02）。"""
    cfg = {**cfg, "classify": {**cfg["classify"], "generic_industries": ["機械"]}}
    inds = [Industry("機械", tuple(Keyword(k, True) for k in ("automation", "machinery", "robot arm", "conveyor", "plc"))),
            Industry("半導體", tuple(Keyword(k, True) for k in ("semiconductor", "wafer", "pcb", "smt")))]
    t = [extract.PageText("u", "Automation machinery, robot arm, conveyor and PLC for semiconductor wafer, PCB and SMT lines.")]
    assert classify.classify(t, inds, cfg, ["機械", "半導體"]).industry == "半導體"     # 5:4，原本會選機械
    assert classify.classify(t, inds, cfg, ["機械"]).industry == "機械"                # 半導體沒提示：照舊
    t = [extract.PageText("u", "Automation machinery, robot arm, conveyor and PLC. Also semiconductor.")]
    c = classify.classify(t, inds, cfg, ["機械", "半導體"])
    assert c.industry == "機械" and c.method == "關鍵字(多數)"                         # 5:1 機械明顯多：照舊


def test_registry_292_hints_semiconductor_equipment(R):
    assert "半導體、PCB與電子製造設備" in R.hinted_industries("TW", ["292"])



def test_subcontract_signal_needs_two_words(cfg):
    from houda_leads import channel
    assert channel.subcontract_signal("提供 CNC 加工與精密加工代工服務", cfg) == ["代工", "精密加工", "CNC 加工"]
    assert channel.subcontract_signal("歡迎 OEM／ODM 合作，專業 CNC 車床製造", cfg) == []     # OEM/ODM 不算


def test_own_equipment_is_only_a_mention_in_chinese(cfg):
    inds = [Industry("機械", (Keyword("cnc", True), Keyword("機械手臂", False)))]
    t = [extract.PageText("u", "本廠引進 CNC 與機械手臂。")]
    c = classify.classify(t, inds, cfg, lang="zh")
    assert c.industry == "" and c.mentions.get("機械")



def test_keyword_ignored_for_one_language(cfg):
    inds = [Industry("金屬", (Keyword("fabrication", True), Keyword("sheet metal", True)))]
    t = [extract.PageText("u", "Fabrication de machines. Sheet metal fabrication.")]
    assert classify.classify(t, inds, cfg, lang="en").hits == ["fabrication", "sheet metal"]
    assert classify.classify(t, inds, cfg, lang="fr").hits == ["sheet metal"]



# ── 2026-11-04 全球試跑抽查後的修正 ──

def test_japanese_for_use_suffix_is_a_mention(cfg):
    inds = [Industry("機械", (Keyword("工作機械", False, True, "ja"), Keyword("産業機械", False, True, "ja")))]
    t = [extract.PageText("u", "工作機械や産業機械用の鋳物を製造しています。")]
    c = classify.classify(t, inds, cfg, lang="ja")
    assert c.industry == "" and c.mentions.get("機械")
    t = [extract.PageText("u", "産業機械の部品を加工しています。")]
    assert classify.classify(t, inds, cfg, lang="ja").industry == ""


def test_japanese_use_words_are_not_the_for_suffix(cfg):
    inds = [Industry("機械", (Keyword("工作機械", False, True, "ja"), Keyword("専用機", False, True, "ja")))]
    t = [extract.PageText("u", "当社は工作機械を使用した専用機を設計・製造しています。")]
    c = classify.classify(t, inds, cfg, lang="ja")
    assert c.industry == "機械" and set(c.hits) == {"工作機械", "専用機"}


def test_installer_needs_no_maker_words(cfg):
    assert channel.service_signal("Installation, entretien et dépannage de climatisation.", cfg) != []
    assert channel.service_signal("Fabricant de pompes à chaleur : installation et entretien.", cfg) == []
    assert channel.service_signal("We manufacture chillers. Installation and maintenance.", cfg) == []
