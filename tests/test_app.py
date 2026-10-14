import re

import httpx
import pytest
from fastapi.testclient import TestClient

from archer.app import app
from archer.config import settings
from archer.searxng import SearXNG

BASE = f"http://{settings.public_host}:{settings.port}"

SEARX_REPLY = {
    "results": [
        {"url": "https://en.wikipedia.org/wiki/Zendaya", "title": "Zendaya - Wikipedia",
         "content": "Zendaya is an American actress.", "engines": ["duckduckgo", "google cse"]},
        {"url": "https://www.reddit.com/r/movies/comments/1", "title": "Zendaya thread", "content": "a", "engines": ["duckduckgo"]},
        {"url": "https://www.reddit.com/r/popculture/comments/2", "title": "Another thread", "content": "b", "engines": ["duckduckgo"]},
        {"url": "javascript:alert(1)", "title": "evil", "engines": ["x"]},
    ],
    "answers": [], "infoboxes": [], "suggestions": [], "corrections": [],
    "unresponsive_engines": [["brave", "Suspended: too many requests"]],
}


def fake(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.searxng_url, transport=httpx.MockTransport(handler))


def searx_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/autocompleter":
        return httpx.Response(200, json=[request.url.params["q"], ["zendaya movies"]])
    return httpx.Response(200, json=SEARX_REPLY)


def offline(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={})


@pytest.fixture
def client():
    with TestClient(app, base_url=BASE) as c:
        app.state.searxng = fake(searx_handler)
        app.state.searx = SearXNG(app.state.searxng)
        app.state.ext = fake(offline)
        yield c


def test_health(client):
    assert client.get("/health").json()["app"] == "archer"


def test_search_renders_results_page(client):
    html = client.get("/search", params={"q": "zendaya"}).text
    assert "Zendaya - Wikipedia" in html
    assert "duckduckgo · google cse" in html          # engine attribution
    assert "Discussions" in html                      # reddit results grouped
    assert "javascript:alert" not in html             # non-http result dropped
    assert "brave" in html                            # unavailable engines listed


def test_bang_goes_to_searxng(client):
    resp = client.get("/search", params={"q": "!g zendaya"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == settings.results_url.format(q="%21g+zendaya")


def test_calculator_hero(client):
    html = client.get("/search", params={"q": "15% of 2400"}).text
    assert "hero-calc" in html and ">360<" in html


def test_unit_hero(client):
    html = client.get("/search", params={"q": "5 feet in cm"}).text
    assert "hero-convert" in html and "152.4" in html


def test_currency_hero_from_searxng_answer(client):
    def handler(request):
        return httpx.Response(200, json={**SEARX_REPLY, "answers": [{
            "engine": "currency",
            "answer": "100.0 USD = 9500.44928252 INR (1 United States dollar : 95.0044928252 Indian rupee)"}]})

    app.state.searx = SearXNG(fake(handler))
    html = client.get("/search", params={"q": "100 usd to inr"}).text
    assert "hero-convert" in html and "9,500.45" in html and "Indian rupee" in html


def test_static_assets_are_versioned(client):
    # New HTML must never be paired with a cached old stylesheet.
    html = client.get("/search", params={"q": "zendaya"}).text
    assert re.search(r'href="/static/archer\.css\?v=[0-9a-f]{8}"', html)
    assert re.search(r'src="/static/archer\.js\?v=[0-9a-f]{8}"', html)


def test_page_has_security_headers(client):
    resp = client.get("/search", params={"q": "zendaya"})
    assert "script-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("key,label", [("", "AI Mode needs an API key"), ("sk-test-secret-123", "answers cite sources")])
def test_home_offers_both_modes_and_settings(client, monkeypatch, key, label):
    monkeypatch.setattr(settings, "deepseek_api_key", key)
    html = client.get("/").text
    assert 'action="/search"' in html                      # works without JS; AI Mode switches the target
    assert 'data-home-mode="search"' in html and 'data-home-mode="ai"' in html
    assert 'class="home-settings" href="/app"' in html
    assert label in html
    assert "sk-test-secret-123" not in html                 # only readiness reaches the page, never the key


def test_empty_search_goes_home(client):
    resp = client.get("/search", params={"q": "  "}, follow_redirects=False)
    assert resp.headers["location"] == "/"


def test_searxng_down_shows_notice(client):
    def handler(request):
        raise httpx.ConnectError("refused")

    app.state.searx = SearXNG(fake(handler))
    html = client.get("/search", params={"q": "zendaya"}).text
    assert "SearXNG unreachable" in html


def test_strip_api(client):
    def handler(request):
        return httpx.Response(200, json={**SEARX_REPLY, "results": [
            {"url": "https://example.com/a", "title": "A", "thumbnail": "https://img.example/a.jpg", "engines": ["bing news"],
             "publishedDate": "2026-10-10T00:00:00"}]})

    app.state.searx = SearXNG(fake(handler))
    items = client.get("/api/strip/news", params={"q": "zendaya"}).json()
    assert items[0]["thumb"] == "https://img.example/a.jpg"
    assert client.get("/api/strip/nope", params={"q": "x"}).status_code == 404


def test_suggest_returns_opensearch_with_chromium_relevance(client):
    resp = client.get("/suggest", params={"q": "zen"})
    assert resp.json() == ["zen", ["zendaya movies"], [], [], {"google:suggestrelevance": [600]}]
    assert resp.headers["content-type"].startswith("application/x-suggestions+json")
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("reply", [
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"unexpected": "shape"}),
])
def test_suggest_degrades_to_empty(client, reply):
    app.state.searx = SearXNG(fake(reply))
    assert client.get("/suggest", params={"q": "dun"}).json() == ["dun", []]


def test_setup_shows_helium_urls(client):
    html = client.get("/setup").text
    assert f"{settings.base_url}/search?q=%s" in html
    assert f"{settings.base_url}/suggest?q=%s" in html


def test_rejects_foreign_host():
    with TestClient(app, base_url="http://evil.example") as other:
        assert other.get("/health").status_code == 400
