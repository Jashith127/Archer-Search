"""Settings app: .env editing, validation, masking, live apply and the guarded API. No real .env is touched."""
import asyncio
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from archer import envfile
from archer.app import app
from archer.config import Settings, settings
from archer.searxng import SearXNG
from archer.settings_service import SettingsError, SettingsService, check_connection

BASE = f"http://{settings.public_host}:{settings.port}"


def test_env_update_keeps_comments_and_reuses_examples():
    text = "# Archer\n# ARCHER_HOME_LOCATION=Bengaluru\nARCHER_PORT=4747\n\n# keys\nDEEPSEEK_API_KEY=old\n"
    out = envfile.update(text, {"ARCHER_HOME_LOCATION": "New Delhi", "ARCHER_PORT": "4800",
                                "DEEPSEEK_API_KEY": None, "ARCHER_CONTACT": "me@example.com"})
    assert out.startswith("# Archer\n")
    assert 'ARCHER_HOME_LOCATION="New Delhi"' in out  # the commented example is reused, value quoted
    assert "ARCHER_PORT=4800" in out
    assert "\n# DEEPSEEK_API_KEY=\n" in out and "=old" not in out
    assert out.rstrip().endswith(envfile.MANAGED + "\nARCHER_CONTACT=me@example.com")


def test_env_update_neutralises_duplicates():
    out = envfile.update("ARCHER_PORT=1\nARCHER_PORT=2\n", {"ARCHER_PORT": "3"})
    assert out == "ARCHER_PORT=3\n# ARCHER_PORT=\n"


def test_env_quoting_round_trips(tmp_path):
    path = tmp_path / ".env"
    path.write_text(envfile.update("", {"ARCHER_RESULTS_URL": 'http://x/?q={q}#a "b"'}), encoding="utf-8")
    assert Settings(_env_file=path).results_url == 'http://x/?q={q}#a "b"'


@pytest.fixture
def service(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# ARCHER_HOME_LOCATION=\nARCHER_TMDB_API_KEY=legacy-key-1234\n", encoding="utf-8")
    return SettingsService(path, live=Settings(_env_file=path))


def test_apply_writes_validates_and_updates_live(service):
    result = service.apply({"home_location": "Chennai", "ai_daily_usd": "2.5", "safesearch": "2"})
    assert set(result["changed"]) == {"home_location", "ai_daily_usd", "safesearch"}
    assert service.live.home_location == "Chennai" and service.live.ai_daily_usd == 2.5 and service.live.safesearch == 2
    assert "ARCHER_HOME_LOCATION=Chennai" in service.path.read_text(encoding="utf-8")


def test_apply_rejects_invalid_values_without_writing(service):
    before = service.path.read_text(encoding="utf-8")
    with pytest.raises(SettingsError) as error:
        service.apply({"ai_daily_usd": "-1", "suggest_timeout": "fast"})
    assert set(error.value.errors) == {"ai_daily_usd", "suggest_timeout"}
    assert error.value.errors["ai_daily_usd"] == "Use a value above 0."
    for bad in ({"nope": "1"}, {"home_location": "a\nARCHER_PORT=1"}, {"searxng_url": "ftp://x"}):
        with pytest.raises(SettingsError):
            service.apply(bad)
    assert service.path.read_text(encoding="utf-8") == before


def test_secrets_are_masked_and_aliases_cleared(service):
    item = next(f for f in service.fields() if f["name"] == "tmdb_api_key")
    assert item == {"name": "tmdb_api_key", "locked": False, "explicit": True, "set": True, "hint": "•••• 1234"}
    service.apply({"tmdb_api_key": "fresh-key-5678"})
    text = service.path.read_text(encoding="utf-8")
    # The new key uses the conventional name, and the old spelling no longer overrides it.
    assert "TMDB_API_KEY=fresh-key-5678" in text and "ARCHER_TMDB_API_KEY=legacy" not in text
    service.apply({"tmdb_api_key": ""})
    assert service.live.tmdb_api_key == ""


def test_address_must_stay_local(service):
    with pytest.raises(SettingsError) as error:
        service.apply({"public_host": "archer.example.com"})
    assert ".localhost" in error.value.errors["public_host"]


def test_restart_fields_wait_for_restart(service):
    port = service.live.port
    result = service.apply({"port": "4801"})
    assert result["restart"] is True and service.live.port == port
    assert next(f for f in service.fields() if f["name"] == "port")["value"] == "4801"


def test_environment_variables_lock_fields(service, monkeypatch):
    monkeypatch.setenv("ARCHER_HOME_LOCATION", "Pune")
    assert next(f for f in service.fields() if f["name"] == "home_location")["locked"]
    with pytest.raises(SettingsError):
        service.apply({"home_location": "Mysuru"})


@pytest.fixture
def client(tmp_path):
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY=sk-very-secret-value-abcd\n", encoding="utf-8")
    with TestClient(app, base_url=BASE) as c:
        app.state.settings_service = SettingsService(path, live=Settings(_env_file=path))
        app.state.searx = SearXNG(httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(lambda r: httpx.Response(404))))
        yield c
    del app.state.settings_service


def headers_for(client):
    html = client.get("/app").text
    csrf = re.search(r'data-csrf="([^"]+)"', html)[1]
    return html, {"Origin": BASE, "Sec-Fetch-Site": "same-origin", "X-Archer-CSRF": csrf}


def test_settings_page_never_contains_full_keys(client):
    html, _ = headers_for(client)
    assert "Companion" in html and "•••• abcd" in html
    assert "sk-very-secret-value" not in html
    assert "sk-very-secret-value" not in client.get("/api/settings").text


def test_settings_changes_require_same_origin_and_csrf(client):
    _, headers = headers_for(client)
    body = {"values": {"home_location": "Kochi"}}
    assert client.post("/api/settings", json=body).status_code == 403
    assert client.post("/api/settings", json=body, headers={**headers, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/settings", json=body, headers={**headers, "X-Archer-CSRF": "wrong"}).status_code == 403
    response = client.post("/api/settings", json=body, headers=headers)
    assert response.status_code == 200 and response.json()["changed"] == ["home_location"]
    bad = client.post("/api/settings", json={"values": {"port": "80"}}, headers=headers)
    assert bad.status_code == 422 and "port" in bad.json()["errors"]


def test_restart_needs_the_tray_launcher(client):
    _, headers = headers_for(client)
    assert client.post("/api/settings/restart", headers=headers).status_code == 400


def test_setup_moves_into_settings(client):
    response = client.get("/setup", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/app#browser"


def test_connection_checks_spend_nothing():
    asyncio.run(connection_checks())


async def connection_checks():
    seen = []
    def reply(request):
        seen.append(str(request.url))
        if request.url.path == "/config":
            return httpx.Response(200, json={"engines": [{"enabled": True}, {"enabled": False}], "autocomplete": "duckduckgo"})
        if request.url.path == "/models":
            return httpx.Response(401 if request.headers["authorization"].endswith("bad") else 200,
                                  json={"data": [{"id": "deepseek-flash"}]})
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        assert (await check_connection(http, "searxng", "http://searx.test"))["message"] == "Connected · 1 engine enabled · suggestions via duckduckgo"
        good = await check_connection(http, "ai", "good", "https://api.test")
        assert good["ok"] and good["models"] == ["deepseek-flash"]
        assert not (await check_connection(http, "ai", "bad", "https://api.test"))["ok"]
        # Only metadata endpoints are used: never a search query or a chat completion.
        assert all(url.endswith(("/config", "/models")) for url in seen)
