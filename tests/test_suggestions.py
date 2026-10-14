import asyncio
import time

import httpx
import pytest

from archer.app import app
from archer.searxng import SearXNG, suggestion_terms
from test_app import client, fake, SEARX_REPLY  # noqa: F401


@pytest.mark.parametrize("q", ["", "  ", "x", " x ", "x" * 301])
def test_ineligible_queries_do_not_contact_upstream(client, q):
    def unexpected(request):
        pytest.fail("ineligible query reached upstream")
    app.state.searx = SearXNG(fake(unexpected))
    assert client.get("/suggest", params={"q": q}).json() == [q, []]


@pytest.mark.parametrize("q", ["!", ":", "!go", " café "])
def test_original_query_echo_and_syntax_are_preserved(client, q):
    def reply(request):
        assert request.url.params["q"] == q
        assert "x-requested-with" not in request.headers
        return httpx.Response(200, json=["", ["!google"], [], [], {"google:suggestrelevance": [597]}])
    app.state.searx = SearXNG(fake(reply))
    assert client.get("/suggest", params={"q": q}).json() == [q, ["!google"], [], [], {"google:suggestrelevance": [597]}]


def test_filtering_keeps_relevance_aligned_and_drops_other_metadata(client):
    raw = ["zen", None, "  Zendaya  ", "zendaya", "", "x" * 301, "zen movies"]
    app.state.searx = SearXNG(fake(lambda r: httpx.Response(200, json=[
        "zen", raw, ["untrusted description"], ["https://example.com"],
        {"google:suggestrelevance": [610, 609, 608, 607, 606, 605, True], "google:suggesttype": ["NAVIGATION"]},
    ])))
    assert client.get("/suggest?q=zen").json() == ["zen", ["Zendaya", "zen movies"], [], [], {"google:suggestrelevance": [608, 599]}]


def test_terms_are_bounded_and_tolerate_malformed_values():
    assert suggestion_terms({}, "q") == []
    assert suggestion_terms([" CAFÉ ", "café", "Q", 1] + [f"q {i}" for i in range(20)], "q") == ["CAFÉ"] + [f"q {i}" for i in range(7)]


def test_cache_expiry_capacity_empty_results_and_failure_recovery(monkeypatch):
    import archer.searxng as module
    monkeypatch.setattr(module, "_CACHE_MAX", 2)
    async def run():
        requests = []
        fail = False
        def reply(request):
            requests.append(request)
            if fail:
                raise httpx.ReadTimeout("slow")
            q = request.url.params["q"]
            return httpx.Response(200, json=[q, [] if q == "empty" else [q + " suggestion"]])
        async with fake(reply) as http:
            searx = SearXNG(http)
            await searx.autocomplete("ab", timeout=.7)
            await searx.autocomplete("ab")
            assert len(requests) == 1
            assert requests[0].extensions["timeout"]["read"] == .7
            searx._suggest_cache["ab"] = (0, ["ab", []])
            await searx.autocomplete("ab")
            assert len(requests) == 2
            await searx.autocomplete("cd")
            await searx.autocomplete("ef")
            assert len(searx._suggest_cache) == 2 and "ab" not in searx._suggest_cache
            await searx.autocomplete("empty")
            await searx.autocomplete("empty")
            assert sum(r.url.params["q"] == "empty" for r in requests) == 1
            assert 0 < searx._suggest_cache["empty"][0] - time.monotonic() <= 5
            fail = True
            assert await searx.autocomplete("failed") == ["failed", []]
            assert "failed" not in searx._suggest_cache
            fail = False
            assert (await searx.autocomplete("failed"))[1] == ["failed suggestion"]
    asyncio.run(run())


def test_concurrent_requests_share_lookup_and_survive_caller_cancellation():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        requests = []
        async def reply(request):
            requests.append(request)
            started.set()
            await release.wait()
            return httpx.Response(200, json=["", ["!google"]])
        async with fake(reply) as http:
            searx = SearXNG(http)
            first = asyncio.create_task(searx.autocomplete("!go"))
            await started.wait()
            second = asyncio.create_task(searx.autocomplete("!go"))
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            assert (await second)[:2] == ["!go", ["!google"]]
            assert len(requests) == 1
            assert not searx._suggest_pending
    asyncio.run(run())


@pytest.mark.parametrize("tab", ["all", "images", "news", "videos"])
def test_shared_form_and_related_terms_on_every_tab(client, tab):
    app.state.searx = SearXNG(fake(lambda r: httpx.Response(200, json={
        **SEARX_REPLY, "suggestions": ["zendaya", "zendaya movies", "ZENDAYA MOVIES", "<b>films</b>"],
        "corrections": ["zendaya corrected"],
    })))
    html = client.get("/search", params={"q": "zendaya", "tab": tab}).text
    assert 'data-searchbox' in html
    assert 'data-suggest-min="2"' in html
    assert 'data-related-q' not in html
    assert '&lt;b&gt;films&lt;/b&gt;' in html
    suffix = "" if tab == "all" else f"&tab={tab}"
    assert f'href="/search?q=zendaya+movies{suffix}"' in html
    assert f'href="/search?q=zendaya+corrected{suffix}"' in html
    if tab != "all":
        assert f'name="tab" value="{tab}"' in html


def test_home_has_the_shared_form_and_plain_search_fallback(client):
    html = client.get("/").text
    assert 'data-searchbox' in html
    assert 'action="/search" method="get"' in html
    assert 'autofocus' in html
