"""第 5 步：crawl.py 只跟同網域、看起來是產品／關於／聯絡的連結，有頁數上限，壞頁不中斷。"""
import copy

import httpx

from houda_leads import crawl, fetch

SITE = {
    "/": '<a href="/products.html">產品介紹</a><a href="/about">關於我們</a><a href="/news">最新消息</a>'
         '<a href="https://other.example/products">別人的產品</a><a href="/products/catalog.pdf">型錄 PDF</a>'
         '<a href="https://www.linkedin.com/company/x">LinkedIn</a><a href="/contact.html">聯絡我們</a>',
    "/products.html": "<p>產品</p>",
    "/about": "<p>關於</p>",
}


def fetcher(cfg, calls, max_pages=None):
    def h(req):
        calls.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host != "acme.example.tw":
            raise AssertionError(f"不該請求 {req.url}")
        body = SITE.get(req.url.path)
        return httpx.Response(200, text=body) if body is not None else httpx.Response(500)

    c = copy.deepcopy(cfg)
    c["fetch"]["min_interval_seconds"] = 0
    if max_pages:
        c["fetch"]["max_pages_per_company"] = max_pages
    return fetch.Fetcher(c, cache_dir=None, client=httpx.Client(transport=httpx.MockTransport(h))), c


def test_follows_only_relevant_same_site_links(cfg):
    calls = []
    f, c = fetcher(cfg, calls)
    pages, problems = crawl.crawl_site(f, "https://acme.example.tw/", c)
    urls = [p.url for p in pages]
    assert urls == ["https://acme.example.tw/", "https://acme.example.tw/products.html", "https://acme.example.tw/about"]
    assert not any("news" in u or ".pdf" in u or "other.example" in u or "linkedin" in u for u in calls)
    assert any("contact.html" in p for p in problems)          # 500：記下來，不中斷


def test_page_cap(cfg):
    calls = []
    f, c = fetcher(cfg, calls, max_pages=2)
    pages, _ = crawl.crawl_site(f, "https://acme.example.tw/", c)
    assert len(pages) == 2
