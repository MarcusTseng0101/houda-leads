"""客戶不限國內（使用者 2026-10-20）：國外名單、英文公司名確認官網、依國家分給該區代理、外聯規則。"""
import csv
import datetime as dt

import pytest

from houda_leads import cli, extract, sources

PAGE = """<html><body><h1>Rhein Antriebstechnik GmbH</h1>
<p>We design and build low-voltage switchgear and motor control centres for factories.</p>
<p>Contact: <a href="mailto:sales@rhein-antrieb.example.de">sales@rhein-antrieb.example.de</a></p>
<footer>Rhein Antriebstechnik GmbH &amp; Co. KG · Amtsgericht HRB 12345</footer></body></html>"""


def site(name, country, url="https://www.rhein-antrieb.example.de/", html=PAGE, reg=""):
    return {"meta": {"公司名稱": name, "統編": reg, "國家": country, "url": url},
            "pages": [extract.Page(url, html)]}


@pytest.mark.parametrize("name,core", [
    ("Rhein Antriebstechnik GmbH & Co. KG", "Rhein Antriebstechnik"),
    ("Foo Electric Co., Ltd.", "Foo Electric"),
    ("Acme Controls, Inc.", "Acme Controls"),
    ("株式会社サンプル電機", "サンプル電機"),
    ("範例電機股份有限公司", "範例電機"),
    ("Taco Systems", "Taco Systems"),          # 結尾的 co 是字的一部分，不能砍
])
def test_company_core(name, core):
    assert extract.company_core(name) == core


def test_english_name_confirms_site_case_insensitive():
    t = [extract.PageText("u", extract.visible_text(PAGE))]
    assert extract.site_confirmation(t, "", "RHEIN ANTRIEBSTECHNIK GMBH", "註冊編號") == "公司名"
    assert extract.site_confirmation(t, "12345", "Other Name Ltd", "註冊編號") == "註冊編號"
    # 單字邊界：「Rhein Antrieb」不能用「Rhein Antriebstechnik」的一部分來確認
    assert extract.site_confirmation(t, "", "Rhein Antrieb AG", "註冊編號") == "未確認"


def test_germany_goes_to_eu_partner_and_needs_consent(R, cfg):
    r = cli.process(site("Rhein Antriebstechnik GmbH", "DE"), R, cfg, dt.date(2026, 10, 20))
    assert r["國家"] == "DE" and r["代理區域"] == "EU"
    assert r["外聯規則"] == "先取得同意"
    assert r["官網確認"] == "公司名"
    assert r["分流"] == "潛在客戶", r["備註"]
    assert r["建議跟進"].startswith("交給該區代理：") and "Comet" in r["建議跟進"]


def test_region_routing_for_each_partner_region(R, cfg):
    want = {"JP": "ABA", "CN": "逸特", "US": "BlockMaster"}
    for country, partner in want.items():
        assert partner in cli._region_partners(R, R.countries[country]["代理區域"]), country


def test_unknown_country_is_flagged(R, cfg):
    r = cli.process(site("Rhein Antriebstechnik GmbH", "ZZ"), R, cfg, dt.date(2026, 10, 20))
    assert r["代理區域"] == "" and r["外聯規則"].startswith("需確認")


def test_taiwan_rows_unchanged(R, cfg):
    html = "<html><body><p>範例電機股份有限公司 統一編號 12345678</p></body></html>"
    r = cli.process(site("範例電機股份有限公司", "", url="https://x.example.tw/", html=html, reg="12345678"),
                    R, cfg, dt.date(2026, 10, 20))
    assert r["國家"] == "TW" and r["官網確認"] == "統編"


def write(tmp_path, rows, hdr=("公司名稱", "國家", "官網", "註冊編號", "來源")):
    p = tmp_path / "list.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        w.writerows(rows)
    return p


def test_load_company_list(tmp_path):
    p = write(tmp_path, [
        ["Rhein Antriebstechnik GmbH", "de", "https://www.rhein-antrieb.example.de/", "HRB 12345", "electronica 2026"],
        ["Rhein Antriebstechnik GmbH", "DE", "https://dup.example.de/", "HRB 12345", ""],     # 重複：只留第一次
        ["Foo Electric Co., Ltd.", "JP", "", "", ""],
        ["", "US", "https://empty.example.com/", "", ""],                                      # 沒名字：略過
    ])
    cs = sources.load_company_list(p)
    assert [(c.name, c.country) for c in cs] == [("Rhein Antriebstechnik GmbH", "DE"), ("Foo Electric Co., Ltd.", "JP")]
    assert cs[0].source == "electronica 2026" and cs[1].website == ""


def test_load_company_list_rejects_bad_country_and_missing_columns(tmp_path):
    with pytest.raises(ValueError, match="兩碼國家代碼"):
        sources.load_company_list(write(tmp_path, [["X Ltd", "Germany", "https://x.example/", "", ""]]))
    with pytest.raises(ValueError, match="缺欄位"):
        sources.load_company_list(write(tmp_path, [["X Ltd", "https://x.example/"]], hdr=("公司名稱", "官網")))
