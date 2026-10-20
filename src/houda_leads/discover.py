"""找官網：沒有搜尋服務（不用 API key），所以從公司名猜網域，再用官網確認擋掉猜錯的。

失敗模式 4：猜到的網域只有在頁面上看得到註冊編號或完整公司名才採用；確認不了就當沒有官網，
不准「看起來像」就用。英國公司依 Trading Disclosures Regulations 2015 網站要寫註冊編號，所以英國最好確認。
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


def discover(fetcher, names: list[str], reg_id: str, country: str, cfg: dict) -> tuple[str, list[extract.Page], list[str]]:
    """回傳 (確認過的官網, 首頁, 紀錄)。沒確認到就回傳 ("", [], 紀錄)。"""
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
        if how != "未確認":
            log.append(f"猜 {url}：首頁{how}確認")
            return url, [page], log
        log.append(f"猜 {url}：連得上但首頁看不到註冊編號或公司名，不採用")
    return "", [], log
