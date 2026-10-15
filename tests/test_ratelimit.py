"""Rate limits: the next free provider answers, later searches skip the limited one, the results page
shows a banner, and everything (strips, AI research) works with DuckDuckGo as the only provider."""

import asyncio
import re
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from archer import searxng
from archer.ai_service import AIService
from archer.app import app
from archer.config import Settings, settings
from archer.searxng import Search, SearXNG

from test_ai import FakeProvider, enable, events, prepare

BASE = f"http://{settings.public_host}:{settings.port}"
SUSPENDED = [["brave", "Suspended: too many requests"], ["duckduckgo", "Suspended: access denied"],
             ["google cse", "Suspended: too many requests"], ["startpage", "Suspended: CAPTCHA"]]
DDG_HTML = """<div class="result results_links results_links_deep web-result">
<a rel="nofollow" class="result__a" href="https://example.com/guide">Official guide</a>
<a class="result__snippet" href="https://example.com/guide">A source-backed explanation of the topic.</a>
<div class="clear"></div></div>
<div class="result results_links results_links_deep web-result">
<a rel="nofollow" class="result__a" href="https://example.org/more">Other guide</a>
<a class="result__snippet" href="https://example.org/more">Another source with details.</a>
<div class="clear"></div></div>"""


class Upstream:
    """SearXNG with every engine suspended, and a working DuckDuckGo; counts who was asked."""

    def __init__(self, searxng_down=True, ddg_captcha=False):
        self.searxng_down, self.ddg_captcha, self.calls = searxng_down, ddg_captcha, []

    def __call__(self, request):
        host = request.url.host
        self.calls.append(host)
        if host == "html.duckduckgo.com":
            if self.ddg_captcha:
                return httpx.Response(200, text='<div class="anomaly-modal">Unfortunately, bots use DuckDuckGo too</div>')
            return httpx.Response(200, text=DDG_HTML)
        if host == "duckduckgo.com" and request.url.path == "/":
            return httpx.Response(200, text="vqd='4-123'")
        if host == "duckduckgo.com":
            return httpx.Response(200, json={"results": [{"url": "https://example.com/a", "title": "Guide photo", "image": "https://img.example.com/a.jpg",
                                                          "thumbnail": "https://img.example.com/a_t.jpg", "width": 800, "height": 600}]})
        if request.url.path == "/search":
            if self.searxng_down:
                return httpx.Response(200, json={"results": [], "unresponsive_engines": SUSPENDED})
            return httpx.Response(200, json={"results": [{"title": "SearXNG result", "url": "https://example.net/x", "content": "From SearXNG.",
                                                          "engines": ["google"]}], "unresponsive_engines": []})
        return httpx.Response(404)


@pytest.fixture(autouse=True)
def clean_limits(monkeypatch):
    monkeypatch.setattr(settings, "search_fallback", True)  # conftest turns it off for every other suite
    searxng._limits.clear()
    yield
    searxng._limits.clear()


def search(upstream):
    return Search(httpx.AsyncClient(base_url="http://127.0.0.1:8888", transport=httpx.MockTransport(upstream)))


def test_rate_limited_searxng_falls_back_and_is_then_skipped():
    upstream = Upstream()
    s = search(upstream)
    out = asyncio.run(s.search("why do cats knead"))
    assert out.provider == "duckduckgo" and [r.title for r in out.results] == ["Official guide", "Other guide"]
    assert out.notice == "SearXNG is rate-limited, so these results are from DuckDuckGo."
    assert ("brave", "Suspended: too many requests") in out.limited and ("SearXNG", "every engine is rate-limited") in out.limited
    assert searxng.cooling("searxng") and not searxng.cooling("duckduckgo")
    upstream.calls.clear()
    again = asyncio.run(s.search("another question"))                   # straight to the free provider
    assert again.provider == "duckduckgo" and "127.0.0.1" not in upstream.calls
    assert again.notice == "SearXNG is rate-limited, so these results are from DuckDuckGo."


def test_duckduckgo_captcha_falls_back_to_searxng(monkeypatch):
    monkeypatch.setattr(settings, "search_provider", "duckduckgo")
    out = asyncio.run(search(Upstream(searxng_down=False, ddg_captcha=True)).search("cats"))
    assert out.provider == "searxng" and out.results[0].title == "SearXNG result"
    assert out.notice == "DuckDuckGo is rate-limited, so these results are from SearXNG."
    assert searxng.cooling("duckduckgo")


def test_keyed_providers_join_the_chain_only_with_keys(monkeypatch):
    assert searxng.fallback_order("searxng") == ["searxng", "duckduckgo"]
    monkeypatch.setattr(settings, "brave_api_key", "k")
    monkeypatch.setattr(settings, "serper_api_key", "k")
    assert searxng.fallback_order("duckduckgo") == ["duckduckgo", "google:serper", "brave", "searxng"]


def test_everything_failing_reports_an_error():
    upstream = Upstream(ddg_captcha=True)
    out = asyncio.run(search(upstream).search("cats"))
    assert not out.results and out.error and searxng.cooling("searxng") and searxng.cooling("duckduckgo")


@pytest.fixture
def client(tmp_path):
    upstream = Upstream()
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        ai = AIService(Settings(_env_file=None, DEEPSEEK_API_KEY="fixture-key"), tmp_path)
        ai.provider = FakeProvider()

        async def read(url):
            return "A source-backed explanation from the public page. " * 10
        ai.reader.read = read
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8888", transport=httpx.MockTransport(upstream)) as http:
            application.state.ai = ai
            application.state.searx = SearXNG(http)
            application.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
            yield
        await ai.close()
    app.router.lifespan_context = lifespan
    try:
        with TestClient(app, base_url=BASE) as c:
            yield c
    finally:
        app.router.lifespan_context = original


def test_results_page_shows_the_rate_limit_banner(client):
    html = client.get("/search", params={"q": "why do cats knead"}).text
    banner = re.search(r'<div class="rl-banner".*?</button>', html, re.S)[0]
    assert "SearXNG rate limit reached" in banner and "Searching with DuckDuckGo for now" in banner
    assert "--c: #fb542b" in banner and "Brave" in banner and "too many requests" in banner     # Brave's own colour
    assert "--c: #4285f4" in banner and "Google" in banner and "Startpage" in banner and "data-rl-close" in banner
    assert "Official guide" in html                                                             # DuckDuckGo's results


def test_strips_and_ai_research_work_on_duckduckgo_alone(client, monkeypatch):
    monkeypatch.setattr(settings, "search_provider", "duckduckgo")
    monkeypatch.setattr(settings, "search_fallback", False)
    assert client.get("/api/strip/images", params={"q": "guide"}).json()[0]["thumb"] == "https://img.example.com/a_t.jpg"
    sid, headers = prepare(client)
    enable(client, headers)
    run = events(client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert run[-1]["event"] == "complete" and any(e["event"] == "block" for e in run)
    sources = next(e["data"]["sources"] for e in run if e["event"] == "sources")
    assert any(s["url"] == "https://example.com/guide" for s in sources)


def test_engine_variants_share_one_banner_entry():
    for engine in ("brave", "brave.images", "google cse", "google news", "startpage images"):
        searxng.note_limit(engine, "Suspended: too many requests")
    assert sorted(e["engine"] for e in searxng.active_limits()) == ["brave", "google", "startpage"]
