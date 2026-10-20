"""Build order 第 2 步：fetch.py 的禁止清單、robots、速率限制、快取。全部用假網路和假時鐘。"""
import httpx
import pytest

from houda_leads import fetch


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def make(cfg, handler, tmp_path=None, clock=None):
    clock = clock or FakeClock()
    calls = []

    def h(req):
        calls.append(str(req.url))
        return handler(req)

    client = httpx.Client(transport=httpx.MockTransport(h))
    f = fetch.Fetcher(cfg, cache_dir=tmp_path, client=client, clock=clock.now, sleep=clock.sleep)
    return f, calls, clock


def ok(req):
    if req.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    return httpx.Response(200, text=f"<p>{req.url.path}</p>")


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/in/someone",
    "https://linkedin.com/company/x",
    "https://tw.linkedin.com/company/x",
    "https://lnkd.in/abc",
])
def test_deny_domain_never_sends_request(cfg, url):
    f, calls, _ = make(cfg, ok)
    with pytest.raises(fetch.ForbiddenDomain):
        f.get(url)
    assert calls == []                 # 連 robots.txt 都不能去要
    assert f.denied_requests == 1


def test_deny_domain_after_redirect(cfg):
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "example.tw":
            return httpx.Response(302, headers={"Location": "https://www.linkedin.com/company/x"})
        raise AssertionError("不該真的打到 linkedin")

    f, calls, _ = make(cfg, h)
    with pytest.raises(fetch.ForbiddenDomain):
        f.get("https://example.tw/team")
    assert not any("linkedin" in c for c in calls)


def test_lookalike_domain_is_not_denied():
    assert not fetch.is_denied("https://notlinkedin.com/x")
    assert not fetch.is_denied("https://linkedin.com.example.tw/x")


def test_robots_disallow(cfg):
    f, calls, _ = make(cfg, ok)
    with pytest.raises(fetch.RobotsDisallowed):
        f.get("https://example.tw/private/page")
    assert calls == ["https://example.tw/robots.txt"]


def test_missing_robots_allows(cfg):
    def h(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, text="hi")

    f, _, _ = make(cfg, h)
    assert f.get("https://example.tw/a") == "hi"


def test_robots_server_error_blocks_all(cfg):
    def h(req):
        return httpx.Response(503) if req.url.path == "/robots.txt" else httpx.Response(200, text="hi")

    f, _, _ = make(cfg, h)
    with pytest.raises(fetch.RobotsDisallowed):
        f.get("https://example.tw/a")


def test_rate_limit_same_host(cfg):
    f, _, clock = make(cfg, ok)
    f.get("https://example.tw/a")
    f.get("https://example.tw/b")
    gap = cfg["fetch"]["min_interval_seconds"]
    # robots → /a → /b：每次之間都要等滿間隔
    assert clock.slept == [gap, gap]


def test_rate_limit_is_per_host(cfg):
    f, _, clock = make(cfg, ok)
    f.get("https://a.example.tw/x")
    f.get("https://b.example.tw/x")
    gap = cfg["fetch"]["min_interval_seconds"]
    assert clock.slept == [gap, gap]   # 只有各自的 robots → 頁面之間等


def test_cache_hit_skips_network(cfg, tmp_path):
    f, calls, _ = make(cfg, ok, tmp_path=tmp_path)
    assert f.get("https://example.tw/a") == "<p>/a</p>"
    n = len(calls)
    f2, calls2, _ = make(cfg, ok, tmp_path=tmp_path)
    assert f2.get("https://example.tw/a") == "<p>/a</p>"
    assert calls2 == [] and n == 2
