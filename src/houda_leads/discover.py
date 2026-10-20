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


def slugs(name: str) -> list[str]:
    """公司名 → 網域名稱候選（不含 TLD）。只處理英數字的名字；中日文名猜不出網域。"""
    core = extract.company_core(name)
    out = []
    for variant in (AND.sub(" and ", core), AND.sub(" ", core)):
        words = re.findall(r"[a-z0-9]+", variant.lower())
        if not words or not re.fullmatch(r"[\x20-\x7e]+", core):
            continue
        for s in ("".join(words), "-".join(words)):
            if len(s) >= 3 and s not in out:
                out.append(s)
    return out


def candidate_urls(name: str, country: str, cfg: dict) -> list[str]:
    d = cfg["discover"]
    tlds = d["tlds"].get(country, d["tlds"]["default"])
    urls = [f"https://www.{s}{t}/" for s in slugs(name) for t in tlds]
    return urls[: int(d["max_candidates"])]


# 註冊編號、地址通常寫在這些頁（英國公司法規定要揭露，常放在頁尾、聯絡、隱私、條款頁）
PROOF_LINK_WORDS = ("contact", "about", "privacy", "terms", "legal", "imprint", "impressum", "会社概要", "お問い合わせ")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _second_proof(fetcher, url: str, html: str, reg_id: str, proof, max_pages: int) -> str:
    """首頁只看到公司名：首頁＋同網站的聯絡／關於／隱私頁裡找註冊編號或郵遞區號。"""
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
    for u, h in pages:
        t = extract.visible_text(h)
        if extract.site_confirmation([extract.PageText(u, t)], reg_id, "", "註冊編號") == "註冊編號":
            return "公司名＋註冊編號"
        hit = [x for x in proof if x and _norm(x) in _norm(t)]
        if hit:
            return f"公司名＋{hit[0]}"
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
    for url in tried:
        try:
            html = fetcher.get(url)
        except Exception as e:          # 網域不存在、robots 禁止、逾時：換下一個
            log.append(f"猜 {url}：{type(e).__name__}")
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
        if how != "未確認":
            log.append(f"猜 {url}：首頁{how}確認")
            return url, [page], log
        log.append(f"猜 {url}：連得上但首頁看不到註冊編號（或公司名＋郵遞區號），不採用")
    return "", [], log
