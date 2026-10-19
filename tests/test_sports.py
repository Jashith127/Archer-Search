"""Club, league and esports panels on recorded ESPN, VLR.gg and Liquipedia replies (tests/fixtures)."""

import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from archer.app import app
from archer.config import settings
from archer.searxng import SearXNG
from archer.sources import esports, sports, wiki

BASE = f"http://{settings.public_host}:{settings.port}"
_spec = importlib.util.spec_from_file_location("sports_fixture", Path(__file__).with_name("sports_browser_server.py"))
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)
FIXTURES = Path(__file__).with_name("fixtures")


def item(qid):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "wikibase-entityid", "value": {"id": qid}}}, "rank": "normal"}


@pytest.fixture
def client(monkeypatch):
    for cache in (wiki._entity_cache, sports._cache, esports._cache):
        cache._data.clear()
    monkeypatch.setitem(esports._last, "api", -1e9)
    monkeypatch.setitem(esports._last, "parse", -1e9)
    with TestClient(app, base_url=BASE) as c:
        mock = httpx.AsyncClient(transport=httpx.MockTransport(fixture.reply))
        app.state.searx = SearXNG(httpx.AsyncClient(base_url=settings.searxng_url, transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"results": [], "suggestions": []}))))
        app.state.ext = mock
        yield c


def test_kinds_for_clubs_leagues_and_esports():
    from archer.sources.wiki import _kind
    assert _kind({"claims": {"P31": [item("Q476028")]}}) == "club"
    assert _kind({"claims": {"P31": [item("Q13393265")]}}) == "club"
    assert _kind({"claims": {"P31": [item("Q15991303")]}}) == "league"
    assert _kind({"id": "Q82595", "claims": {"P31": [item("Q135641761")]}}) == "league"     # Bundesliga's newer class
    assert _kind({"claims": {"P31": [item("Q61974339")]}}) == "esteam"
    assert _kind({"claims": {"P31": [item("Q5")], "P106": [item("Q4379701")]}}) == "esplayer"
    assert _kind({"claims": {"P31": [item("Q5")], "P106": [item("Q937857")], "P641": [item("Q2736")]}}) == "person"


def test_club_names_match_espn_spellings():
    teams = [t["team"] for t in __import__("json").loads((FIXTURES / "espn" / "soccer_eng.1_teams.json").read_bytes())
             ["sports"][0]["leagues"][0]["teams"]]
    name = lambda title: (sports.match(title, teams) or {}).get("displayName")  # noqa: E731
    assert name("Arsenal F.C.") == "Arsenal" and name("A.F.C. Bournemouth") == "AFC Bournemouth"
    assert name("Brighton & Hove Albion F.C.") == "Brighton & Hove Albion"
    assert name("Manchester United F.C.") == "Manchester United" and name("Manchester") is None


def test_club_api_has_table_form_fixtures_and_squad(client):
    d = client.get("/api/sport/club", params={"title": "Arsenal F.C.", "league": "Q9448"}).json()
    assert d["team"]["abbr"] == "ARS" and d["team"]["color"] == "#e20520" and d["position"] == 2
    assert len(d["table"]) == 20 and [r["rank"] for r in d["ladder"]] == [1, 2, 3, 4, 5] and d["ladder"][1]["me"]
    assert {z["label"] for z in d["zones"]} == {"Champions League", "Europa League", "Relegation"}
    assert d["record"]["points"] == 15 and len(d["form"]) == 5
    assert d["results"][-1]["outcome"] in "WDL" and d["fixtures"][0]["opp"] == "Nottingham Forest"
    assert [g["label"] for g in d["squad"]] == ["Goalkeepers", "Defenders", "Midfielders", "Forwards"]


def test_nba_team_gets_its_conference(client):
    d = client.get("/api/sport/club", params={"title": "Boston Celtics", "league": "Q155223"}).json()
    assert d["sport"] == "basketball" and d["league"]["group"] == "Eastern Conference" and d["position"] == 1
    assert d["record"]["pct"] == "1.000" and d["fixtures"]


def test_club_api_is_empty_for_unknown_clubs_and_when_espn_is_down(client):
    assert client.get("/api/sport/club", params={"title": "Nobody United", "league": "Q9448"}).json() == {}
    assert client.get("/api/sport/club", params={"title": "Arsenal F.C.", "league": "nope"}).json() == {}
    sports._cache._data.clear()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert client.get("/api/sport/club", params={"title": "Arsenal F.C.", "league": "Q9448"}).json() == {}
    assert client.get("/api/sport/league", params={"qid": "Q9448"}).json() == {}


def test_league_api_has_table_and_round(client):
    d = client.get("/api/sport/league", params={"qid": "Q9448"}).json()
    assert d["name"] == "Premier League" and len(d["tables"][0]["rows"]) == 20 and d["games"]
    assert d["games"][0]["home"]["name"] and d["leader"]["rank"] == 1


def test_club_and_league_panels_render(client):
    club = client.get("/search", params={"q": "arsenal"}).text
    assert 'class="hero hero-entity hero-club is-football"' in club and 'data-club-league="Q9448"' in club
    assert "Emirates Stadium" in club and "data-sp-squad" in club
    league = client.get("/search", params={"q": "premier league"}).text
    assert "hero-league" in league and 'data-league="Q9448"' in league


def test_valorant_team_from_vlr(client):
    d = client.get("/api/esports", params={"title": "Sentinels (esports)", "role": "team", "game": "valorant"}).json()
    assert (d["name"], d["tag"], d["rating"], d["record"]) == ("Sentinels", "SEN", 1684, {"w": 82, "l": 58})
    assert len(d["players"]) == 5 and any(p["captain"] for p in d["players"]) and d["staff"]
    series = d["results"][0]
    assert series["opp"] == "KRÜ Esports" and (series["us"], series["them"], series["outcome"]) == (0, 2, "L")
    assert len(series["maps"][0]["agents"]) == 5 and series["maps"][0]["agents"][0]["role"] == "controller"
    assert d["maps"][0]["map"] == "Haven" and d["maps"][0]["played"] >= d["maps"][-1]["played"]
    assert d["history"] and d["history"][-1]["rating"] == 1684 and d["winnings"] == "$1,197,000"


def test_valorant_player_from_vlr(client):
    d = client.get("/api/esports", params={"title": "TenZ", "role": "player", "game": "valorant"}).json()
    assert d["alias"] == "TenZ" and d["country"] == "Canada" and d["agents"][0]["name"] == "Jett"
    assert d["career"]["roles"][0]["role"] == "duelist" and 1 < d["career"]["rating"] < 1.3
    assert d["results"][0]["maps"][0]["rating"] and d["placements"][0]["rank"] == 1


def test_other_games_come_from_liquipedia(client):
    t1 = client.get("/api/esports", params={"title": "T1 (esports)", "role": "team", "game": "lol"}).json()
    assert t1["league"] == "LCK" and [p["alias"] for p in t1["roster"]][:3] == ["Doran", "Oner", "Faker"]
    assert t1["roster"][0]["joined"] == "2024-11-19" and t1["roster"][0]["flag"].endswith("/kr.svg")
    faker = client.get("/api/esports", params={"title": "Faker (gamer)", "role": "player", "game": "lol"}).json()
    assert faker["team"] == "T1" and faker["position"] == "Mid" and len(faker["titles"]) == 6


def test_esports_panels_pick_valorant_or_the_generic_one(client):
    sen = client.get("/search", params={"q": "sentinels"}).text
    assert "hero-val is-team" in sen and 'data-val-title="Sentinels (esports)"' in sen
    tenz = client.get("/search", params={"q": "tenz"}).text
    assert "hero-val is-player" in tenz
    faker = client.get("/search", params={"q": "faker"}).text
    assert "hero-es is-player" in faker and 'data-es-game="lol"' in faker


def test_games_in_puts_valorant_first():
    assert esports.games_in("American esports organization", "It fields teams in Fortnite and Valorant.") == ["valorant", "fortnite"]
    assert esports.games_in("South Korean League of Legends player") == ["lol"]
    assert esports.handle("Faker (gamer)") == "Faker" and esports.handle("Sentinels (esports)") == "Sentinels"


def test_liquipedia_parse_is_skipped_rather_than_queued(client, monkeypatch):
    """Liquipedia asks for 30 s between rendered pages: a second roster that soon comes back empty."""
    import time
    monkeypatch.setitem(esports._last, "parse", time.monotonic())
    out = asyncio.run(esports._liquipedia(app.state.ext, "leagueoflegends", "parse", action="parse", title="T1"))
    assert out == {}


def test_vct_searches_name_a_season():
    from datetime import date
    from archer.query import classify, vct
    today = date(2026, 10, 18)
    assert vct("vct 2026", today) | {} == {"year": 2026, "this_year": 2026, "event": "", "region": "", "named_year": True}
    assert vct("vct", today)["year"] == 2026 and vct("vct", today)["named_year"] is False
    assert vct("valorant champions 2024", today)["event"] == "champions"
    assert (vct("vct 2025 emea stage 2", today)["event"], vct("vct 2025 emea stage 2", today)["region"]) == ("stage2", "EMEA")
    assert vct("vct sentinels", today) is None and vct("vct 2040", today) is None and vct("valorant", today) is None
    assert classify("vct 2024").kind == "vct"


def test_vct_season_opens_on_the_latest_event_or_a_past_seasons_champions(client):
    this = client.get("/api/vct/season", params={"year": 2026}).json()
    names = {g["name"]: [e["short"] for e in g["events"]] for g in this["groups"]}
    assert list(names)[:2] == ["Kickoff", "Masters"] and names["Champions"] == ["Champions"]
    assert names["Masters"] == ["London", "Santiago"] and len(names["Game Changers"]) <= 6
    assert "Championship São Paulo" in names["Game Changers"]
    assert this["pick"] == "2766"                                            # Champions 2026, just finished
    assert client.get("/api/vct/season", params={"year": 2026, "event": "masters"}).json()["pick"] == "2765"
    assert client.get("/api/vct/season", params={"year": 2026, "event": "stage2", "region": "EMEA"}).json()["pick"] == "2976"
    assert client.get("/api/vct/season", params={"year": 2024}).json()["pick"] == "2097"
    assert client.get("/api/vct/season", params={"year": 2025}).json() == {}   # no hub recorded


def test_vct_event_has_its_champion_final_and_standings(client):
    ev = client.get("/api/vct/event", params={"id": "2097"}).json()
    assert ev["name"] == "Valorant Champions 2024" and ev["done"] and ev["champion"]["team"] == "EDward Gaming"
    f = ev["final"]
    assert f["final"] and (f["a"]["score"], f["b"]["score"]) == (3, 2) and f["b"]["name"] == "Team Heretics"
    assert [s["rank"] for s in ev["standings"]][:4] == [1, 2, 3, 4] and ev["standings"][0]["prize"] == "$1,000,000"
    assert client.get("/api/vct/event", params={"id": "x"}).status_code == 422
    assert client.get("/api/vct/event", params={"id": "99"}).json() == {}


def test_vct_panel_renders_and_placements_keep_the_final_stage(client):
    html = client.get("/search", params={"q": "vct 2024"}).text
    assert "hero-vct" in html and 'data-vct-year="2024"' in html and "VCT 2024" in html
    team = client.get("/api/esports", params={"title": "Sentinels (esports)", "role": "team", "game": "valorant"}).json()
    first = team["placements"][0]
    assert (first["stage"], first["place"], first["rank"]) == ("Group Stage", "7th–8th", 7)


def test_a_game_name_after_a_handle_finds_the_esports_panel(client):
    from archer.query import classify
    q = classify("tenz valorant")
    assert (q.entity_candidate, q.entity_hint) == ("tenz", ("esports:valorant", "esports"))
    html = client.get("/search", params={"q": "tenz valorant"}).text
    assert "hero-val is-player" in html
    # An organisation known for another game is drawn for the one the search names.
    t1 = client.get("/search", params={"q": "t1 valorant"}).text
    assert "hero-val is-team" in t1 and 'data-val-title="T1 (esports)"' in t1
    assert "hero-es is-team" in client.get("/search", params={"q": "t1 esports"}).text


def test_an_organisation_naming_no_game_is_checked_on_vlr(client, monkeypatch):
    """Gen.G's article says only "esports organization": VLR.gg having a team of that name makes it Valorant."""
    asked = []

    async def on_vlr(_client, title, role):
        asked.append((title, role))
        return title == "Gen.G"
    monkeypatch.setattr(esports, "on_vlr", on_vlr)
    monkeypatch.setitem(fixture.PAGES, "Gen.G", ("Q24884615", "Esports organization", "logo", "Gen.G is a professional esports organization."))
    data = asyncio.run(wiki.lookup(app.state.ext, "Gen.G"))
    assert data["kind"] == "esteam" and data["esports"]["game"] == "valorant" and asked == [("Gen.G", "team")]


def test_a_sparse_esports_panel_is_compact(client, monkeypatch):
    async def on_vlr(*_):
        return False
    monkeypatch.setattr(esports, "on_vlr", on_vlr)
    monkeypatch.setitem(fixture.PAGES, "Gen.G", ("Q24884615", "Esports organization", "logo", "Gen.G is a professional esports organization."))
    html = client.get("/search", params={"q": "gen.g"}).text
    assert "hero-es is-team" in html and "data-compact" in html and "data-es-info" not in html


def test_vct_opens_on_champions_once_it_has_begun_and_never_on_game_changers_unasked():
    ev = lambda i, kind, status: {"id": i, "kind": kind, "status": status, "name": i}  # noqa: E731
    season = [ev("gc", "gc", "ongoing"), ev("champs", "champions", "completed"), ev("stage2", "stage2", "completed")]
    assert esports._pick(season, 2026, 2026) == "champs"
    before = [ev("gc", "gc", "ongoing"), ev("champs", "champions", "upcoming"), ev("stage2", "stage2", "completed")]
    assert esports._pick(before, 2026, 2026) == "stage2"
    assert esports._pick(before, 2026, 2026, "gc") == "gc"
    assert esports._pick([ev("m", "masters", "ongoing"), ev("champs", "champions", "upcoming")], 2026, 2026) == "m"


def test_vct_finalists_carry_logo_country_and_flag(client):
    f = client.get("/api/vct/event", params={"id": "2766"}).json()["final"]
    assert f["a"]["name"] == "LOUD" and f["a"]["logo"] == "/api/vlr/img/62bbec8dc1b9f.png" and f["a"]["country"] == "Brazil"
    assert f["b"]["name"] == "Team Vitality" and f["b"]["flag"] == "https://flagcdn.com/eu.svg"


def test_vlr_images_are_fetched_by_archer_not_the_browser(client):
    """owcdn.net turns away a browser that sends no Referer: logos are handed out as /api/vlr/img paths."""
    ev = client.get("/api/vct/event", params={"id": "2766"}).json()
    assert all(s["logo"].startswith("/api/vlr/img/") for s in ev["standings"] if s["logo"])
    asked = []

    def cdn(request):
        asked.append(str(request.url))
        return httpx.Response(200, content=b"PNG fake", headers={"content-type": "image/png"})
    esports._images._data.clear()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(cdn))
    r = client.get("/api/vlr/img/62bbec8dc1b9f.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "max-age" in r.headers["cache-control"]
    assert asked == ["https://owcdn.net/img/62bbec8dc1b9f.png"]
    client.get("/api/vlr/img/62bbec8dc1b9f.png")
    assert len(asked) == 1                                                   # kept, not fetched again
    assert client.get("/api/vlr/img/..%2F..%2Fsecret.png").status_code == 404
    assert client.get("/api/vlr/img/notanimage.exe").status_code == 404 and len(asked) == 1


def test_lockin_is_the_season_opener():
    from datetime import date
    from archer.query import vct
    assert vct("vct lockin 2025", date(2026, 10, 18))["event"] == "kickoff"
    assert vct("vct lock//in 2023", date(2026, 10, 18))["year"] == 2023
    assert esports.vct_kind("VCT LOCK//IN São Paulo") == "kickoff"
