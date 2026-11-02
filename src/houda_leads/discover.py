"""找官網：沒有搜尋服務（不用 API key），所以從公司名猜網域，再用官網確認擋掉猜錯的。

失敗模式 4：猜到的網域只有在頁面上看得到註冊編號，或「公司名＋登記地址的郵遞區號」才採用；
確認不了就當沒有官網，不准「看起來像」就用。只看公司名不夠：網域是用公司名猜的，同名的別家公司
頁面上當然也有這個名字（2026-10-20 英國試跑：FINSPA LIMITED 猜到 finspa.com，是烏克蘭的浴缸店）。英國公司依 Trading Disclosures Regulations 2015 網站要寫註冊編號，所以英國最好確認。
所有請求都經過 fetch.Fetcher（robots、速率、禁止網域）。
"""
from __future__ import annotations

import re

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
                    "聯絡", "聯繫", "關於", "公司簡介")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


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
        hit = [x for x in proof if x and (x[4:] in digits if x.startswith("tel:") else _norm(x) in _norm(t.text))]
        if hit:
            return "公司名＋電話" if hit[0].startswith("tel:") else f"公司名＋{hit[0]}"
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
        page = extract.Page(url, html)
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
        if how != "未確認":
            log.append(f"猜 {url}：首頁{how}確認")
            return url, [page], log
        log.append(f"猜 {url}：連得上但首頁看不到註冊編號（或公司名＋郵遞區號），不採用")
    return "", [], log
