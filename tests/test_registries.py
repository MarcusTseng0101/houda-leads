"""國外政府登記（Companies House、gBizINFO）和猜網域找官網。合成資料，不連網。"""
import csv
import io
import zipfile

from houda_leads import discover, extract, sources
from houda_leads.fetch import RobotsDisallowed

GB_HDR = ["CompanyName", " CompanyNumber", "RegAddress.PostTown", "RegAddress.PostCode", "CompanyCategory",
          "CompanyStatus", "Accounts.AccountCategory",
          "SICCode.SicText_1", "SICCode.SicText_2", "SICCode.SicText_3", "SICCode.SicText_4"]


def gb_row(name, num, sic="27120 - Manufacture of electricity distribution and control apparatus",
           status="Active", cat="Private Limited Company", acc="SMALL"):
    return [name, num, "LEEDS", "LS1 1AA", cat, status, acc, sic, "", "", ""]


def test_companies_house_from_zip(tmp_path, cfg):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(GB_HDR)
    w.writerows([
        gb_row("ACME CONTROLS LIMITED", "01234567"),
        gb_row("DORMANT SWITCHGEAR LTD", "02222222", acc="DORMANT"),             # 休眠：排除
        gb_row("BAKERY LTD", "03333333", sic="10710 - Manufacture of bread"),     # 產業不符：排除
        gb_row("GONE MACHINES LTD", "04444444", status="Liquidation"),            # 不是 Active：排除
        gb_row("SOME LLP", "OC555555", cat="Limited Liability Partnership"),     # 型態不符：排除
    ])
    p = tmp_path / "ch.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("BasicCompanyData.csv", buf.getvalue().encode("utf-8-sig"))
    cs = sources.load_companies_house(p, cfg)
    assert [(c.tax_id, c.name, c.country) for c in cs] == [("01234567", "ACME CONTROLS LIMITED", "GB")]
    assert cs[0].industry_codes == ["27"] and cs[0].website == ""


JP_HDR = ["法人番号", "商号または名称", "商号または名称（カナ）", "商号または名称（英字）", "都道府県", "状態",
          "代表者名称", "事業概要", "WebサイトURL", "事業種目"]


def test_gbizinfo_never_keeps_representative(tmp_path, cfg):
    p = tmp_path / "jp.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(JP_HDR)
        w.writerows([
            ["1234567890123", "株式会社サンプル電機", "", "Sample Denki Co., Ltd.", "大阪府", "", "代表取締役 山田太郎",
             "配電盤の製造", "https://www.sample-denki.example.jp/", "電気機械器具製造業|卸売業"],
            ["2234567890123", "閉鎖電機株式会社", "", "", "東京都", "閉鎖", "", "", "", "電気機械器具製造業"],
            ["3234567890123", "パン屋株式会社", "", "", "東京都", "", "", "", "", "食料品製造業"],
        ])
    cs = sources.load_gbizinfo(p, cfg)
    assert [c.name for c in cs] == ["株式会社サンプル電機"]
    c = cs[0]
    assert c.aliases == ["Sample Denki Co., Ltd."] and c.website.startswith("https://www.sample-denki")
    assert "山田" not in repr(c)


def test_slugs_and_candidates(cfg):
    assert discover.slugs("ACME CONTROLS LIMITED") == ["acmecontrols", "acme-controls"]
    assert discover.slugs("Smith & Jones Electrical Ltd")[:2] == ["smithandjoneselectrical", "smith-and-jones-electrical"]
    assert discover.slugs("株式会社サンプル電機") == []
    urls = discover.candidate_urls("ACME CONTROLS LIMITED", "GB", cfg)
    assert urls[0] == "https://www.acmecontrols.co.uk/" and len(urls) <= cfg["discover"]["max_candidates"]


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url):
        self.asked.append(url)
        if url not in self.pages:
            raise ConnectionError(url)
        v = self.pages[url]
        if isinstance(v, Exception):
            raise v
        return v


def test_discover_needs_confirmation(cfg):
    parked = "<html><body><p>acmecontrols.co.uk is for sale!</p></body></html>"
    real = ("<html><body><p>Acme Controls: panel builders.</p>"
            "<footer>Registered in England No. 1234567</footer></body></html>")
    f = FakeFetcher({"https://www.acmecontrols.co.uk/": parked,
                     "https://www.acmecontrols.com/": real})
    url, pages, log = discover.discover(f, ["ACME CONTROLS LIMITED"], "01234567", "GB", cfg)
    assert url == "https://www.acmecontrols.com/"            # 停車頁（只有網域名）不採用
    assert any("不採用" in x for x in log) and "註冊編號" in log[-1]


def test_discover_gives_up_without_confirmation(cfg):
    f = FakeFetcher({"https://www.acmecontrols.co.uk/": RobotsDisallowed("x"),
                     "https://www.acmecontrols.com/": "<html><body>Welcome</body></html>"})
    url, pages, log = discover.discover(f, ["ACME CONTROLS LIMITED"], "01234567", "GB", cfg)
    assert url == "" and pages == []


def test_registration_number_without_leading_zero():
    t = [extract.PageText("u", "Company No. 1234567, registered in England")]
    assert extract.site_confirmation(t, "01234567", "X Ltd", "註冊編號") == "註冊編號"
    t = [extract.PageText("u", "Order 912345678")]                   # 前後還有數字：不算
    assert extract.site_confirmation(t, "01234567", "X Ltd", "註冊編號") == "未確認"


def test_name_alone_is_not_enough_for_guessed_domain(cfg):
    """英國試跑：FINSPA LIMITED 猜到 finspa.com（烏克蘭浴缸店），只因為頁面有 FinSpa 就被採用。"""
    other = "<html><body><p>FinSpa: wood-fired hot tubs, delivery across Ukraine.</p></body></html>"
    ours = "<html><body><p>Finspa Ltd, Unit 4, Leeds LS1 1AA. General engineering.</p></body></html>"
    f = FakeFetcher({"https://www.finspa.com/": other})
    assert discover.discover(f, ["FINSPA LIMITED"], "07654321", "GB", cfg, ["LS1 1AA"])[0] == ""
    f = FakeFetcher({"https://www.finspa.co.uk/": ours})
    url, _, log = discover.discover(f, ["FINSPA LIMITED"], "07654321", "GB", cfg, ["ls11aa"])
    assert url == "https://www.finspa.co.uk/" and "郵遞區號" not in log[-1] and "LS11AA".lower() in log[-1].lower()


def test_second_proof_on_contact_page(cfg):
    home = '<html><body><p>Acme Controls</p><a href="/contact-us">Contact</a></body></html>'
    contact = "<html><body><p>Acme Controls Ltd, Leeds LS1 1AA</p></body></html>"
    f = FakeFetcher({"https://www.acmecontrols.co.uk/": home, "https://www.acmecontrols.co.uk/contact-us": contact})
    url, _, log = discover.discover(f, ["ACME CONTROLS LIMITED"], "01234567", "GB", cfg, ["LS1 1AA"])
    assert url == "https://www.acmecontrols.co.uk/" and "LS1 1AA" in log[-1]
