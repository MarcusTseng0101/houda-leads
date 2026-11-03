"""找官網：沒有搜尋服務（不用 API key），所以從公司名猜網域，再用官網確認擋掉猜錯的。

失敗模式 4：猜到的網域只有在頁面上看得到註冊編號，或「公司名＋登記地址的郵遞區號」才採用；
確認不了就當沒有官網，不准「看起來像」就用。只看公司名不夠：網域是用公司名猜的，同名的別家公司
頁面上當然也有這個名字（2026-10-20 英國試跑：FINSPA LIMITED 猜到 finspa.com，是烏克蘭的浴缸店）。英國公司依 Trading Disclosures Regulations 2015 網站要寫註冊編號，所以英國最好確認。
所有請求都經過 fetch.Fetcher（robots、速率、禁止網域）。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from . import extract

AND = re.compile(r"\s*&\s*|\s+and\s+", re.I)


def slugs(name: str, drop_words: list[str] = ()) -> list[str]:
    """公司名 → 網域名稱候選（不含 TLD）。只處理英數字的名字；中日文名猜不出網域。
    drop_words：去掉結尾的通用字，只留品牌字，排在前面（台灣：CHI HUA MACHINERY → chi-hua.com.tw）。"""
    core = extract.company_core(name)
    out = []
    drop = {w.lower() for w in drop_words}
    for variant in (AND.sub(" and ", core), AND.sub(" ", core)):
        words = re.findall(r"[a-z0-9]+", variant.lower())
        if not words or not re.fullmatch(r"[\x20-\x7e]+", core):
            continue
        brand = list(words)
        while drop and len(brand) > 1 and brand[-1] in drop:
            brand.pop()
        forms = ([brand] if brand != words else []) + [words]
        for ws in forms:
            for s in ("".join(ws), "-".join(ws)):
                if len(s) >= 3 and s not in out:
                    out.append(s)
    return out


def candidate_urls(name: str, country: str, cfg: dict) -> list[str]:
    d = cfg["discover"]
    tlds = d["tlds"].get(country, d["tlds"]["default"])
    drop = d.get("drop_words", {}).get(country, [])
    urls = [f"https://www.{s}{t}/" for s in slugs(name, drop) for t in tlds]
    return urls[: int(d.get("max_candidates_by_country", {}).get(country, d["max_candidates"]))]


# 註冊編號、地址通常寫在這些頁（英國公司法規定要揭露，常放在頁尾、聯絡、隱私、條款頁）
PROOF_LINK_WORDS = ("contact", "about", "privacy", "terms", "legal", "imprint", "impressum", "会社概要", "お問い合わせ",
                    "聯絡", "聯繫", "關於", "公司簡介", "mentions", "légales", "legales")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def address_key(address: str) -> str:
    """工廠地址 → 「路名＋門牌」，比對官網用。臺／台、全形半形、空白不分；縣市、區、里、鄰都拿掉（官網常省略里）。
    「臺南市永康區塩洲里塩忠街31號」→「塩忠街31號」。太短（< 5 字）或沒有「號」就不用（2026-11-03：登記電話常換）。"""
    s = _norm(address.translate(extract.HALFWIDTH)).replace("臺", "台")
    s = re.sub(r"^\d{3,6}", "", s)                       # 郵遞區號
    m = re.search(r"([^區鄉鎮市里村鄰]+?[路街道巷弄大道].*?\d+(?:之\d+)?號)", s)
    if not m:
        return ""
    k = re.sub(r"^\d+鄰", "", m.group(1))
    return k if len(k) >= 5 else ""


def _second_proof(fetcher, url: str, html: str, reg_id: str, proof, max_pages: int, names=()) -> str:
    """首頁＋同網站的聯絡／關於／隱私頁當一組：裡面看得到註冊編號，或公司名＋第二個證據（郵遞區號、登記電話）才算。
    names：首頁沒看到公司名時，公司名也可以在這幾頁裡（台灣試跑：一中工業首頁只有產品，公司名在「關於我們」）。"""
    root = extract.site_root(url)
    pages = [(url, html)]
    for href, text in extract.links(html, url):
        if len(pages) > max_pages:
            break
        low = (href + " " + text).lower()
        if extract.site_root(href) == root and any(w in low for w in PROOF_LINK_WORDS)                 and href not in [u for u, _ in pages]:
            try:
                pages.append((href, fetcher.get(href)))
            except Exception:
                continue
    texts = [extract.PageText(u, extract.visible_text(h)) for u, h in pages]
    for t in texts:
        if extract.site_confirmation([t], reg_id, "", "註冊編號") == "註冊編號":
            return "公司名＋註冊編號" if not names else "註冊編號"
    if names and not any(extract.site_confirmation(texts, "", n, "註冊編號") == "公司名" for n in names):
        return "未確認"
    for t in texts:
        digits = re.sub(r"\D", "", t.text)
        # 「tel:」開頭的是登記的電話（去掉區碼的 0）：只比數字，(04)868-1868、+886-4-8681868 都算
        plain = _norm(t.text.translate(extract.HALFWIDTH)).replace("臺", "台")
        hit = [x for x in proof if x and (x[4:] in digits if x.startswith("tel:")
                                          else x[5:] in plain if x.startswith("addr:")
                                          else _norm(x) in _norm(t.text))]
        if hit:
            kind = {"tel:": "電話", "addr:": "地址"}
            return "公司名＋" + next((v for k, v in kind.items() if hit[0].startswith(k)), hit[0])
    return "未確認"


def discover(fetcher, names: list[str], reg_id: str, country: str, cfg: dict,
             proof: list[str] = ()) -> tuple[str, list[extract.Page], list[str]]:
    """回傳 (確認過的官網, 首頁, 紀錄)。沒確認到就回傳 ("", [], 紀錄)。
    proof：只靠公司名確認時，頁面上還要看到其中一個（例如郵遞區號），不分大小寫和空白。"""
    log = []
    tried = []
    for n in names:
        tried += [u for u in candidate_urls(n, country, cfg) if u not in tried]
    if not tried:
        return "", [], ["公司名不是英數字，猜不出網域"]
    fallback = country in cfg["discover"].get("http_fallback", [])
    for url in tried:
        try:
            html = fetcher.get(url)
        except Exception as e:          # 網域不存在、robots 禁止、逾時：換下一個
            log.append(f"猜 {url}：{type(e).__name__}")
            # 台灣很多小公司官網只有 http（沒有憑證）；robots 禁止、禁止網域就不再試
            if not fallback or type(e).__name__ in ("RobotsDisallowed", "ForbiddenDomain"):   # SiteUnreachable 可以換 http
                continue
            url = "http://" + url[len("https://"):]
            try:
                html = fetcher.get(url)
            except Exception as e2:
                log.append(f"猜 {url}：{type(e2).__name__}")
                continue
        how = _confirm(fetcher, url, html, names, reg_id, proof, cfg)
        if how != "未確認":
            log.append(f"猜 {url}：首頁{how}確認")
            return url, [extract.Page(url, html)], log
        log.append(f"猜 {url}：連得上但首頁看不到註冊編號（或公司名＋郵遞區號），不採用")
    return "", [], log


def _confirm(fetcher, url: str, html: str, names: list[str], reg_id: str, proof, cfg: dict) -> str:
    """一個網站是不是這家公司：首頁看得到註冊編號；或首頁＋聯絡／關於頁裡有公司名＋第二個證據。"""
    texts = [extract.PageText(url, extract.visible_text(html))]
    how = "未確認"
    for n in names:
        how = extract.site_confirmation(texts, reg_id, n, "註冊編號")
        if how != "未確認":
            break
    if how == "公司名":
        how = _second_proof(fetcher, url, html, reg_id, proof, int(cfg["discover"]["proof_pages"]))
    elif how == "未確認":           # 首頁什麼都沒有：公司名和證據可能都在聯絡／關於頁（要求一樣，只是多看幾頁）
        how = _second_proof(fetcher, url, html, reg_id, proof, int(cfg["discover"]["proof_pages"]), names)
    return how


def search_roots(result_urls: list[str], skip_domains: list[str], tried: list[str] = (), limit: int = 3) -> list[str]:
    """搜尋結果 → 要去確認的網站首頁。只取網域，不用結果那一頁：
    名錄網站（台灣公司網、104…）那一頁上也有統編和電話，但它的首頁沒有，回首頁確認就會被擋掉。
    skip_domains 是已知的名錄、社群、電商平台，直接跳過。"""
    seen = {extract.site_root(u) for u in tried}
    out = []
    for u in result_urls:
        root = extract.site_root(u)
        if not root or root in seen or any(root == d or root.endswith("." + d) for d in skip_domains):
            continue
        seen.add(root)
        parts = urlsplit(u)
        out.append(f"{parts.scheme}://{parts.netloc}/")
        if len(out) >= limit:
            break
    return out


def discover_by_search(fetcher, query: str, names: list[str], reg_id: str, cfg: dict, proof: list[str] = (),
                       tried: list[str] = (), country: str = "TW") -> tuple[str, list[extract.Page], list[str]]:
    """猜網域找不到：用自架的 searchcrawl 搜尋（不用 API key），結果的網站一樣要確認才採用（失敗模式 4）。"""
    s = cfg["search"]
    try:
        results = fetcher.search(query, int(s["results"]), country) if country != "TW" \
            else fetcher.search(query, int(s["results"]))
    except Exception as e:
        return "", [], [f"搜尋失敗：{type(e).__name__}"]
    log = []
    for url in search_roots(results, s["skip_domains"], tried, int(s["max_sites"])):
        try:
            html = fetcher.get(url)
        except Exception as e:
            log.append(f"搜尋 {url}：{type(e).__name__}")
            continue
        how = _confirm(fetcher, url, html, names, reg_id, proof, cfg)
        if how != "未確認":
            log.append(f"搜尋 {url}：首頁{how}確認")
            return url, [extract.Page(url, html)], log
        log.append(f"搜尋 {url}：看不到統編（或公司名＋電話），不採用")
    if not log:
        log.append("搜尋結果裡沒有可以確認的網站")
    return "", [], log
