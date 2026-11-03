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
          "組織種別", "代表者名称", "事業概要", "WebサイトURL", "事業種目"]


def test_gbizinfo_never_keeps_representative(tmp_path, cfg):
    p = tmp_path / "jp.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(JP_HDR)
        w.writerows([
            ["1234567890123", "株式会社サンプル電機", "", "Sample Denki Co., Ltd.", "大阪府", "", "301", "代表取締役 山田太郎",
             "配電盤の製造", "https://www.sample-denki.example.jp/", "E:製造業|I:卸売業、小売業"],
            ["2234567890123", "閉鎖電機株式会社", "", "", "東京都", "閉鎖", "301", "", "", "https://x.example.jp/", "E:製造業"],
            ["3234567890123", "パン屋株式会社", "", "", "東京都", "", "301", "", "", "https://p.example.jp/", "M:宿泊業、飲食サービス業"],
            ["4234567890123", "官網なし工業株式会社", "", "", "東京都", "", "301", "", "", "", "E:製造業"],          # 沒官網：排除
            ["5234567890123", "一般社団法人 電機協会", "", "", "東京都", "", "399", "", "", "https://a.example.jp/", "E:製造業"],  # 型態不符
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



# ── 台灣：出進口廠商登記的英文名猜網域（2026-11-02，使用者：不再請業務標，先讓工具自己找官網）──

TRADE_HDR = ["統一編號", "原始登記日期", "核發日期", "廠商中文名稱", "廠商英文名稱", "中文營業地址", "英文營業地址",
             "代表人", "電話號碼", "傳真號碼", "進口資格", "出口資格"]


def test_trade_registry_reads_name_and_phone_never_representative(tmp_path):
    p = tmp_path / "trade.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(TRADE_HDR)
        w.writerow(["12345678", "", "", "範例機械股份有限公司", "CHI HUA MACHINERY CO., LTD.", "", "", "王O明",
                    "04-8681868#8603", "", "有", "有"])
        w.writerow(["87654321", "", "", "別家", "OTHER CO., LTD.", "", "", "", "", "", "有", "有"])
    t = sources.load_trade_registry(p, {"12345678"})
    assert list(t) == ["12345678"]
    assert t["12345678"] == sources.TradeInfo("CHI HUA MACHINERY CO., LTD.", "48681868")
    assert "王" not in repr(t)


def test_phone_digits():
    assert sources.phone_digits("(02)2397-7505") == "223977505"
    assert sources.phone_digits("+886-2-2397-7505") == "223977505"
    assert sources.phone_digits("04-8681868#8603") == "48681868"
    assert sources.phone_digits("123") == ""


def test_tw_brand_slug_first(cfg):
    drop = cfg["discover"]["drop_words"]["TW"]
    assert discover.slugs("CHI HUA MACHINERY CO., LTD.", drop)[:2] == ["chihua", "chi-hua"]
    assert discover.slugs("ACME CONTROLS LIMITED") == ["acmecontrols", "acme-controls"]     # 英國不變
    urls = discover.candidate_urls("CHI HUA MACHINERY CO., LTD.", "TW", cfg)
    assert urls[0] == "https://www.chihua.com.tw/" and "https://www.chi-hua.com.tw/" in urls
    assert len(urls) <= cfg["discover"]["max_candidates_by_country"]["TW"]


def test_tw_name_plus_registered_phone_confirms(cfg):
    home = '<html><body><p>祺華機械股份有限公司</p><a href="/about">關於我們</a></body></html>'
    about = "<html><body><p>祺華機械股份有限公司 TEL: +886-4-868-1868</p></body></html>"
    f = FakeFetcher({"https://www.chihua.com.tw/": home, "https://www.chihua.com.tw/about": about})
    url, _, log = discover.discover(f, ["CHI HUA MACHINERY CO., LTD.", "祺華機械股份有限公司"], "12345678", "TW", cfg,
                                    ["tel:48681868"])
    assert url == "https://www.chihua.com.tw/" and "電話" in log[-1]


def test_tw_name_without_matching_phone_is_rejected(cfg):
    home = "<html><body><p>祺華機械股份有限公司 TEL: 02-1234-5678</p></body></html>"
    f = FakeFetcher({"https://www.chihua.com.tw/": home})
    url, _, _ = discover.discover(f, ["CHI HUA MACHINERY CO., LTD.", "祺華機械股份有限公司"], "12345678", "TW", cfg,
                                  ["tel:48681868"])
    assert url == ""


def test_tw_http_fallback_but_not_after_robots(cfg):
    page = "<html><body><p>統一編號 12345678</p></body></html>"
    f = FakeFetcher({"http://www.chihua.com.tw/": page})
    url, _, _ = discover.discover(f, ["CHI HUA MACHINERY CO., LTD."], "12345678", "TW", cfg)
    assert url == "http://www.chihua.com.tw/"
    f = FakeFetcher({"https://www.chihua.com.tw/": RobotsDisallowed("x"), "http://www.chihua.com.tw/": page})
    url, _, _ = discover.discover(f, ["CHI HUA MACHINERY CO., LTD."], "12345678", "TW", cfg)
    assert "http://www.chihua.com.tw/" not in f.asked                                  # robots 禁止：不換 http 硬抓
    f = FakeFetcher({"http://www.acmecontrols.co.uk/": page})
    assert discover.discover(f, ["ACME CONTROLS LIMITED"], "12345678", "GB", cfg)[0] == ""   # 英國不退回 http



def test_tw_http_fallback_after_unreachable(cfg):
    from houda_leads.fetch import SiteUnreachable
    page = "<html><body><p>統一編號 12345678</p></body></html>"
    f = FakeFetcher({"https://www.chihua.com.tw/": SiteUnreachable("x"), "http://www.chihua.com.tw/": page})
    assert discover.discover(f, ["CHI HUA MACHINERY CO., LTD."], "12345678", "TW", cfg)[0] == "http://www.chihua.com.tw/"


def test_name_and_phone_both_on_about_page(cfg):
    """一中工業：首頁只有產品，公司名和電話都在「關於我們」。要的證據一樣，只是多看幾頁。"""
    home = '<html><body><p>Autoclaves and hot air ovens.</p><a href="/about-us">About us</a></body></html>'
    about = "<html><body><p>E-Chung Machinery Co., Ltd. Tel: 04-2358-1234</p></body></html>"
    f = FakeFetcher({"https://www.echung.com.tw/": home, "https://www.echung.com.tw/about-us": about})
    url, _, log = discover.discover(f, ["E-CHUNG MACHINERY COMPANY", "一中工業股份有限公司"], "12345678", "TW", cfg,
                                    ["tel:423581234"])
    assert url == "https://www.echung.com.tw/" and "電話" in log[-1]


def test_parked_domain_with_contact_link_still_rejected(cfg):
    home = '<html><body><p>This domain is for sale.</p><a href="/contact">Contact</a></body></html>'
    contact = "<html><body><p>Call 04-2358-1234 to buy this domain.</p></body></html>"   # 電話對得上，但沒有公司名
    f = FakeFetcher({"https://www.echung.com.tw/": home, "https://www.echung.com.tw/contact": contact})
    url, _, _ = discover.discover(f, ["E-CHUNG MACHINERY COMPANY", "一中工業股份有限公司"], "12345678", "TW", cfg,
                                  ["tel:423581234"])
    assert url == ""



# ── 搜尋找官網（自架 searchcrawl，2026-11-03 使用者同意）──

class SearchFetcher(FakeFetcher):
    def __init__(self, pages, results):
        super().__init__(pages)
        self.results = results

    def search(self, query, limit):
        self.query = query
        return self.results


def test_search_roots_use_site_home_and_skip_directories(cfg):
    skip = cfg["search"]["skip_domains"]
    res = ["https://www.twincn.com/item.aspx?no=22243425", "https://teamtaiwan.info/22243425",
           "http://www.ginzheng.com/products/a.html", "http://www.ginzheng.com/about", "https://www.other.com.tw/x"]
    assert discover.search_roots(res, skip, limit=3) == ["http://www.ginzheng.com/", "https://www.other.com.tw/"]
    assert discover.search_roots(res, skip, tried=["https://www.ginzheng.com/"]) == ["https://www.other.com.tw/"]


def test_search_result_confirmed_on_home(cfg):
    home = "<html><body><p>勤正電子股份有限公司 統一編號：22243425</p></body></html>"
    f = SearchFetcher({"http://www.ginzheng.com/": home},
                      ["https://www.twincn.com/item.aspx?no=22243425", "http://www.ginzheng.com/index.html"])
    url, pages, log = discover.discover_by_search(f, "勤正電子股份有限公司 22243425", ["勤正電子股份有限公司"],
                                                  "22243425", cfg)
    assert url == "http://www.ginzheng.com/" and pages and "twincn" not in "".join(f.asked)


def test_unknown_directory_is_rejected_because_its_home_lacks_the_company(cfg):
    """沒列在黑名單的名錄網站：結果那一頁有統編，但回首頁確認就沒有 → 不採用。"""
    f = SearchFetcher({"https://new-directory.example/": "<html><body><p>全台公司資料查詢</p></body></html>",
                       "https://new-directory.example/22243425": "<p>勤正電子股份有限公司 22243425</p>"},
                      ["https://new-directory.example/22243425"])
    url, _, log = discover.discover_by_search(f, "q", ["勤正電子股份有限公司"], "22243425", cfg)
    assert url == "" and "https://new-directory.example/22243425" not in f.asked


def test_search_failure_is_logged_not_raised(cfg):
    class Broken(FakeFetcher):
        def search(self, q, n):
            raise ConnectionError("searchcrawl 沒開")
    url, _, log = discover.discover_by_search(Broken({}), "q", ["X"], "1", cfg)
    assert url == "" and "搜尋失敗" in log[0]



def test_address_key():
    assert discover.address_key("臺南市永康區塩洲里塩忠街31號") == "塩忠街31號"
    assert discover.address_key("新北市五股區五權路１２號３樓") == "五權路12號"
    assert discover.address_key("桃園市龜山區文化里文化一路") == ""          # 沒有門牌：不用


def test_name_plus_factory_address_confirms_when_phone_changed(cfg):
    """模得發：登記電話換過了，網站上的地址還對得上（2026-11-03）。"""
    home = "<html><body><p>模得發工業股份有限公司 地址：台中市大雅區雅潭路四段100號 TEL: 04-2515-9799</p></body></html>"
    f = FakeFetcher({"https://www.mouldfly.com/": home})
    proof = ["tel:425293626", "addr:" + discover.address_key("臺中市大雅區雅潭路四段100號")]
    url, _, log = discover.discover_by_search(SearchFetcher(f.pages, ["https://www.mouldfly.com/"]), "q",
                                              ["MOULD FLYING INDUSTRIAL CO., LTD.", "模得發工業股份有限公司"], "12345678",
                                              cfg, proof)
    assert url == "https://www.mouldfly.com/" and "地址" in log[-1]
