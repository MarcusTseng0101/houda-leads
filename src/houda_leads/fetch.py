"""唯一的對外 HTTP。所有網頁請求都必須經過 Fetcher.get()。

- 網域禁止清單（失敗模式 1：LinkedIn 禁止自動擷取，Proxycurl 因此被告關站）
- robots.txt：不允許就不抓
- 同一網域請求間隔（失敗模式 8）
- 快取到 data/cache/，重跑不會再打一次對方網站
- search()：本機自架的 searchcrawl（使用者 2026-11-03 同意），找不到官網時用
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.robotparser
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx

# 只有這裡和 export.py（給業務手動點的搜尋連結）可以提到這個網域
DENY_DOMAINS = frozenset({"linkedin.com", "www.linkedin.com", "lnkd.in"})


class ForbiddenDomain(Exception):
    pass


class RobotsDisallowed(Exception):
    pass


class SiteUnreachable(RobotsDisallowed):
    """連 robots.txt 都連不上（網域不存在、憑證錯、逾時）：照樣不抓，但找官網時可以換 http 再試（2026-11-02）。"""


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_denied(url: str) -> bool:
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in DENY_DOMAINS)


class Fetcher:
    def __init__(self, cfg: dict, cache_dir: Path | None = Path("data/cache"),
                 client: httpx.Client | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        f = cfg["fetch"]
        self.cfg = cfg
        self.searches = 0
        self.offline = False           # True：只讀快取，沒快取就當抓不到（reclassify 用）
        self.interval = float(f["min_interval_seconds"])
        self.ua = f["user_agent"]
        # 不讓 httpx 自動跟轉址：自動跟的話，轉到被禁止的網域時請求已經送出去了
        self.client = client or httpx.Client(timeout=f["timeout_seconds"], follow_redirects=False,
                                             headers={"User-Agent": self.ua})
        self.max_redirects = 5
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.clock, self.sleep = clock, sleep
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.denied_requests = 0       # eval 的「對 DENY_DOMAINS 的請求 = 0」靠這個
        self.requests = 0

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.clock() - last
            if gap < self.interval:
                self.sleep(self.interval - gap)
        self._last[host] = self.clock()

    def _raw_get(self, url: str) -> httpx.Response:
        """送出請求；轉址由這裡一步一步跟，每一步送出前都檢查禁止清單。"""
        for _ in range(self.max_redirects + 1):
            if is_denied(url):
                self.denied_requests += 1
                raise ForbiddenDomain(url)
            self._wait(host_of(url))
            self.requests += 1
            r = self.client.get(url, follow_redirects=False)
            if r.is_redirect and "location" in r.headers:
                url = str(r.url.join(r.headers["location"]))
                continue
            return r
        raise httpx.TooManyRedirects(f"轉址超過 {self.max_redirects} 次：{url}", request=r.request)

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self._raw_get(base + "/robots.txt")
                # 4xx：沒有 robots.txt → 全部允許；5xx 或連不上 → 保守起見全部不抓
                if r.status_code >= 500:
                    rp.disallow_all = True
                elif r.status_code >= 400:
                    rp.allow_all = True
                else:
                    rp.parse(r.text.splitlines())
            except httpx.HTTPError:
                rp.disallow_all = True
                rp.unreachable = True
            self._robots[base] = rp
        return rp

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".html")

    def search(self, query: str, limit: int, country: str = "TW") -> list[str]:
        """自架的 searchcrawl（本機 HTTP，不用 API key）。只回傳結果網址；禁止網域直接丟掉、不算請求。
        結果存快取：重跑不會再搜一次（搜尋背後是 DuckDuckGo，用太兇會被限速）。"""
        s = self.cfg["search"]
        lang, region = s.get("region", {}).get(country, [s.get("lang", "zh-TW"), s.get("country", "tw")])
        key = f"{query}|{limit}" if country == "TW" else f"{query}|{limit}|{country}"   # 台灣沿用舊的快取鍵
        cp = self.cache_dir / ("search-" + hashlib.sha256(key.encode()).hexdigest() + ".json") \
            if self.cache_dir else None
        if cp and cp.exists():
            urls = json.loads(cp.read_text(encoding="utf-8"))
        else:
            self._wait("searchcrawl")
            self.searches += 1
            r = self.client.post(s["url"], json={"query": query, "limit": limit, "lang": lang, "country": region},
                                 timeout=float(s.get("timeout_seconds", 60)))
            if r.status_code == 502:          # 每個搜尋引擎都沒結果
                urls = []
            else:
                r.raise_for_status()
                urls = [d.get("url", "") for d in r.json().get("data", [])]
            if cp:
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
        return [u for u in urls if u.startswith(("http://", "https://")) and not is_denied(u)]

    def get(self, url: str) -> str:
        if is_denied(url):
            self.denied_requests += 1
            raise ForbiddenDomain(url)
        cp = self._cache_path(url)
        if cp and cp.exists():
            return cp.read_text(encoding="utf-8")
        if self.offline:
            raise SiteUnreachable(f"快取裡沒有：{url}")
        rp = self._robots_for(url)
        if not rp.can_fetch(self.ua, url):
            raise (SiteUnreachable if getattr(rp, "unreachable", False) else RobotsDisallowed)(url)
        r = self._raw_get(url)
        r.raise_for_status()
        text = r.text
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(text, encoding="utf-8")
        return text
