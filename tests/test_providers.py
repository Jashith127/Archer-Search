"""Search providers, AI providers and the companion app. Every upstream is mocked."""
import asyncio
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from archer import companion, websearch
from archer.ai_budget import Budget
from archer.ai_provider import Provider, readiness, resolve
from archer.app import app
from archer.config import Settings, settings
from archer.searxng import Search
from archer.settings_service import SettingsService, check_connection

BASE = f"http://{settings.public_host}:{settings.port}"

DDG_PAGE = """
<div class="result results_links results_links_deep result--ad">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></h2>
  <div class="clear"></div></div>
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://docs.python.org/3/library/asyncio.html">asyncio &mdash; Asynchronous I/O</a></h2>
  <a class="result__snippet" href="https://docs.python.org/3/library/asyncio.html"><b>asyncio</b> is a library to write concurrent code.</a>
  <div class="clear"></div></div>
<div class="result results_links results_links_deep web-result ">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com%2Fasync-io-python%2F&amp;rut=x">Async IO in Python</a></h2>
  <a class="result__snippet" href="#">A walkthrough.</a>
  <div class="clear"></div></div>
"""


def run(coro):
    return asyncio.run(coro)


def mock(handler):
    return httpx.AsyncClient(base_url=settings.searxng_url, transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------- search

def test_duckduckgo_html_parses_results_skips_ads_and_unwraps_redirects():
    results = websearch.parse_ddg_html(DDG_PAGE)
    assert [r.url for r in results] == ["https://docs.python.org/3/library/asyncio.html", "https://realpython.com/async-io-python/"]
    assert results[0].title == "asyncio — Asynchronous I/O" and results[0].content == "asyncio is a library to write concurrent code."
    assert results[1].host == "realpython.com"


def test_duckduckgo_backend_sends_region_and_safesearch(monkeypatch):
    monkeypatch.setattr(settings, "search_provider", "duckduckgo")
    monkeypatch.setattr(settings, "home_country", "GB")
    monkeypatch.setattr(settings, "search_language", "en-GB")
    monkeypatch.setattr(settings, "safesearch", 2)
    seen = []

    def reply(request):
        seen.append(request)
        return httpx.Response(200, text=DDG_PAGE)

    async def go():
        async with mock(reply) as http:
            return await Search(http).search("asyncio", "general", 2)
    out = run(go())
    form = dict(x.split("=") for x in seen[0].content.decode().split("&"))
    assert seen[0].url.host == "html.duckduckgo.com" and form["kl"] == "uk-en" and form["kp"] == "1"
    assert form["s"] == "10" and form["dc"] == "11"
    assert out.provider == "duckduckgo" and len(out.results) == 2 and not out.error


def test_duckduckgo_images_use_the_search_token(monkeypatch):
    monkeypatch.setattr(settings, "search_provider", "duckduckgo")

    def reply(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<script>vqd="4-12345"</script>')
        assert request.url.path == "/i.js" and request.url.params["vqd"] == "4-12345"
        return httpx.Response(200, json={"results": [{"title": "Red fox", "url": "https://example.org/fox", "image": "https://img.example.org/fox.jpg",
                                                        "thumbnail": "https://tse.example.org/fox.jpg", "width": 800, "height": 600, "source": "Bing"}]})

    async def go():
        async with mock(reply) as http:
            return await Search(http).search("red fox", "images")
    out = run(go())
    assert out.results[0].img_src == "https://img.example.org/fox.jpg" and out.results[0].resolution == "800 × 600"


def test_brave_serper_and_google_backends(monkeypatch):
    monkeypatch.setattr(settings, "brave_api_key", "brave-key")
    monkeypatch.setattr(settings, "serper_api_key", "serper-key")
    monkeypatch.setattr(settings, "google_api_key", "google-key")
    monkeypatch.setattr(settings, "google_cx", "cx-1")

    def reply(request):
        host = request.url.host
        if host == "api.search.brave.com":
            assert request.headers["x-subscription-token"] == "brave-key"
            return httpx.Response(200, json={"web": {"results": [{"title": "Brave hit", "url": "https://brave.example/a", "description": "<strong>x</strong> y"}]}})
        if host == "google.serper.dev":
            assert request.headers["x-api-key"] == "serper-key" and json.loads(request.content)["q"] == "fox"
            return httpx.Response(200, json={"organic": [{"title": "Serper hit", "link": "https://serper.example/a", "snippet": "s"}],
                                             "relatedSearches": [{"query": "fox facts"}]})
        if host == "www.googleapis.com":
            assert request.url.params["cx"] == "cx-1"
            return httpx.Response(200, json={"items": [{"title": "CSE hit", "link": "https://cse.example/a", "snippet": "c"}]})
        raise AssertionError(host)

    async def go(provider, backend="serper"):
        monkeypatch.setattr(settings, "search_provider", provider)
        monkeypatch.setattr(settings, "google_backend", backend)
        async with mock(reply) as http:
            return await Search(http).search("fox")
    brave = run(go("brave"))
    assert brave.results[0].title == "Brave hit" and brave.results[0].content == "x y" and brave.provider == "brave"
    serper = run(go("google"))
    assert serper.results[0].url == "https://serper.example/a" and serper.suggestions == ["fox facts"]
    cse = run(go("google", "cse"))
    assert cse.results[0].title == "CSE hit" and cse.provider == "google"


def test_failures_fall_back_to_duckduckgo_and_say_so(monkeypatch):
    monkeypatch.setattr(settings, "search_fallback", True)

    def reply(request):
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(200, text=DDG_PAGE)
        return httpx.Response(502)

    async def go():
        async with mock(reply) as http:
            return await Search(http).search("asyncio")
    out = run(go())
    assert out.provider == "duckduckgo" and len(out.results) == 2
    assert out.notice.startswith("SearXNG didn't answer (SearXNG returned HTTP 502)")

    monkeypatch.setattr(settings, "search_provider", "brave")
    monkeypatch.setattr(settings, "brave_api_key", "")
    out = run(go())
    assert "Brave Search isn't set up yet" in out.notice

    monkeypatch.setattr(settings, "search_fallback", False)
    out = run(go())
    assert out.error.startswith("Brave Search isn't set up yet") and not out.results


def test_categories_a_provider_lacks_come_from_duckduckgo(monkeypatch):
    monkeypatch.setattr(settings, "search_provider", "google")
    monkeypatch.setattr(settings, "google_backend", "cse")
    monkeypatch.setattr(settings, "google_api_key", "k")
    monkeypatch.setattr(settings, "google_cx", "c")
    hosts = []

    def reply(request):
        hosts.append(request.url.host)
        if request.url.path == "/":
            return httpx.Response(200, text="vqd='4-9'")
        return httpx.Response(200, json={"results": []})

    async def go():
        async with mock(reply) as http:
            return await Search(http).search("news today", "news")
    run(go())
    assert hosts == ["duckduckgo.com", "duckduckgo.com"]


def test_suggestions_follow_the_provider(monkeypatch):
    urls = []

    def reply(request):
        urls.append(request.url)
        return httpx.Response(200, json=[request.url.params["q"], ["fox facts", "fox"]])

    async def go():
        async with mock(reply) as http:
            return await Search(http).autocomplete("fox")
    monkeypatch.setattr(settings, "search_provider", "google")
    assert run(go())[1] == ["fox facts"]
    assert urls[-1].host == "suggestqueries.google.com"
    monkeypatch.setattr(settings, "search_provider", "brave")
    run(go())
    assert urls[-1].host == "duckduckgo.com" and urls[-1].path == "/ac/"
    monkeypatch.setattr(settings, "suggest_provider", "off")
    count = len(urls)
    assert run(go()) == ["fox", []] and len(urls) == count


def test_bangs_use_duckduckgo_unless_searxng_answers(monkeypatch):
    with TestClient(app, base_url=BASE) as client:
        monkeypatch.setattr(settings, "search_provider", "duckduckgo")
        response = client.get("/search", params={"q": "!w fox"}, follow_redirects=False)
        assert response.headers["location"] == "https://duckduckgo.com/?q=%21w+fox"
        monkeypatch.setattr(settings, "search_provider", "searxng")
        response = client.get("/search", params={"q": "!w fox"}, follow_redirects=False)
        assert response.headers["location"].startswith(settings.results_url.split("?")[0])


# ----------------------------------------------------------------------- AI

def ai_settings(**values):
    return Settings(_env_file=None, **values)


def test_resolve_and_readiness_name_what_is_missing():
    s = ai_settings(ai_provider="anthropic")
    r = resolve(s)
    assert (r.base, r.model) == ("https://api.anthropic.com", "claude-haiku-5-5")
    assert readiness(s) == (False, "Add your Claude API key in Archer's settings (AI) to enable answers.")
    assert readiness(ai_settings(ai_provider="ollama")) == (False, "Choose which Ollama model to use in Archer's settings (AI).")
    assert readiness(ai_settings(ai_provider="ollama", ai_model="qwen3:8b")) == (True, "")
    assert readiness(ai_settings(ai_provider="custom", ai_model="m"))[1].startswith("Add your AI server's address")
    assert readiness(ai_settings(DEEPSEEK_API_KEY="k")) == (True, "")


def stream_lines(*events, prefix="data: "):
    return "\n".join(prefix + json.dumps(event) for event in events) + "\n"


ANSWER = '{"visuals": {"images": []}, "blocks": [{"kind": "summary", "text": "Foxes are canids."}]}'


def test_claude_streams_blocks_and_reports_cached_usage():
    seen, blocks = [], []

    def reply(request):
        seen.append(request)
        events = [{"type": "message_start", "message": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 900}}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ANSWER[:40]}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ANSWER[40:]}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 50}}]
        return httpx.Response(200, text=stream_lines(*events))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            provider = Provider(ai_settings(ai_provider="anthropic", anthropic_api_key="sk-ant"), http)
            return await provider.generate("system", {"q": "fox"}, 900, blocks.append)
    output, usage = run(go())
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://api.anthropic.com/v1/messages" and seen[0].headers["x-api-key"] == "sk-ant"
    assert body["model"] == "claude-haiku-5-5" and body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert blocks == [{"kind": "summary", "text": "Foxes are canids."}] and output["blocks"] == blocks
    assert usage == {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 900}, "completion_tokens": 50}


def test_claude_planning_becomes_openai_shaped_tool_calls():
    def reply(request):
        body = json.loads(request.content)
        assert body["tools"][0] == {"name": "search_web", "description": "Search", "input_schema": {"type": "object"}}
        return httpx.Response(200, json={"stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "text", "text": "Searching."}, {"type": "tool_use", "id": "t1", "name": "search_web", "input": {"query": "fox"}}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            provider = Provider(ai_settings(ai_provider="anthropic", anthropic_api_key="k"), http)
            return await provider.plan("s", {}, [{"type": "function", "function": {"name": "search_web", "description": "Search", "parameters": {"type": "object"}}}])
    message, usage = run(go())
    assert message["tool_calls"][0]["function"] == {"name": "search_web", "arguments": '{"query": "fox"}'}
    assert usage["prompt_tokens"] == 10 and usage["completion_tokens"] == 5


def test_ollama_streams_natively_with_a_context_window_and_skips_tools_it_cannot_use():
    seen = []

    def reply(request):
        seen.append(request)
        body = json.loads(request.content)
        if not body["stream"]:
            return httpx.Response(400, json={"error": "model does not support tools"})
        lines = [{"message": {"content": ANSWER[:30]}, "done": False},
                 {"message": {"content": ANSWER[30:]}, "done": True, "done_reason": "stop", "prompt_eval_count": 700, "eval_count": 80}]
        return httpx.Response(200, text="\n".join(json.dumps(line) for line in lines))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            provider = Provider(ai_settings(ai_provider="ollama", ai_model="qwen3:8b", ollama_context="32768"), http)
            plan = await provider.plan("s", {}, [])
            answer = await provider.generate("s", {}, 900)
            return plan, answer
    (message, _), (output, usage) = run(go())
    assert message["tool_calls"] == []
    body = json.loads(seen[1].content)
    assert seen[1].url == "http://127.0.0.1:11434/api/chat" and body["format"] == "json"
    assert body["options"] == {"num_ctx": 32768, "num_predict": 900} and "authorization" not in seen[1].headers
    assert output["blocks"][0]["kind"] == "summary" and usage == {"prompt_tokens": 700, "completion_tokens": 80}


@pytest.mark.parametrize("provider,model,expect,absent", [
    ("deepseek", "", {"max_tokens": 900, "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}}, "max_completion_tokens"),
    ("openai", "gpt-5-mini", {"max_completion_tokens": 900, "reasoning_effort": "low"}, "thinking"),
    ("openai", "gpt-4.1-mini", {"max_completion_tokens": 900}, "reasoning_effort"),
    ("openrouter", "", {"max_tokens": 900, "model": "openrouter/auto"}, "thinking"),
    ("custom", "local-model", {"max_tokens": 900}, "response_format"),
])
def test_openai_protocol_bodies_suit_each_provider(provider, model, expect, absent):
    seen = []

    def reply(request):
        seen.append(request)
        chunk = {"choices": [{"delta": {"content": ANSWER}, "finish_reason": "stop"}]}
        return httpx.Response(200, text=stream_lines(chunk, {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4}}) + "data: [DONE]\n")

    values = {"ai_provider": provider, "ai_model": model, "DEEPSEEK_API_KEY": "d", "openai_api_key": "o",
              "openrouter_api_key": "r", "ai_base_url": "http://127.0.0.1:1234/v1" if provider == "custom" else ""}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            return await Provider(ai_settings(**values), http).generate("s", {}, 900)
    output, usage = run(go())
    body = json.loads(seen[0].content)
    assert all(body[key] == value for key, value in expect.items()) and absent not in body
    assert usage == {"prompt_tokens": 3, "completion_tokens": 4} and output["blocks"]
    if provider == "custom":
        assert str(seen[0].url) == "http://127.0.0.1:1234/v1/chat/completions" and "authorization" not in seen[0].headers


def test_fenced_answers_still_parse():
    def reply(request):
        text = "```json\n" + ANSWER + "\n```"
        return httpx.Response(200, text=stream_lines({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            return await Provider(ai_settings(ai_provider="custom", ai_base_url="http://x/v1", ai_model="m"), http).generate("s", {}, 900)
    assert run(go())[0]["blocks"][0]["text"] == "Foxes are canids."


def test_budget_prices_local_models_at_zero(tmp_path):
    budget = Budget(ai_settings(ai_provider="ollama", ai_model="qwen3:8b"), tmp_path)
    assert budget.priced and budget.cost(10_000, 10_000) == 0
    assert Budget(ai_settings(DEEPSEEK_API_KEY="k"), tmp_path).rates == (.30, 1.20, .006)


# ----------------------------------------------------------------- settings

def test_switching_ai_provider_resets_model_and_address(tmp_path):
    path = tmp_path / ".env"
    path.write_text("ARCHER_AI_MODEL=deepseek-chat\nARCHER_AI_BASE_URL=https://proxy.example/v1\n", encoding="utf-8")
    service = SettingsService(path, live=Settings(_env_file=path))
    service.apply({"ai_provider": "anthropic", "anthropic_api_key": "sk-ant-key-1234"})
    text = path.read_text(encoding="utf-8")
    assert "ARCHER_AI_PROVIDER=anthropic" in text and "ARCHER_AI_MODEL=deepseek" not in text and "proxy.example" not in text
    assert service.live.ai_model == "" and resolve(service.live).model == "claude-haiku-5-5"
    fields = {f["name"]: f for f in service.fields()}
    assert fields["anthropic_api_key"]["hint"] == "•••• 1234" and fields["ai_provider"]["explicit"]
    assert fields["search_provider"]["explicit"] is False


def test_connection_checks_for_new_providers():
    seen = []

    def reply(request):
        seen.append(request)
        if request.url.host == "api.anthropic.com":
            return httpx.Response(200, json={"data": [{"id": "claude-haiku-5-5"}, {"id": "claude-sonnet-5-5"}]})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if request.url.host == "google.serper.dev":
            return httpx.Response(401 if request.headers["x-api-key"] == "bad" else 200, json={"credits": 1})
        if request.url.host == "api.search.brave.com":
            return httpx.Response(429)
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
            return [await check_connection(http, "ai", "sk-ant", provider="anthropic"),
                    await check_connection(http, "ai", provider="ollama"),
                    await check_connection(http, "serper", "bad"),
                    await check_connection(http, "serper", "good"),
                    await check_connection(http, "brave", "key"),
                    await check_connection(http, "ai", provider="openai")]
    claude, ollama, bad, good, brave, openai = run(go())
    assert claude["ok"] and claude["models"] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert seen[0].headers["anthropic-version"] == "2023-06-01"
    assert ollama["ok"] and ollama["models"] == ["qwen3:8b"]
    assert not bad["ok"] and good["ok"] and "credit" in good["message"]
    assert not brave["ok"] and "credit" in brave["message"]
    assert openai == {"ok": False, "message": "Add a key first."} or settings.openai_api_key


# ---------------------------------------------------------------- companion

@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("openai_api_key=sk-openai-secret-wxyz\n", encoding="utf-8")
    monkeypatch.setattr(companion, "STATE_FILE", tmp_path / "companion.json")
    monkeypatch.setattr(companion, "browsers", lambda: [{"id": "edge", "name": "Microsoft Edge", "settings": "edge://settings/searchEngines", "default": True, "chromium": True}])
    with TestClient(app, base_url=BASE) as c:
        app.state.settings_service = SettingsService(path, live=Settings(_env_file=path))
        yield c
    del app.state.settings_service


def guarded(client, page="/app"):
    html = client.get(page).text
    csrf = re.search(r'data-csrf="([^"]+)"', html)[1]
    return html, {"Origin": BASE, "Sec-Fetch-Site": "same-origin", "X-Archer-CSRF": csrf}


def test_companion_pages_render_every_provider_without_keys(client):
    html, _ = guarded(client)
    for label in ("DuckDuckGo", "SearXNG", "Brave Search", "Google", "DeepSeek", "OpenAI", "Claude", "Ollama", "OpenRouter", "Microsoft Edge"):
        assert label in html
    assert "Recommended" in html and "sk-openai-secret" not in html
    welcome, _ = guarded(client, "/app/welcome")
    assert "Welcome to Archer" in welcome and 'data-step="browser"' in welcome and "sk-openai-secret" not in welcome
    assert client.get("/settings", follow_redirects=False).headers["location"] == "/app"


def test_finishing_setup_is_guarded_and_remembered(client):
    _, headers = guarded(client)
    assert client.post("/api/app/onboarded", json={"enabled": True}).status_code == 403
    assert client.post("/api/app/onboarded", json={"enabled": True}, headers=headers).json() == {"onboarded": True}
    assert companion.onboarded()
    assert client.get("/api/settings").json()["app"]["onboarded"] is True


def test_browser_settings_only_open_known_browsers(client):
    _, headers = guarded(client)
    assert client.post("/api/app/browser", json={"browser": "notepad"}, headers=headers).status_code == 422
    assert client.post("/api/app/browser", json={"browser": "edge"}).status_code == 403
