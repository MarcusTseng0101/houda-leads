"""抓一家公司的官網：首頁＋同網域、看起來是產品／關於／聯絡的頁面。所有請求都經過 fetch.Fetcher。"""
from __future__ import annotations

import time
from urllib.parse import urldefrag, urlsplit

from . import extract
from .fetch import ForbiddenDomain, RobotsDisallowed

# 不抓的檔案（看網址路徑，不看連結文字）
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".rar", ".doc", ".docx", ".xls", ".xlsx", ".mp4")


def _same_site(url: str, root: str) -> bool:
    h = (urlsplit(url).hostname or "").lower()
    return h == root or h.endswith("." + root)


def crawl_site(fetcher, start_url: str, cfg: dict) -> tuple[list[extract.Page], list[str]]:
    """回傳 (抓到的頁面, 問題紀錄)。抓不到首頁就回傳空清單，由呼叫的人送人工。"""
    f = cfg["fetch"]
    cap, words = int(f["max_pages_per_company"]), [w.lower() for w in f["follow_link_words"]]
    root = extract.site_root(start_url)
    pages, problems, seen = [], [], set()
    queue = [start_url]
    budget = float(f.get("max_seconds_per_company", 0) or 0)
    t0 = time.monotonic()
    while queue and len(pages) < cap:
        if budget and time.monotonic() - t0 > budget:
            problems.append(f"超過每家 {budget:.0f} 秒上限，停在 {len(pages)} 頁")
            break
        url = urldefrag(queue.pop(0))[0]
        if url in seen:
            continue
        seen.add(url)
        try:
            html = fetcher.get(url)
        except ForbiddenDomain:
            problems.append(f"禁止網域：{url}")
            continue
        except RobotsDisallowed:
            problems.append(f"robots禁止：{url}")
            continue
        except Exception as e:           # 連不上、逾時、4xx/5xx：記下來，不中斷整批
            problems.append(f"抓不到：{url}（{type(e).__name__}）")
            continue
        pages.append(extract.Page(url, html))
        for href, text in extract.links(html, url):
            low = (href + " " + text).lower()
            if href.startswith(("http://", "https://")) and _same_site(href, root) \
                    and any(w in low for w in words) and href not in seen \
                    and not urlsplit(href).path.lower().endswith(SKIP_EXT):
                queue.append(href)
    return pages, problems
