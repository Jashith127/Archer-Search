"""Offline AI contracts: no model billing or public-engine requests."""
import asyncio
from contextlib import asynccontextmanager
import json
import re
import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from archer.app import app
from archer.config import Settings
from archer.ai_service import AIService, validate_block
from archer.ai_provider import AIError, JSONBlocks, Provider
from archer.ai_budget import Budget
from archer.ai_tools import Toolbox
from archer.evidence import Evidence, public_url
from archer.fetcher import PageReader, pinned_address
from archer.query import classify
from archer.searxng import Result, SearchResponse, SearXNG
from archer.views import View

BASE = "http://archer.localhost:4747"


class FakeProvider:
    def __init__(self):
        self.answers = 0
        self.plans = 0
        self.invalid = False
        self.slow = False

    async def plan(self, system, payload, tools, max_output):
        self.plans += 1
        assert "loan_payment" in {t["function"]["name"] for t in tools}
        if self.plans % 2:
            return {"tool_calls": [{"function": {"name": "loan_payment", "arguments": json.dumps({"principal": 1000000, "annual_rate": 10, "months": 60})}}]}, {"prompt_tokens": 100, "completion_tokens": 60}
        return {"content": "Ready"}, {"prompt_tokens": 100, "completion_tokens": 5}

    async def generate(self, system, payload, max_output, on_block, on_visuals=None):
        self.answers += 1
        self.payload = payload
        if self.slow:
            await asyncio.sleep(30)
        block = {"kind": "paragraph", "text": "The source supports this explanation.", "citations": ["FAKE" if self.invalid else payload["evidence"][0]["id"]]}
        on_block(block)
        return {"blocks": [block], "followups": ["What changes with a shorter term?"]}, {"prompt_tokens": 120, "completion_tokens": 40}


def search_reply(request):
    if request.url.path == "/search":
        return httpx.Response(200, json={"results": [
            {"title": "Official guide", "url": "https://example.com/guide", "content": "A source-backed explanation of the topic.", "engines": ["a", "b"]},
            {"title": "Other guide", "url": "https://example.org/guide", "content": "Another source with details.", "engines": ["b"]},
        ], "unresponsive_engines": [["engine", "rate limited"]]})
    return httpx.Response(404, json={})


@pytest.fixture
def ai_client(tmp_path):
    original = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        settings = Settings(_env_file=None, DEEPSEEK_API_KEY="fixture-key")
        ai = AIService(settings, tmp_path)
        ai.provider = FakeProvider()
        async def read(url):
            return "A source-backed explanation from the public page. " * 10
        ai.reader.read = read
        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(search_reply)) as http:
            application.state.ai = ai
            application.state.searx = SearXNG(http)
            application.state.ext = http
            yield
        await ai.close()
    app.router.lifespan_context = lifespan
    try:
        with TestClient(app, base_url=BASE) as client:
            yield client
    finally:
        app.router.lifespan_context = original


def prepare(client, query="why do cats knead"):
    html = client.get("/search", params={"q": query}).text
    sid = re.search(r'data-snapshot="([^"]+)"', html)[1]
    csrf = re.search(r'data-csrf="([^"]+)"', html)[1]
    headers = {"Origin": BASE, "Sec-Fetch-Site": "same-origin", "X-Archer-CSRF": csrf}
    return sid, headers


def enable(client, headers, mode="classic"):
    response = client.post("/api/ai/preferences", headers=headers, json={"consent": True, "mode": mode})
    assert response.status_code == 200, response.text


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines()]


def test_gets_and_suggestions_never_call_ai(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers, "balanced")
    ai_client.get("/search", params={"q": "why do cats knead"})
    ai_client.get("/suggest?q=cats")
    ai_client.get("/search?q=cats&mode=ai")
    assert app.state.ai.provider.answers == app.state.ai.provider.plans == 0
    assert ai_client.get("/api/ai/runs").status_code == 405


@pytest.mark.parametrize("remove,value", [("Origin", None), ("Origin", "http://evil.example"),
    ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Site", None), ("X-Archer-CSRF", "bad")])
def test_paid_posts_require_origin_and_token(ai_client, remove, value):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    if value is None:
        headers.pop(remove)
    else:
        headers[remove] = value
    assert ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid}).status_code == 403
    assert app.state.ai.provider.answers == 0


def test_compact_stream_has_evidence_and_reuses_run(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    body = {"snapshot": sid}
    first = events(ai_client.post("/api/ai/runs", headers=headers, json=body))
    assert any(e["event"] == "block" for e in first)
    assert first[-1]["event"] == "complete"
    assert any(e["event"] == "notice" for e in first)  # partial engines preserved
    second = events(ai_client.post("/api/ai/runs", headers=headers, json=body))
    assert first == second
    assert app.state.ai.provider.answers == 1


def test_research_uses_native_tools_and_custom_ui(ai_client):
    sid, headers = prepare(ai_client, "compare loan repayment options")
    enable(ai_client, headers)
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert result[-1]["event"] == "complete", result
    card = next(e["data"] for e in result if e["event"] == "card")
    assert card["kind"] == "calculation"
    assert card["data"]["monthly_payment"] == 21247.04
    assert app.state.ai.provider.plans == 1
    run_id = result[0]["data"]["id"]
    followup = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research", "parent": run_id, "question": "What changes with a shorter term?"}))
    assert followup[-1]["event"] == "complete"
    assert app.state.ai.runs[followup[0]["data"]["id"]].history


def test_long_conversation_keeps_going_and_trims_oldest_turns(ai_client):
    sid, headers = prepare(ai_client, "compare loan repayment options")
    enable(ai_client, headers)
    ai = app.state.ai
    ai.settings.ai_context_tokens = 16000  # small, so the conversation has to be trimmed to fit
    long_answer = {"kind": "paragraph", "text": "A long cited explanation. " * 40}
    parent, questions = "", []
    for turn in range(14):
        question = f"Follow-up number {turn} about the loan?"
        body = {"snapshot": sid, "depth": "research", "parent": parent, "question": question} if parent else {"snapshot": sid, "depth": "research"}
        run = events(ai_client.post("/api/ai/runs", headers=headers, json=body))
        assert run[-1]["event"] == "complete", run[-1]
        parent = run[0]["data"]["id"]
        questions.append(question if turn else "compare loan repayment options")
        ai.runs[parent].events.insert(-1, {"event": "block", "data": long_answer})
    payload = ai.provider.payload
    assert payload["previous_question"] == questions[-2]
    earlier = payload["earlier_turns_untrusted"]
    assert earlier and len(earlier) < 12                                    # the oldest turns were dropped to fit
    assert [t["question"] for t in earlier] == questions[-2 - len(earlier):-2]  # ...and only the oldest
    assert len(ai.runs[parent].turns) == 12                                 # still carried for the next turn
    ai.prune()
    assert parent in ai.runs and sid in ai.snapshots


def test_no_consent_and_no_key_are_explicit(ai_client):
    sid, headers = prepare(ai_client)
    assert ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid}).status_code == 400
    enable(ai_client, headers)
    app.state.ai.settings.deepseek_api_key = ""
    response = ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid})
    assert "DeepSeek API key" in response.text


def test_balanced_needs_saved_default_and_eligible_query(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    assert ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "automatic": True}).status_code == 400
    enable(ai_client, headers, "balanced")
    assert events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "automatic": True}))[-1]["event"] == "complete"
    sid, headers = prepare(ai_client, "15% of 2400")
    assert ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "automatic": True}).status_code == 400


def test_bad_citation_stops_before_publishing_block(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    app.state.ai.provider.invalid = True
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid}))
    assert result[-1]["event"] == "error"
    assert not any(e["event"] == "block" for e in result)


def test_session_ownership_and_untrusted_fields(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    assert ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "evidence": [{"text": "fake"}]}).status_code == 422
    ai_client.cookies.clear()
    _, foreign = prepare(ai_client)
    assert ai_client.post("/api/ai/runs", headers=foreign, json={"snapshot": sid}).status_code == 400


def test_comparison_rejects_unknown_sources(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    assert ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "selected": ["S1", "FAKE"]}).status_code == 400


def test_unknown_and_unsupported_quantities_are_rejected():
    evidence = [Evidence("S1", "Title", "https://example.com/", "The temperature is 24 degrees.")]
    with pytest.raises(AIError):
        validate_block({"text": "Temperature is 40 degrees", "citations": ["S1"]}, evidence)
    with pytest.raises(AIError):
        validate_block({"text": "Unsupported fact"}, evidence)
    assert validate_block({"text": "Temperature is 24 degrees", "citations": ["S1"]}, evidence)
    with pytest.raises(AIError):
        validate_block({"kind": "table", "columns": ["Fact", "A"], "rows": [[{"text": "Score"}, {"text": "Good"}]]}, evidence)


def test_incremental_json_handles_chunks_and_braces_in_text():
    parser = JSONBlocks()
    text = json.dumps({"blocks": [{"text": 'a { brace } and "quote"'}, {"text": "second"}], "followups": []})
    blocks = []
    for character in text:
        blocks.extend(parser.feed(character))
    assert blocks == [{"text": 'a { brace } and "quote"'}, {"text": "second"}]


def test_provider_sse_contract_and_usage():
    async def run():
        def reply(request):
            body = json.loads(request.content)
            assert body["thinking"] == {"type": "disabled"}
            assert body["response_format"] == {"type": "json_object"}
            answer = '{"blocks":[{"kind":"limitation","text":"Unknown"}],"followups":[]}'
            chunks = [{"choices": [{"delta": {"content": answer[:30]}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": answer[30:]}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 12, "completion_tokens": 15}}]
            return httpx.Response(200, text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n")
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            provider = Provider(Settings(_env_file=None, DEEPSEEK_API_KEY="fake"), http)
            blocks = []
            answer, usage = await provider.generate("Return JSON", {}, 700, blocks.append)
            assert blocks == answer["blocks"]
            assert usage["prompt_tokens"] == 12
    asyncio.run(run())


def test_budget_reservations_survive_restart_and_corruption(tmp_path):
    settings = Settings(_env_file=None, ai_daily_tokens=1000, ai_input_price=1, ai_output_price=2)
    run = {"input": 0, "output": 0, "usd": 0, "input_limit": 5000, "output_limit": 5000}
    budget = Budget(settings, tmp_path)
    reservation = budget.reserve(run, 300, 300)
    assert Budget(settings, tmp_path).load()["tokens"] == 600
    budget.settle(run, reservation, {"prompt_tokens": 100, "completion_tokens": 50})
    assert Budget(settings, tmp_path).load()["tokens"] == 150
    with pytest.raises(AIError):
        budget.reserve(run, 500, 500)
    budget.path.write_text('{"day":"broken","tokens":-1,"usd":0}')
    with pytest.raises(AIError):
        Budget(settings, tmp_path).load()


def test_public_url_and_dns_guards(monkeypatch):
    assert not public_url("javascript:alert(1)")
    assert not public_url("https://user:password@example.com")
    async def run():
        loop = asyncio.get_running_loop()
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "224.0.0.1"):
            async def lookup(*args, **kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))]
            monkeypatch.setattr(loop, "getaddrinfo", lookup)
            with pytest.raises(ValueError):
                await pinned_address("example.com", 80)
    asyncio.run(run())


def test_fetcher_pins_tls_host_and_blocks_redirect_to_private(monkeypatch):
    import archer.fetcher as fetcher
    original_client = httpx.AsyncClient
    async def address(host, port):
        if host == "127.0.0.1":
            raise ValueError("private")
        return "93.184.216.34"
    monkeypatch.setattr(fetcher, "pinned_address", address)
    calls = []
    def reply(request):
        calls.append(request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(reply), **kwargs))
    async def run():
        with pytest.raises(ValueError):
            await PageReader().read("https://example.com/")
        assert len(calls) == 1
    asyncio.run(run())


def test_tools_reject_arbitrary_actions_and_keep_exact_math(tmp_path):
    async def run():
        box = Toolbox(None, None, None, Settings(_env_file=None), [], lambda *args: None)
        result = await box.call("loan_payment", '{"principal":1000000,"annual_rate":10,"months":60}')
        assert result["new_source_ids"] == ["S1"]
        data = json.loads(box.evidence[0].text)
        assert data["monthly_payment"] == 21247.04
        with pytest.raises(ValueError):
            await box.call("shell", '{"command":"bad"}')
        with pytest.raises(ValueError):
            await box.call("search_web", '{"query":"!g anything"}')
        with pytest.raises(ValueError):
            await box.call("show_cards", '{"source_ids":["FAKE"]}')
    asyncio.run(run())


def test_cancellation_deduplication_and_concurrency_limits(tmp_path):
    async def run():
        settings = Settings(_env_file=None, DEEPSEEK_API_KEY="fixture")
        ai = AIService(settings, tmp_path)
        ai.consent = True
        ai.provider = FakeProvider()
        ai.provider.slow = True
        ai.searx = ai.ext = None
        session = ai.session()
        view = View("why", "all", 1, classify("why"), response=SearchResponse(results=[Result("Guide", "https://example.com", "example.com", [], "Some evidence", ["a"])]))
        snap = ai.snapshot(session, view)
        first = ai.start(session, snap.id, "compact")
        assert ai.start(session, snap.id, "compact") is first
        await asyncio.sleep(.01)
        first.task.cancel()
        await first.task
        assert first.done and first.events[-1]["event"] == "error"
        early = ai.start(session, snap.id, "compact", regenerate=True)
        early.task.cancel()
        await asyncio.gather(early.task, return_exceptions=True)
        await asyncio.sleep(0)
        assert early.done
        one = ai.start(session, snap.id, "compact", regenerate=True)
        two = ai.start(session, snap.id, "compact", regenerate=True)
        with pytest.raises(AIError):
            ai.start(session, snap.id, "compact", regenerate=True)
        await ai.close()
    asyncio.run(run())


def test_weather_places_and_regional_watch_tools_keep_source_limits(monkeypatch):
    import archer.ai_tools as tool_module
    async def geo(client, place):
        return {"latitude": 12.97, "longitude": 77.59, "name": "Bengaluru"}
    async def forecast(*args):
        return {"temp": 24, "timezone": "Asia/Kolkata", "forecast_hours": [{"time": "2026-10-15T07:00", "temp": 23, "rain": 20}]}
    async def places(*args):
        return {"label": "Cafes", "area": "Indiranagar", "source": "photon", "map": "https://www.openstreetmap.org/", "places": [{"name": "Cafe", "hours": ""}]}
    async def entity(*args, **kwargs):
        assert kwargs["fresh"] is True
        return {"title": "Film", "wiki_url": "https://en.wikipedia.org/wiki/Film", "watch": {"tmdb": "42", "media": "movie"}}
    async def providers(*args, **kwargs):
        assert args[-1] == "IN" and kwargs["fresh"] is True
        return {"groups": [], "link": ""}
    monkeypatch.setattr(tool_module.weather, "geocode", geo)
    monkeypatch.setattr(tool_module.weather, "forecast", forecast)
    monkeypatch.setattr(tool_module.views, "places_payload", places)
    monkeypatch.setattr(tool_module.wiki, "lookup", entity)
    monkeypatch.setattr(tool_module.watch, "tmdb_providers", providers)
    async def run():
        emitted = []
        box = Toolbox(None, None, None, Settings(_env_file=None, TMDB_API_KEY="fixture"), [], lambda event, data: emitted.append((event, data)))
        await box.call("weather", '{"place":"Bengaluru"}')
        await box.call("places", '{"category":"cafe","area":"Indiranagar"}')
        await box.call("lookup_entity", '{"name":"Film"}')
        await box.call("watch_providers", '{"entity_source_id":"S3"}')
        await box.call("show_cards", '{"source_ids":["S1","S2","S3","S4"]}')
        assert len([e for e in emitted if e[0] == "card"]) == 4
        assert "no ratings" in box.evidence[1].limitation
        assert json.loads(box.evidence[3].text)["country"] == "IN"
        assert box.evidence[0].kind == "tool"
    asyncio.run(run())


def test_search_and_read_tools_count_and_preserve_unreadable_sources():
    async def search(*args):
        return SearchResponse(results=[Result("Guide", "https://example.com/", "example.com", [], "Only the excerpt", ["one", "two"])])
    async def read(url):
        raise ValueError("private redirect")
    async def run():
        box = Toolbox(SimpleNamespace(search=search), None, SimpleNamespace(read=read), Settings(_env_file=None), [], lambda *args: None)
        await box.call("search_web", '{"query":"first question"}')
        await box.call("read_sources", '{"source_ids":["S1"]}')
        assert box.evidence[0].kind == "excerpt"
        assert "could not be read" in box.evidence[0].limitation
        for query in ("second question", "third question"):
            await box.call("search_web", json.dumps({"query": query}))
        with pytest.raises(ValueError):
            await box.call("search_web", '{"query":"fourth question"}')
        assert len(box.evidence) == 1  # same page, multiple engines, still one source
    asyncio.run(run())


def test_read_url_tool_reads_github_repo_and_plain_pages(monkeypatch):
    from archer.ai_service import question_urls
    from archer.sources import github
    assert question_urls("summarize this app https://github.com/Jashith127/Archer-Search.") == ["https://github.com/Jashith127/Archer-Search"]
    assert question_urls("no link here") == [] and question_urls("see http://localhost:4747/x") == []
    assert github.repo_of("https://github.com/a/b/tree/main/src") == ("a", "b") and github.repo_of("https://github.com/settings/x") is None
    async def repo(client, owner, name):
        return {"name": f"{owner}/{name}", "description": "Personal search", "language": "Python", "readme": "Archer is a search engine.", "url": "https://github.com/a/b"}
    monkeypatch.setattr(github, "repo", repo)
    async def read(url):
        return "Docs title\n" + "body text " * 20
    async def run():
        box = Toolbox(None, None, SimpleNamespace(read=read), Settings(_env_file=None), [], lambda *args: None)
        await box.call("read_url", '{"url":"https://github.com/a/b"}')
        await box.call("read_url", '{"url":"https://example.com/docs"}')
        assert box.evidence[0].kind == "page" and "Archer is a search engine." in box.evidence[0].text
        assert box.evidence[1].title == "Docs title"
        with pytest.raises(ValueError):
            await box.call("read_url", '{"url":"ftp://example.com/x"}')
    asyncio.run(run())


def test_fetcher_rejects_compression_and_oversized_pages(monkeypatch):
    import archer.fetcher as fetcher
    original_client = httpx.AsyncClient
    async def address(*args):
        return "93.184.216.34"
    monkeypatch.setattr(fetcher, "pinned_address", address)
    class BytesStream(httpx.AsyncByteStream):
        def __init__(self, content):
            self.content = content
        async def __aiter__(self):
            yield self.content
    async def run():
        for headers, content in (({"content-type": "text/html", "content-encoding": "br"}, b"unreadable"),
                                 ({"content-type": "text/plain"}, b"x" * (fetcher.MAX_BYTES+1))):
            def reply(request):
                return httpx.Response(200, headers=headers, stream=BytesStream(content))
            monkeypatch.setattr(fetcher.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(reply), **kwargs))
            with pytest.raises((ValueError, httpx.DecodingError)):
                await PageReader().read("https://example.com/")
    asyncio.run(run())


def test_entity_summary_and_structured_facts_have_distinct_provenance():
    from archer.evidence import from_view
    from archer.views import Hero
    view = View("Film", "all", 1, classify("Film"), hero=Hero("entity", {
        "title": "Film", "qid": "Q123", "wiki_url": "https://en.wikipedia.org/wiki/Film",
        "extract": "An introductory summary.", "tiles": [{"label": "Runtime", "value": "90 minutes"}],
        "facts": [], "cast": [], "watch": {"services": []}}))
    sources = from_view(view)
    assert sources[0].url == "https://en.wikipedia.org/wiki/Film"
    assert "Runtime" not in sources[0].text
    assert sources[1].url == "https://www.wikidata.org/wiki/Q123"
    assert "Runtime" in sources[1].text


def test_simple_research_skips_planner_and_reserves_answer_space(ai_client):
    sid, headers = prepare(ai_client, "how does induction cooking work")
    enable(ai_client, headers)
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert result[-1]["event"] == "complete"
    assert app.state.ai.provider.plans == 0
    assert result[-1]["data"]["model_calls"] == 1
    assert app.state.ai.settings.ai_answer_tokens >= 4000


def test_full_view_get_and_cached_restore_never_generate(ai_client):
    response = ai_client.get("/ai?q=why+do+cats+knead")
    assert response.status_code == 200 and "ai-fullview" in response.text
    assert app.state.ai.provider.answers == 0
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    run_id = result[0]["data"]["id"]
    calls = app.state.ai.provider.answers
    restored = ai_client.get("/api/ai/runs/" + run_id)
    assert restored.status_code == 200 and len(restored.json()["turns"]) == 1
    assert app.state.ai.provider.answers == calls
    ai_client.cookies.clear()
    assert ai_client.get("/api/ai/runs/" + run_id).status_code == 404


def test_new_full_view_composer_can_start_a_question(ai_client):
    html = ai_client.get("/ai").text
    sid = re.search(r'data-snapshot="([^"]+)"', html)[1]
    token = re.search(r'data-csrf="([^"]+)"', html)[1]
    headers = {"Origin": BASE, "Sec-Fetch-Site": "same-origin", "X-Archer-CSRF": token}
    enable(ai_client, headers)
    assert ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}).status_code == 400
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research", "question": "why do cats knead"}))
    assert result[-1]["event"] == "complete"
    assert app.state.ai.provider.answers == 1


def test_native_media_tool_returns_real_source_linked_urls():
    async def search(query, category):
        assert category == "images"
        return SearchResponse(results=[Result(f"Camera product photograph {n}", f"https://example.com/camera{n}", "example.com", [],
            "A source image", ["one"], img_src=f"https://example.com/camera{n}.jpg", thumbnail=f"https://example.com/thumb{n}.jpg", resolution="1200x800")
            for n in ("", 2, 3)] + [Result("Holiday beach sunset", "https://example.com/beach", "example.com", [], "Off topic", ["one"],
            img_src="https://example.com/beach.jpg", thumbnail="https://example.com/beach-thumb.jpg", resolution="1200x800")])
    async def run():
        box = Toolbox(SimpleNamespace(search=search), None, None, Settings(_env_file=None), [], lambda *args: None)
        await box.call("search_media", '{"query":"camera product photograph","category":"images"}')
        card = box.ui["S1"]
        assert card["kind"] == "media"
        assert card["data"]["items"][0]["url"] == "https://example.com/camera"
        assert card["data"]["items"][0]["image"] == "https://example.com/camera.jpg"
        assert [i["ref"] for i in card["data"]["items"]] == ["S1.1", "S1.2", "S1.3"]  # the beach photo didn't match
    asyncio.run(run())


def test_image_followup_prefetches_media_without_paid_planning(ai_client):
    sid, headers = prepare(ai_client, "why do cats knead")
    enable(ai_client, headers)
    queries = []
    original = app.state.ai.searx.search
    async def search(query, category="general", page=1):
        queries.append((query, category))
        if category == "images":
            return SearchResponse(results=[Result(f"Cats kneading blanket {n}", f"https://example.com/cat{n}", "example.com", [], "Cat photo", ["one"],
                img_src=f"https://example.com/cat{n}.jpg", thumbnail=f"https://example.com/cat-thumb{n}.jpg", resolution="1200x800") for n in range(3)])
        return await original(query, category, page)
    app.state.ai.searx.search = search
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research", "question": "show me images"}))
    assert result[-1]["event"] == "complete", result
    assert any(e["event"] == "card" and e["data"]["kind"] == "media" for e in result)
    assert ("cats knead", "images") in queries
    verdict = [e["data"] for e in result if e["event"] == "visuals" and "verdict" in e["data"]]
    assert verdict and verdict[0]["verdict"] == "show" and len(verdict[0]["keep"]) == 3
    assert app.state.ai.provider.plans == 0


def test_one_bad_quantity_does_not_discard_other_supported_sections(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    async def generate(system, payload, output, accept, on_visuals=None):
        blocks = [{"text": "The source mentions 987654321 people.", "citations": ["S1"]},
                  {"text": "The source supports this explanation.", "citations": ["S1"]}]
        for block in blocks:
            accept(block)
        return {"blocks": blocks}, {"prompt_tokens": 100, "completion_tokens": 100}
    app.state.ai.provider.generate = generate
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert result[-1]["event"] == "complete"
    assert len([e for e in result if e["event"] == "block"]) == 1
    assert any(e["event"] == "notice" and "omitted" in e["data"]["text"] for e in result)


def test_lists_have_item_level_evidence():
    evidence = [Evidence("S1", "Source", "https://example.com/", "A copper coil creates a magnetic field.")]
    block = {"kind": "steps", "heading": "How it works", "items": [{"text": "**The coil:** creates a magnetic field.", "citations": ["S1"]}]}
    assert validate_block(block, evidence)["items"][0]["citations"] == ["S1"]
    block["items"][0]["citations"] = []
    with pytest.raises(AIError):
        validate_block(block, evidence)


def test_media_context_keeps_urls_out_of_the_text_token_budget(tmp_path):
    from archer.ai_service import Run, Snapshot, relevant_tools
    from archer.visuals import plan
    assert plan("show me pictures", "Apple HomeView") == {"need": "required", "category": "images", "query": "Apple HomeView", "reason": "You asked for visuals."}
    assert "lookup_entity" not in relevant_tools("Apple HomeView images and features")
    settings = Settings(_env_file=None)
    ai = AIService(settings, tmp_path)
    evidence = [Evidence("S1", "Images", "", "very long image URLs " * 1000, kind="tool")]
    snap = Snapshot("snap", "owner", "camera", evidence, False, [])
    run = Run("run", "owner", snap, "research", "camera photos", "key", evidence,
        ui={"S1":{"kind":"media", "data":{"category":"images", "items":[{"title":"Camera", "url":"https://example.com/", "thumbnail":"https://example.com/" + "x"*10000}]}}})
    context = json.dumps(ai.context(run, [], [], 3600))
    assert "x"*100 not in context and len(context) < 1500


def test_default_flash_pricing_is_a_cache_aware_ceiling(tmp_path):
    budget = Budget(Settings(_env_file=None), tmp_path)
    assert budget.priced
    assert budget.cost(1000,100,900) < budget.cost(1000,100)
    assert budget.cost(1000,100) == pytest.approx(.00042)


def test_bounded_gzip_pages_are_read_and_bombs_rejected(monkeypatch):
    import gzip
    import archer.fetcher as fetcher
    client = httpx.AsyncClient
    async def address(*args): return "93.184.216.34"
    monkeypatch.setattr(fetcher, "pinned_address", address)
    class Stream(httpx.AsyncByteStream):
        def __init__(self, data): self.data = data
        async def __aiter__(self): yield self.data
    async def run():
        for data, oversized in ((b"A readable page about induction cooking. " * 10, False), (b"a"*(fetcher.MAX_BYTES+1), True)):
            def reply(request):
                return httpx.Response(200, headers={"content-type":"text/plain", "content-encoding":"gzip"}, stream=Stream(gzip.compress(data)))
            monkeypatch.setattr(fetcher.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(reply), **kwargs))
            if oversized:
                with pytest.raises(ValueError): await PageReader().read("https://example.com/")
            else:
                assert "induction" in await PageReader().read("https://example.com/")
    asyncio.run(run())


@pytest.mark.parametrize("question,subject,need,query", [
    ("what does a quokka look like", "", "required", "quokka"),
    ("why do cats knead", "", "helpful", "cats knead"),
    ("eiffel tower bridges and buildings", "", "helpful", "eiffel tower bridges and buildings"),
    ("how does induction cooking work", "", "none", "induction cooking"),
    ("compare loan repayment options", "", "none", "compare loan repayment options"),
    ("python list comprehension error", "", "none", "python list comprehension error"),
    ("what is the meaning of stoicism", "", "none", "meaning stoicism"),
    ("show me a trailer", "Dune Part Two", "required", "trailer"),
])
def test_visual_need_is_decided_before_any_model_call(question, subject, need, query):
    from archer.visuals import plan
    result = plan(question, subject)
    assert result["need"] == need
    assert result["query"] == query


def test_followups_and_comparisons_do_not_repeat_pictures():
    from archer.visuals import plan
    assert plan("how long does the battery last", "pixel 9 phone", followup=True)["need"] == "none"
    assert plan("show me photos of it", "pixel 9 phone", followup=True)["need"] == "required"
    assert plan("best camera phone", "", comparison=True)["need"] == "none"
    assert plan("trailer", "Dune")["category"] == "videos"


def test_relevant_images_drop_pictures_that_only_appeared_for_the_query():
    from archer.imagerank import relevant_images
    results = [Result("Sony WH-1000XM5 review", "https://a.example/xm5", "a.example", [], "", ["one"], img_src="https://a.example/1.jpg", resolution="1200x800"),
               Result("Best headphones of 2026", "https://b.example/list", "b.example", [], "", ["one"], img_src="https://b.example/2.jpg", resolution="1200x800"),
               Result("Sony logo", "https://c.example/sony", "c.example", [], "", ["one"], img_src="https://c.example/3.jpg", resolution="1200x800")]
    kept = relevant_images("sony wh-1000xm5", results)
    assert [r.url for r in kept] == ["https://a.example/xm5"]


def test_summary_facts_callouts_and_highlights_are_validated():
    # A read page, because a summary may no longer rest only on an unverified snippet
    # (Deliverable 1 / U3). The snippet-only case has its own test below.
    evidence = [Evidence("S1", "Spec", "https://example.com/",
                         "The battery is 5000 mAh and lasts about two days. Avoid fast chargers above 45 W.",
                         kind="page", verified=True)]
    summary = validate_block({"kind": "summary", "text": "It lasts ==about two days== on a ==5000 mAh== cell.", "citations": ["S1"]}, evidence, first=True)
    assert summary["kind"] == "summary"
    assert summary["text"] == "It lasts ==about two days== on a 5000 mAh cell."  # one highlight per summary
    later = validate_block({"kind": "summary", "text": "It lasts about two days.", "citations": ["S1"]}, evidence)
    assert later["kind"] == "paragraph"  # only the first block is the bottom line
    facts = validate_block({"kind": "facts", "items": [{"label": "Battery", "text": "5000 mAh", "citations": ["S1"]}]}, evidence)
    assert facts["items"][0]["label"] == "Battery"
    with pytest.raises(AIError):  # unsupported figure
        validate_block({"kind": "facts", "items": [{"label": "Battery", "text": "6000 mAh", "citations": ["S1"]}]}, evidence)
    with pytest.raises(AIError):  # figures need labels
        validate_block({"kind": "facts", "items": [{"text": "5000 mAh", "citations": ["S1"]}]}, evidence)
    callout = validate_block({"kind": "callout", "tone": "warning", "text": "Avoid chargers above 45 W.", "citations": ["S1"]}, evidence)
    assert callout["tone"] == "warning"
    with pytest.raises(AIError):  # a callout is a factual claim
        validate_block({"kind": "callout", "tone": "tip", "text": "Charge overnight."}, evidence)

    # The same assertions hold against a snippet for everything except the summary,
    # which a snippet alone may no longer carry.
    snippet = [Evidence("S2", "Spec", "https://example.org/",
                        "The battery is 5000 mAh and lasts about two days. Avoid fast chargers above 45 W.")]
    with pytest.raises(AIError, match="unverified"):
        validate_block({"kind": "summary", "text": "It lasts about two days.", "citations": ["S2"]},
                       snippet, first=True)
    assert validate_block({"kind": "callout", "tone": "warning", "text": "Avoid chargers above 45 W.",
                           "citations": ["S2"]}, snippet, first=True)["tone"] == "warning"


def test_visual_verdict_respects_what_the_user_asked_for(tmp_path):
    from archer.ai_service import Run, Snapshot, visual_verdict
    snap = Snapshot("snap", "owner", "q", [], False, [])
    items = [{"ref": f"S3.{n}", "title": "t", "url": "https://example.com/"} for n in (1, 2, 3)]
    run = Run("run", "owner", snap, "compact", "q", "key", [], ui={"S3": {"kind": "media", "data": {"category": "images", "items": items}}})
    run.visual = {"need": "helpful"}
    assert visual_verdict({"show": False, "keep": []}, run) == {"verdict": "hide", "keep": []}
    assert visual_verdict({"show": True, "keep": ["S3.2", "S3.1", "S9.9"]}, run) == {"verdict": "show", "keep": ["S3.2", "S3.1"]}
    assert visual_verdict({"show": True, "keep": ["S3.2"]}, run)["verdict"] == "hide"  # one stray photo isn't a gallery
    assert visual_verdict(None, run) == {"verdict": "show", "keep": ["S3.1", "S3.2", "S3.3"]}
    run.visual = {"need": "required"}
    assert visual_verdict({"show": False}, run) == {"verdict": "show", "keep": ["S3.1", "S3.2", "S3.3"]}
    run.ui = {}
    assert visual_verdict({"show": True}, run) is None


def test_visuals_verdict_streams_before_the_blocks():
    parser = JSONBlocks()
    assert parser.feed('{"visuals":{"show":fal') == []
    assert parser.visuals is None
    parser.feed('se,"keep":[]},"blocks":[{"kind":"summary","text":"x"}')
    assert parser.visuals == {"show": False, "keep": []}


def test_abstract_question_gets_no_image_search(ai_client):
    sid, headers = prepare(ai_client, "how does induction cooking work")
    enable(ai_client, headers)
    queries = []
    original = app.state.ai.searx.search
    async def search(query, category="general", page=1):
        queries.append(category)
        return await original(query, category, page)
    app.state.ai.searx.search = search
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert result[-1]["event"] == "complete"
    assert "images" not in queries and "videos" not in queries
    assert {"need": "none", "reason": "Pictures wouldn't add to this answer."} in [e["data"] for e in result if e["event"] == "visuals"]
    assert app.state.ai.provider.payload["visual_need"] == "none"


def test_key_fact_pictures_must_name_the_value():
    evidence = [Evidence("S1", "Cast", "https://example.com/", "Red John is portrayed by Xander Berkeley since 1988.")]
    facts = validate_block({"kind": "facts", "items": [
        {"label": "Portrayed by", "text": "==Xander Berkeley==", "picture": "Xander Berkeley", "citations": ["S1"]},
        {"label": "First killing", "text": "1988", "picture": "Xander Berkeley", "citations": ["S1"]},
        {"label": "Actor", "text": "Xander Berkeley", "picture": "Xander|Berkeley", "citations": ["S1"]}]}, evidence)
    assert [i["picture"] for i in facts["items"]] == ["Xander Berkeley", "", ""]
    listed = validate_block({"kind": "list", "items": [{"text": "Xander Berkeley", "picture": "Xander Berkeley", "citations": ["S1"]}]}, evidence)
    assert listed["items"][0]["picture"] == ""


def test_portraits_use_exact_titles_and_follow_redirects():
    from archer.sources import wiki
    calls = []
    def reply(request):
        calls.append(request.url.params["titles"])
        return httpx.Response(200, json={"query": {
            "normalized": [{"from": "xander berkeley", "to": "Xander berkeley"}],
            "redirects": [{"from": "Xander berkeley", "to": "Xander Berkeley"}],
            "pages": [{"title": "Xander Berkeley", "pageprops": {"wikibase_item": "Q1"}, "thumbnail": {"source": "https://upload.wikimedia.org/x.jpg"}},
                      {"title": "Mercury", "pageprops": {"wikibase_item": "Q2", "disambiguation": ""}, "thumbnail": {"source": "https://upload.wikimedia.org/m.jpg"}}]}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            first = await wiki.portraits(client, ["xander berkeley", "Mercury"])
            again = await wiki.portraits(client, ["xander berkeley", "Mercury"])
        return first, again
    first, again = asyncio.run(run())
    assert first == again == {"xander berkeley": "https://upload.wikimedia.org/x.jpg"}
    assert len(calls) == 1 and "generator" not in calls[0]  # cached, and never a search


def test_numbers_that_end_a_sentence_are_still_supported():
    evidence = [Evidence("S1", "Cast", "https://example.com/", "The first killing was in 1988.")]
    assert validate_block({"text": "It began in 1988.", "citations": ["S1"]}, evidence)["text"] == "It began in 1988."


def test_rich_layout_blocks_stay_grounded():
    evidence = [Evidence("S1", "Amazon listing", "https://amazon.example/msi", "MSI MAG 255F E20 now 8,600 INR, 200Hz IPS. “A superb budget panel for esports.”",
                         thumbnail="https://m.media-amazon.example/msi.jpg"),
                Evidence("S2", "Price history", "https://prices.example/msi", "Usual price 9999 INR; lowest 8199 INR in 2025.")]
    products = validate_block({"kind": "products", "items": [{"label": "MSI MAG 255F E20", "value": "₹8,600", "text": "200Hz IPS",
        "citations": ["S1"], "image": "https://evil.example/x.jpg", "url": "https://evil.example/"}]}, evidence)
    assert products["items"][0]["image"] == "https://m.media-amazon.example/msi.jpg"  # from the source, not the model
    assert products["items"][0]["url"] == "https://amazon.example/msi"
    with pytest.raises(AIError, match="quantity"):
        validate_block({"kind": "products", "items": [{"label": "MSI", "value": "₹7,100", "citations": ["S1"]}]}, evidence)
    bars = validate_block({"kind": "bars", "items": [{"label": "This deal", "value": "8600", "citations": ["S1"]},
                                                     {"label": "Usual", "value": "9999", "citations": ["S2"]}]}, evidence)
    assert [i["value"] for i in bars["items"]] == ["8600", "9999"]
    with pytest.raises(AIError, match="layout"):  # one bar is not a comparison
        validate_block({"kind": "bars", "items": [{"label": "This deal", "value": "8600", "citations": ["S1"]}]}, evidence)
    with pytest.raises(AIError, match="quantity"):  # timeline dates are claims
        validate_block({"kind": "timeline", "items": [{"label": "2024", "text": "Lowest price", "citations": ["S2"]},
                                                      {"label": "2025", "text": "Now", "citations": ["S1"]}]}, evidence)
    quote = validate_block({"kind": "quote", "text": "\"a superb budget panel for esports\"", "citations": ["S1"]}, evidence)
    assert quote["kind"] == "quote"
    paraphrase = validate_block({"kind": "quote", "text": "A great cheap esports monitor.", "citations": ["S1"]}, evidence)
    assert paraphrase["kind"] == "paragraph"  # not verbatim, so not presented as a quote
    verdict = validate_block({"kind": "verdict", "heading": "Good buy", "tone": "warning", "text": "Below the usual 9999 INR.", "citations": ["S2"]}, evidence)
    assert verdict["tone"] == "mixed"
    pc = validate_block({"kind": "proscons", "pros": [{"text": "200Hz IPS", "citations": ["S1"]}], "cons": [{"text": "Not the lowest", "citations": ["S2"]}]}, evidence)
    assert len(pc["pros"]) == len(pc["cons"]) == 1 and pc["items"] == []
    with pytest.raises(AIError):
        validate_block({"kind": "proscons", "pros": [{"text": "Fast"}]}, evidence)


def test_malformed_visual_drops_only_that_section(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    async def generate(system, payload, output, accept, on_visuals=None):
        blocks = [{"kind": "summary", "text": "The source supports this explanation.", "citations": ["S1"]},
                  {"kind": "bars", "items": [{"label": "Only", "value": "12", "citations": ["S1"]}]}]
        for block in blocks:
            accept(block)
        return {"blocks": blocks}, {"prompt_tokens": 100, "completion_tokens": 100}
    app.state.ai.provider.generate = generate
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert result[-1]["event"] == "complete"
    assert [e["data"]["kind"] for e in result if e["event"] == "block"] == ["summary"]


def test_same_question_replays_a_saved_answer_without_a_model_call(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    first = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert first[-1]["event"] == "complete" and app.state.ai.provider.answers == 1
    app.state.ai.sessions.clear()  # a later visit: new browser session, new snapshot
    ai_client.cookies.clear()
    sid, headers = prepare(ai_client)
    again = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert app.state.ai.provider.answers == 1
    assert again[-1]["data"]["saved"] is True and again[-1]["data"]["model_calls"] == 0
    assert again[0]["data"]["id"] != first[0]["data"]["id"]
    assert [e for e in again if e["event"] == "block"] == [e for e in first if e["event"] == "block"]
    # A follow-up on the replayed answer still works, and regenerate pays for a fresh one.
    follow = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research", "parent": again[0]["data"]["id"], "question": "And kittens?"}))
    assert follow[-1]["event"] == "complete" and app.state.ai.provider.answers == 2
    events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research", "regenerate": True}))
    assert app.state.ai.provider.answers == 3


def test_payload_puts_stable_evidence_before_the_question(tmp_path):
    from archer.ai_service import Run, Snapshot
    ai = AIService(Settings(_env_file=None), tmp_path)
    evidence = [Evidence(f"S{n}", f"Source {n}", f"https://example.com/{n}", "cats knead " * n) for n in range(1, 5)]
    run = Run("run", "owner", Snapshot("snap", "owner", "q", evidence, False, []), "research", "why do cats knead", "key", evidence)
    payload = ai.context(run, [], [], 2800)
    assert list(payload)[0] == "evidence" and list(payload)[-1] == "question"
    assert [e["id"] for e in payload["evidence"]] == ["S1", "S2", "S3", "S4"]  # fixed order, not relevance order


@pytest.mark.parametrize("question,expected", [
    ("emi for 10 lakh loan at 9% for 5 years or 4 years", [("loan_payment", {"principal": 1000000.0, "annual_rate": 9.0, "months": 60}),
                                                         ("loan_payment", {"principal": 1000000.0, "annual_rate": 9.0, "months": 48})]),
    ("emi for 15 lakh loan", []),  # no rate or term: the planner decides
    ("compare loan repayment options", []),
    ("rtx 4070 vs rx 7800 xt for 1440p gaming", [("search_web", {"query": "rtx 4070 1440p gaming"}), ("search_web", {"query": "rx 7800 xt 1440p gaming"})]),
    ("will it rain in Pune tomorrow", [("weather", {"place": "Pune"})]),
    ("why do cats knead", []),
])
def test_obvious_tool_work_skips_the_paid_planner(question, expected):
    from archer.ai_service import direct_tools
    assert direct_tools(question) == expected


def test_direct_loan_research_makes_one_model_call(ai_client):
    sid, headers = prepare(ai_client, "emi for 10 lakh loan at 10% for 5 years")
    enable(ai_client, headers)
    result = events(ai_client.post("/api/ai/runs", headers=headers, json={"snapshot": sid, "depth": "research"}))
    assert result[-1]["event"] == "complete", result
    assert app.state.ai.provider.plans == 0 and result[-1]["data"]["model_calls"] == 1
    card = next(e["data"] for e in result if e["event"] == "card")
    assert card["data"]["monthly_payment"] == 21247.04


def test_off_topic_and_repeated_snippets_stay_out_of_the_prompt():
    from archer.ai_service import useful_evidence
    evidence = [Evidence("S1", "Why cats knead", "https://a.example/", "Cats knead to mark territory."),
                Evidence("S2", "Mirror", "https://b.example/", "Cats knead to mark territory."),
                Evidence("S3", "Cookie settings", "https://c.example/", "Accept all cookies."),
                Evidence("S4", "Kneading dough", "https://d.example/", "Knead bread for ten minutes, unlike cats."),
                Evidence("S5", "Forecast", "", "{}", kind="tool"),
                Evidence("S6", "Vet guide", "https://e.example/", "Kneading cats are often content.")]
    kept = [e.id for e in useful_evidence(evidence, "why do cats knead")]
    assert "S2" not in kept and "S3" not in kept and {"S1", "S4", "S5", "S6"} <= set(kept)
    terse = [e.id for e in useful_evidence(evidence[2:3], "why do cats knead")]
    assert terse == ["S3"]  # too little left: the best dropped snippet comes back
