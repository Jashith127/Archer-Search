"""Render every hero template against canned upstream data, so template bugs fail here, not in the browser."""

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from archer.app import app
from archer.config import settings
from archer.searxng import SearXNG
from archer.sources import aimodels, crypto, dictionary, fx, modelprices, places, translate, weather, wiki

BASE = f"http://{settings.public_host}:{settings.port}"
EMPTY_SEARCH = {"results": [], "answers": [], "infoboxes": [], "suggestions": [], "corrections": [], "unresponsive_engines": []}

GEO = {"results": [{"name": "Bengaluru", "admin1": "Karnataka", "country": "India", "latitude": 12.97,
                    "longitude": 77.59, "timezone": "Asia/Kolkata", "population": 8443675, "feature_code": "PPLA"}]}
HOURS = [f"2026-10-13T{h:02d}:00" for h in range(24)] + [f"2026-10-14T{h:02d}:00" for h in range(24)]
FORECAST = {
    "timezone": "Asia/Kolkata",
    "current": {"time": "2026-10-13T21:15", "temperature_2m": 24.2, "apparent_temperature": 25.1,
                "relative_humidity_2m": 65, "precipitation": 0, "weather_code": 1, "wind_speed_10m": 9.6, "is_day": 0},
    "hourly": {"time": HOURS, "temperature_2m": [20 + i % 10 for i in range(48)],
               "precipitation_probability": [i % 100 for i in range(48)], "weather_code": [3] * 48},
    "daily": {"time": [f"2026-10-{13 + i}" for i in range(7)], "weather_code": [3, 61, 63, 95, 0, 2, 45],
              "temperature_2m_max": [30] * 7, "temperature_2m_min": [20] * 7,
              "precipitation_probability_max": [0, 22, 97, 95, 87, 80, 63],
              "sunrise": ["2026-10-13T06:09"] * 7, "sunset": ["2026-10-13T18:02"] * 7},
}
WIKTIONARY = {"en": [{"partOfSpeech": "Noun", "definitions": [
    {"definition": "A <a href='/wiki/x'>fortunate</a> discovery.", "parsedExamples": [{"example": "Pure <b>serendipity</b>."}]}]}]}
WP_PAGE = {"query": {"pages": [{"title": "Zendaya", "fullurl": "https://en.wikipedia.org/wiki/Zendaya",
                                "description": "American actress and singer", "extract": "Zendaya is an actress.",
                                "pageprops": {"wikibase_item": "Q189489"},
                                "original": {"source": "https://upload.wikimedia.org/z.jpg", "width": 600, "height": 900}}]}}


def item(qid):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "wikibase-entityid", "value": {"id": qid}}}, "rank": "normal"}


WD_ENTITY = {"entities": {"Q189489": {"claims": {
    "P31": [item("Q5")], "P106": [item("Q33999")], "P19": [item("Q17042")], "P26": [item("Q2023710")],
    "P569": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": "+1996-09-01T00:00:00Z", "precision": 11}}}, "rank": "normal"}],
    "P2048": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "quantity", "value": {"amount": "+178", "unit": "http://www.wikidata.org/entity/Q174728"}}}, "rank": "normal"}],
    "P2003": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": "zendaya"}}, "rank": "normal"}],
    "P2002": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": "zendaya"}}, "rank": "normal"}],
    "P2013": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": "zendaya"}}, "rank": "normal"}],
}}}}
WD_LABELS = {"entities": {
    "Q17042": {"labels": {"en": {"value": "Oakland"}}},
    "Q33999": {"labels": {"en": {"value": "actor"}}},
    "Q2023710": {"labels": {"mul": {"value": "Tom Holland"}}},
}}


COINGECKO = [{"name": "Bitcoin", "symbol": "btc", "image": "https://img/btc.png", "current_price": 7907309,
              "price_change_percentage_24h": 0.39, "price_change_percentage_7d_in_currency": -1.2, "market_cap_rank": 1,
              "market_cap": 1.57e14, "total_volume": 2.1e12, "high_24h": 7950000, "low_24h": 7800000,
              "ath": 11187013, "ath_date": "2025-10-06T00:00:00Z", "circulating_supply": 19900000,
              "sparkline_in_7d": {"price": [7.8e6 + i * 1000 for i in range(168)]}, "last_updated": "2026-10-13T16:50:00Z"}]
MYMEMORY = {"responseStatus": 200, "responseData": {"translatedText": "ಧನ್ಯವಾದಗಳು", "detectedLanguage": "en-GB"},
            "matches": [{"translation": "ಧನ್ಯವಾದ"}]}
def photon_feature(name, city, state, lon, lat, kind="suburb"):
    return {"properties": {"name": name, "city": city, "state": state, "osm_value": kind},
            "geometry": {"coordinates": [lon, lat]}}


PHOTON = {"features": [
    photon_feature("Indiranagar", "Edappadi", "Tamil Nadu", 77.87, 11.60, "village"),   # same name, 150 km away
    photon_feature("Indiranagar", "Bengaluru", "Karnataka", 77.64, 12.97),
]}
OVERPASS = {"elements": [
    {"type": "node", "id": 1, "lat": 12.971, "lon": 77.641, "tags": {"name": "Third Wave Coffee", "amenity": "cafe",
     "opening_hours": "Mo-Su 08:00-23:00", "website": "https://thirdwavecoffee.in", "addr:street": "100 Feet Road"}},
    {"type": "way", "id": 2, "center": {"lat": 12.975, "lon": 77.645}, "tags": {"name": "Dyu Art Cafe", "amenity": "cafe"}},
    {"type": "node", "id": 3, "lat": 12.98, "lon": 77.65, "tags": {"amenity": "cafe"}},   # unnamed: dropped
]}
SUN = {"timezone": "Asia/Kolkata", "daily": {"time": ["2026-10-13", "2026-10-14"], "sunrise": ["2026-10-13T06:09", "2026-10-14T06:09"],
                                           "sunset": ["2026-10-13T18:02", "2026-10-14T18:01"], "daylight_duration": [42780, 42700]}}


def upstream(request: httpx.Request) -> httpx.Response:
    host, path, params = request.url.host, request.url.path, request.url.params
    if host == "geocoding-api.open-meteo.com":
        return httpx.Response(200, json=GEO)
    if host == "api.open-meteo.com":
        return httpx.Response(200, json=SUN if "daylight_duration" in params.get("daily", "") else FORECAST)
    if host == "api.coingecko.com":
        return httpx.Response(200, json=COINGECKO)
    if host == "api.mymemory.translated.net":
        return httpx.Response(200, json=MYMEMORY)
    if host == "photon.komoot.io":
        return httpx.Response(200, json=PHOTON)
    if host in ("overpass-api.de", "maps.mail.ru"):
        return httpx.Response(200, json=OVERPASS)
    if host == "en.wiktionary.org":
        return httpx.Response(200, json=WIKTIONARY)
    if host == "api.dictionaryapi.dev":
        return httpx.Response(200, json=[{"phonetic": "/ˌsɛɹ.ənˈdɪp.ɪ.ti/", "phonetics": []}])
    if host == "en.wikipedia.org":
        return httpx.Response(200, json=WP_PAGE)
    if host == "www.wikidata.org":
        return httpx.Response(200, json=WD_ENTITY if params.get("props", "").startswith("claims") else WD_LABELS)
    return httpx.Response(404)


@pytest.fixture
def client(tmp_path, monkeypatch):
    # No model catalog on disk, so model lookups never read the user's cached Epoch data.
    monkeypatch.setattr(aimodels, "CACHE_FILE", tmp_path / "epoch.zip")
    monkeypatch.setattr(aimodels, "_catalog", None)
    monkeypatch.setattr(aimodels, "_failed_at", 0.0)
    monkeypatch.setattr(modelprices, "CACHE_FILE", tmp_path / "prices.json")
    for name, value in (("_table", None), ("_loaded_at", 0.0), ("_failed_at", 0.0), ("_refresh", None)):
        monkeypatch.setattr(modelprices, name, value)
    fx._cache.clear()
    monkeypatch.setattr(settings, "home_currency", "inr")  # other suites apply settings live
    for cache in (weather._geo_cache, weather._wx_cache, dictionary._cache, wiki._entity_cache,
                  crypto._cache, translate._cache, places._area_cache, places._places_cache):
        cache._data.clear()
    with TestClient(app, base_url=BASE) as c:
        searx = httpx.AsyncClient(base_url=settings.searxng_url,
                                  transport=httpx.MockTransport(lambda r: httpx.Response(200, json=EMPTY_SEARCH)))
        app.state.searx = SearXNG(searx)
        app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        yield c


def test_weather_hero(client):
    html = client.get("/search", params={"q": "weather bengaluru"}).text
    assert "hero-weather" in html and "Bengaluru, Karnataka, India" in html and "97%" in html


def test_time_hero(client):
    html = client.get("/search", params={"q": "time in bengaluru"}).text
    assert "hero-time" in html and 'data-clock-tz="Asia/Kolkata"' in html


def test_define_hero(client):
    html = client.get("/search", params={"q": "define serendipity"}).text
    hero = html.split("hero-define")[1].split("</section>")[0]
    assert "A fortunate discovery." in hero and "Pure serendipity." in hero
    assert "<b>" not in hero and "<a href='/wiki" not in hero   # Wiktionary HTML is stripped


def test_entity_hero(client):
    html = client.get("/search", params={"q": "zendaya"}).text
    assert "hero-entity--person" in html
    assert "Tom Holland" in html          # 'mul' label fallback
    assert "178 cm" in html and "Oakland" in html and "Instagram" in html
    assert 'data-films-qid="Q189489"' in html and "data-works-qid" not in html   # poster grid, not the old row


def test_typo_follows_wikipedia_suggestion(client):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if request.url.host == "en.wikipedia.org":
            if p.get("titles") == "zendeya":
                return httpx.Response(200, json={"query": {"pages": [{"title": "Zendeya", "missing": True}]}})
            if p.get("gsrsearch") == "zendeya":
                return httpx.Response(200, json={"query": {"searchinfo": {"suggestion": "zendaya"}}})
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    html = client.get("/search", params={"q": "zendeya"}).text
    assert "hero-entity--person" in html and "Did you mean" in html and "Tom Holland" in html


def test_wide_logo_is_not_cropped_as_poster(client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "en.wikipedia.org":
            page = {**WP_PAGE["query"]["pages"][0], "title": "BoJack Horseman",
                    "original": {"source": "https://upload.wikimedia.org/BoJack_Horseman_Logo.svg", "width": 629, "height": 234}}
            return httpx.Response(200, json={"query": {"pages": [page]}})
        if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
            return httpx.Response(200, json={"entities": {"Q189489": {"claims": {"P31": [item("Q5398426")]}}}})
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    html = client.get("/search", params={"q": "bojack horseman"}).text
    assert "hero-entity--tv" in html and "is-logo" in html and "is-poster" not in html


def test_film_uses_wikidata_art_when_wikipedia_has_no_page_image():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "en.wikipedia.org":
            page = {**WP_PAGE["query"]["pages"][0], "title": "Example Film"}
            page.pop("original", None)
            return httpx.Response(200, json={"query": {"pages": [page]}})
        if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
            claims = {"P31": [item("Q11424")], "P18": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": "Example poster.jpg"}}, "rank": "normal"}]}
            return httpx.Response(200, json={"entities": {"Q189489": {"claims": claims}}})
        return upstream(request)

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as ext:
            return await wiki.lookup(ext, "Example Film")

    wiki._entity_cache._data.clear()
    result = asyncio.run(fetch())
    assert result["image"] == "https://commons.wikimedia.org/wiki/Special:FilePath/Example%20poster.jpg?width=800"


def test_music_has_separate_video_spotify_and_lyrics_cards(client):
    def string_claim(value):
        return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": value}}, "rank": "normal"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "en.wikipedia.org":
            page = {**WP_PAGE["query"]["pages"][0], "title": "Example Song"}
            return httpx.Response(200, json={"query": {"pages": [page]}})
        if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
            claims = {"P31": [item("Q7366")], "P1651": [string_claim("abcdefghijk")],
                      "P2207": [string_claim("1234567890ABCDEFGHIJKL")]}
            return httpx.Response(200, json={"entities": {"Q189489": {"claims": claims}}})
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    wiki._entity_cache._data.clear()
    html = client.get("/search", params={"q": "Example Song"}).text
    assert "player--video" in html and "music-embed--spotify" in html
    assert "music-service--spotify" not in html
    assert "music-lyrics" in html and "Example+Song+lyrics" in html
    assert "<iframe" not in html


def test_song_without_spotify_id_has_discovery_card_not_formed_fact(client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "en.wikipedia.org":
            page = {**WP_PAGE["query"]["pages"][0], "title": "Ghost Town (Kanye West song)"}
            return httpx.Response(200, json={"query": {"pages": [page]}})
        if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
            claims = {"P31": [item("Q7366")], "P1651": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": "abcdefghijk"}}, "rank": "normal"}],
                      "P571": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": "+2014-01-01T00:00:00Z", "precision": 11}}}, "rank": "normal"}]}
            return httpx.Response(200, json={"entities": {"Q189489": {"claims": claims}}})
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    wiki._entity_cache._data.clear()
    html = client.get("/search", params={"q": "Ghost Town"}).text
    assert "music-service--spotify" in html and "Search on Spotify" in html
    assert "Formed" not in html


@pytest.mark.parametrize("q, marker", [
    ("#1a73e8", "hero-color"), ("1760000000", "hero-time"), ("flip a coin", "hero-random"),
    ("100 f to c", "hero-convert"), ("2^10", "hero-calc"), ("timer 5 min", "hero-timer"),
    ("stopwatch", "hero-timer"), ("metronome 90", "hero-metronome"), ("emi calculator", "hero-finance"),
    ("bmi", "hero-finance"), ("tip calculator", "hero-finance"), ("solve 2x+3=11", "hero-solve"),
    ("y=x^2", "hero-graph"), ("track EE123456789IN", "hero-track"),
])
def test_local_heroes(client, q, marker):
    assert marker in client.get("/search", params={"q": q}).text


def test_crypto_hero(client):
    html = client.get("/search", params={"q": "bitcoin price"}).text
    assert "hero-crypto" in html and "₹79,07,309.00" in html    # lakh/crore digit grouping
    assert "₹157.00 L Cr" in html and "Rank #1" in html and 'class="cx-line"' in html


def test_translate_hero(client):
    html = client.get("/search", params={"q": "thank you in kannada"}).text
    assert "hero-translate" in html and "ಧನ್ಯವಾದಗಳು" in html and "English" in html


def test_places_hero_renders_immediately(client):
    html = client.get("/search", params={"q": "cafes in indiranagar"}).text
    assert "hero-places" in html and 'data-places-cat="cafe"' in html and 'data-places-area="indiranagar"' in html
    assert "place--skeleton" in html   # places arrive via /api/places; Overpass must not hold the page


def test_places_api(client):
    data = client.get("/api/places", params={"cat": "cafe", "area": "indiranagar"}).json()
    names = [p["name"] for p in data["places"]]
    assert names == ["Third Wave Coffee", "Dyu Art Cafe"]          # richer + nearer first; unnamed dropped
    assert data["area"] == "Indiranagar, Bengaluru, Karnataka"      # nearest-to-home of the same-named places
    assert data["places"][0]["hours"] == "Mo-Su 08:00-23:00"


def test_places_api_rejects_unknown_category(client):
    assert client.get("/api/places", params={"cat": '"];out;', "area": "x"}).status_code == 404


def test_places_falls_back_to_photon_when_overpass_is_down(client):
    def handler(request):
        if request.url.host in ("overpass-api.de", "maps.mail.ru"):
            raise httpx.ReadTimeout("slow")
        if request.url.host == "photon.komoot.io" and request.url.params.get("osm_tag") == "amenity:cafe":
            poi = photon_feature("Cafe Muziris", "Bengaluru", "Karnataka", 77.6359, 12.9689)
            poi["properties"].update(street="Indiranagar Double Road", osm_type="N", osm_id=42)
            return httpx.Response(200, json={"features": [poi, poi]})       # duplicate is dropped
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    data = client.get("/api/places", params={"cat": "cafe", "area": "indiranagar"}).json()
    assert data["source"] == "photon" and [p["name"] for p in data["places"]] == ["Cafe Muziris"]


def test_places_api_reports_total_failure(client):
    def handler(request):
        if request.url.host in ("overpass-api.de", "maps.mail.ru") or request.url.params.get("osm_tag"):
            raise httpx.ReadTimeout("slow")
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert "didn't answer" in client.get("/api/places", params={"cat": "cafe", "area": "indiranagar"}).json()["error"]


def test_country_leads_with_flag_and_about_is_inline(client):
    def handler(request):
        if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
            flag = [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": "Flag of India.svg"}}, "rank": "normal"}]
            return httpx.Response(200, json={"entities": {"Q189489": {"claims": {
                "P31": [item("Q6256")], "P36": [item("Q17042")], "P41": flag}}}})
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    html = client.get("/search", params={"q": "india"}).text
    assert 'class="collage-main collage-flag"' in html
    assert "Special:FilePath/Flag%20of%20India.svg" in html
    # One fact only (Capital): sparse layout, About moves to the right of the media.
    assert 'class="about-side"' in html and 'class="aside"' not in html and "is-sparse" in html


def test_sparse_topic_shows_types_as_tags(client):
    def handler(request):
        if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
            return httpx.Response(200, json={"entities": {"Q189489": {"claims": {"P31": [item("Q95074"), item("Q15773347")]}}}})
        if request.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"entities": {"Q95074": {"labels": {"en": {"value": "fictional human"}}},
                                                          "Q15773347": {"labels": {"en": {"value": "television character"}}}}})
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    html = client.get("/search", params={"q": "red john"}).text
    assert 'class="tag-chip"' in html and "fictional human" in html and "television character" in html
    assert "is-sparse" in html and 'class="tiles"' not in html     # no giant lone "Type" tile
    assert 'href="#u-flag"' in html or "hero-badge-icon" in html


def test_entity_badges_and_tile_icons(client):
    html = client.get("/search", params={"q": "zendaya"}).text
    assert "Person" in html and 'href="#u-calendar"' in html          # Age tile icon
    assert 'href="#b-instagram"' in html                                  # brand logo on link chip
    assert "tile--lead" in html
    assert "Facebook" not in html                                          # dropped from link chips
    assert '<details class="about" open>' in html                          # rich panel: About below, expanded
    x_chip = html.split('link-chip--x', 1)[1].split("</a>", 1)[0]
    assert 'aria-label="X"' in x_chip and ">X<" not in x_chip               # X is logo-only


def test_results_header_has_pixel_mark(client):
    html = client.get("/search", params={"q": "zendaya"}).text
    assert 'class="brand"' in html and 'class="kbd"' not in html
    brand = html.split('class="brand"', 1)[1].split("</a>", 1)[0]
    assert brand.count('class="px-on"') == 23 and brand.count('class="px-off"') == 26   # full 7x7 matrix


@pytest.mark.parametrize("host, upstream_reply, status", [
    ("example.com", httpx.Response(200, content=b"\x00\x00\x01\x00", headers={"content-type": "image/x-icon"}), 200),
    ("example.com", httpx.Response(200, content=b"<svg onload=alert(1)>", headers={"content-type": "image/svg+xml"}), 404),
    ("example.com", httpx.Response(404), 404),
    ("not a host", None, 404),
])
def test_favicon_proxy(client, host, upstream_reply, status):
    from archer import app as app_module
    app_module._favicons._data.clear()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: upstream_reply))
    resp = client.get(f"/api/favicon/{host}")
    assert resp.status_code == status
    if status == 200:
        assert resp.headers["x-content-type-options"] == "nosniff" and "sandbox" in resp.headers["content-security-policy"]


def test_favicon_falls_back_to_duckduckgo(client):
    from archer import app as app_module
    app_module._favicons._data.clear()
    icon = httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(404) if r.url.host == "www.google.com" else icon))
    assert client.get("/api/favicon/minimax.io").status_code == 200


def test_software_detected_by_properties(client):
    def handler(request):
        if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
            version = [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": "3.15.0"}}, "rank": "preferred"}]
            return httpx.Response(200, json={"entities": {"Q189489": {"claims": {"P31": [item("Q12772052")], "P348": version}}}})
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    html = client.get("/search", params={"q": "python"}).text
    assert "hero-entity--software" in html and "3.15.0" in html


def test_places_area_spelling_from_results(client):
    reply = {**EMPTY_SEARCH, "results": [
        {"url": f"https://example.com/{i}", "title": "Best cafes in Indiranagar Bangalore", "content": "Indiranagar is…",
         "engines": ["duckduckgo"]} for i in range(3)]}
    app.state.searx = SearXNG(httpx.AsyncClient(base_url=settings.searxng_url,
                                                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply))))
    html = client.get("/search", params={"q": "cafes in indranagar"}).text
    assert 'data-places-area="Indiranagar"' in html and "you typed “indranagar”" in html


def test_failed_source_shows_notice(client):
    def handler(request):
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(503)
        return upstream(request)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    html = client.get("/search", params={"q": "weather bengaluru"}).text
    assert "Open-Meteo didn't respond" in html and "hero-weather" not in html


def test_sun_hero(client):
    html = client.get("/search", params={"q": "sunset bengaluru"}).text
    assert "hero-sun" in html and "18:02" in html and "11 h 53 min" in html


def test_quick_answer_for_questions(client):
    reply = {**EMPTY_SEARCH, "answers": [{"engine": "duckduckgo", "url": "https://en.wikipedia.org/wiki/Kneading_(cats)",
                                         "answer": "Kneading is a behavior frequently observed in domestic cats."}]}
    app.state.searx = SearXNG(httpx.AsyncClient(base_url=settings.searxng_url,
                                                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=reply))))
    html = client.get("/search", params={"q": "why do cats knead"}).text
    assert "hero-answer" in html and "Kneading is a behavior" in html


def test_country_and_chemical_kinds(client):
    def entity_with(claims):
        def handler(request):
            if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
                return httpx.Response(200, json={"entities": {"Q189489": {"claims": claims}}})
            return upstream(request)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def string(v):
        return [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": v}}, "rank": "normal"}]

    app.state.ext = entity_with({"P31": [item("Q11173")], "P274": string("H2O")})
    assert "H₂O" in client.get("/search", params={"q": "water"}).text
    wiki._entity_cache._data.clear()
    app.state.ext = entity_with({"P31": [item("Q6256")], "P36": [item("Q17042")]})
    html = client.get("/search", params={"q": "india"}).text
    assert "hero-entity--country" in html and "Capital" in html


# ------------------------------------------------------------- AI models (Epoch AI)

ECI_CSV = """Model,Display name,eci,eci_ci_low,eci_ci_high,date,Organization,Country (of organization),Model accessibility,Accessibility group,model_versions
Claude Opus 4.5,Claude Opus 4.5,150.2,148,152,2025-11-24,Anthropic,United States of America,API access,Closed weights,
Claude Opus 4.1,Claude Opus 4.1,144.1,142,146,2025-08-05,Anthropic,United States of America,API access,Closed weights,
Claude Opus 4,Claude Opus 4,143.0,141,145,2025-05-22,Anthropic,United States of America,API access,Closed weights,
Claude Opus 4.6,Claude Opus 4.6,155.2,153,157,2026-02-05,Anthropic,United States of America,API access,Closed weights,
Gemini 3 Pro,Gemini 3 Pro,153.1,151,155,2025-11-18,"Google DeepMind,Google",United States of America,API access,Closed weights,
GPT-5.1,GPT-5.1,149.0,147,151,2025-11-12,OpenAI,United States of America,API access,Closed weights,
o3,o3,146.0,144,148,2025-04-16,OpenAI,United States of America,API access,Closed weights,
DeepSeek-V3.2,DeepSeek-V3.2,146.0,144,148,2025-12-01,DeepSeek,China,Open weights,Open weights,
"""
SCORES_CSV = """model_id,benchmark_id,performance,benchmark,benchmark_release_date,model,model_version,Model,date,source
m1,b1,0.814,GPQA diamond,2023-11-20,x,x,Claude Opus 4.5,2025-11-24,
m2,b1,0.697,GPQA diamond,2023-11-20,x,x,Claude Opus 4.1,2025-08-05,
m3,b1,0.902,GPQA diamond,2023-11-20,x,x,Gemini 3 Pro,2025-11-18,
m4,b1,0.85,GPQA diamond,2023-11-20,x,x,GPT-5.1,2025-11-12,
m1,b2,0.767,SWE-Bench verified,2024-08-13,x,x,Claude Opus 4.5,2025-11-24,
m2,b2,0.733,SWE-Bench verified,2024-08-13,x,x,Claude Opus 4.1,2025-08-05,
m3,b2,0.729,SWE-Bench verified,2024-08-13,x,x,Gemini 3 Pro,2025-11-18,
m1,b3,0.4,DTBench,2025-01-01,x,x,Claude Opus 4.5,2025-11-24,
"""
# GPQA's own results file: every model version, including ones the index doesn't cover.
BENCH_META_CSV = """benchmark,in_eci,source_file,score_column,scale,random_baseline,score_ceiling,release_date,superseded_by
GPQA diamond,True,gpqa_diamond.csv,Best score (across scorers),1.0,0.25,1.0,2023-11-20,
"""
VERSIONS_CSV = """model_version,model_group,date,display_name,organization,country,accessibility,training_compute_flop
claude-opus-4-5_low,Claude Opus 4.5,2025-11-24,Claude Opus 4.5 (low),Anthropic,,,
claude-opus-4-5_high,Claude Opus 4.5,2025-11-24,Claude Opus 4.5 (high),Anthropic,,,
"""
GPQA_CSV = """Model version,mean_score,Best score (across scorers),Release date,Organization,Country,Training compute (FLOP),Training compute notes,stderr,Log viewer,Logs,Started at,id
claude-opus-4-5_low,0.70,0.70,2025-11-24,Anthropic,,,,,,,,a
claude-opus-4-5_high,0.871,0.871,2025-11-24,Anthropic,,,,,,,,b
mystery-model-9_high,0.55,0.55,2025-09-01,Somewhere,,,,,,,,c
"""


def epoch_zip(scores: str = SCORES_CSV) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(aimodels._ECI, ECI_CSV)
        z.writestr(aimodels._SCORES, scores)
        z.writestr(aimodels._BENCH_META, BENCH_META_CSV)
        z.writestr(aimodels._VERSIONS, VERSIONS_CSV)
        z.writestr("gpqa_diamond.csv", GPQA_CSV)
    return buf.getvalue()


def test_ai_model_matching():
    cat = aimodels._parse(epoch_zip())
    find = lambda q: (m.name if (m := aimodels.find(cat, q)) else None)  # noqa: E731
    assert find("Claude Opus 4.5") == find("opus 4.5") == find("claude opus 4.5 benchmarks") == "Claude Opus 4.5"
    assert find("gpt 5.1 release date") == "GPT-5.1"
    assert find("o3") is None                       # too short to claim on its own...
    assert find("openai o3") == find("o3 model") == "o3"  # ...unless the query frames it as a model
    assert find("iphone 17") is None and find("claude") is None and find("python 3.12") is None


def test_ai_model_hero(client):
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=epoch_zip()) if r.url.host == "epoch.ai" else upstream(r)))
    html = client.get("/search", params={"q": "claude opus 4.5"}).text
    assert "hero-aimodel" in html and "24 Nov 2025" in html and "Closed weights" in html
    assert "Claude Opus 4" in html                                        # family
    assert ">Claude Opus 4.1</a>" in html and ">Claude Opus 4.6</a>" in html  # previous / succeeded by
    assert "Gemini 3 Pro" in html and "Google DeepMind" in html             # rival from another lab
    assert "GPQA Diamond" in html and "SWE-bench Verified" in html and "76.7%" in html
    assert "87.1%" in html and "81.4%" not in html          # GPQA uses its own file, best reasoning setting
    assert "mystery-model-9_high" not in html                 # only the chosen models are plotted, no background dots
    assert html.count('class="am-filter"') == 3 and 'data-am-show="all"' in html   # a chip filter per chart
    assert 'data-am-toggle="dots"' not in html and "am-frontier" not in html
    assert "DTBench" not in html                                          # only well-known benchmarks get tabs
    assert "hero-entity" not in html                                      # beats the Wikipedia panel
    assert aimodels.CACHE_FILE.exists()                                   # cached for the next query


OPENROUTER = {"data": [
    {"id": "anthropic/claude-opus-4.5", "name": "Anthropic: Claude Opus 4.5",
     "pricing": {"prompt": "0.000005", "completion": "0.000025", "input_cache_read": "0.0000005"}},
    {"id": "anthropic/claude-opus-4.1", "name": "Anthropic: Claude Opus 4.1", "pricing": {"prompt": "0.000015", "completion": "0.000075"}},
    {"id": "google/gemini-3-pro-preview", "name": "Google: Gemini 3 Pro Preview", "pricing": {"prompt": "0.000002", "completion": "0.000012"}},
    {"id": "openai/gpt-5.1", "name": "OpenAI: GPT-5.1", "pricing": {"prompt": "0.00000125", "completion": "0.00001"}},
    {"id": "openai/gpt-5.1:free", "name": "OpenAI: GPT-5.1 (free)", "pricing": {"prompt": "0", "completion": "0"}},
]}


def test_model_prices_match_epoch_names():
    table = modelprices._parse(json.dumps(OPENROUTER).encode())
    opus = modelprices.lookup(table, "Claude Opus 4.5")
    assert (opus.input, opus.output, opus.cached, opus.blended) == (5, 25, .5, 10)   # (3 x $5 + $25) / 4
    assert modelprices.lookup(table, "Gemini 3 Pro").blended == 4.5        # "Preview" and the org prefix don't count
    assert modelprices.lookup(table, "GPT-5.1").blended == 3.4375          # the paid listing, not ":free"
    assert modelprices.lookup(table, "o3") is None and modelprices.lookup({}, "GPT-5.1") is None


def test_ai_model_price_chart(client):
    # SWE-bench under its other Epoch spelling still gets a tab, and charts plot score against price.
    blob = epoch_zip(SCORES_CSV.replace("SWE-Bench verified", "SWE-bench Verified (Bash Only)"))
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=blob) if r.url.host == "epoch.ai"
        else httpx.Response(200, json=OPENROUTER) if r.url.host == "openrouter.ai"
        else httpx.Response(200, json={"date": "2026-10-13", "rates": {"INR": 88.21}}) if r.url.host == "api.frankfurter.app"
        else upstream(r)))
    html = client.get("/search", params={"q": "claude opus 4.5"}).text
    assert "SWE-bench Verified" in html and "76.7%" in html
    assert "by API price" in html and "Token pricing" not in html and "Cost / point" not in html
    assert "Estimate a workload" not in html and "data-am-cur" not in html  # no calculator or currency switch
    assert "₹882" in html and 'data-money="10.0"' in html                # the blended price in the table, in rupees
    assert "₹441" in html and "₹2,205" in html                            # input and output rates in the facts
    assert "1 USD = ₹88.21" in html and "13 Oct 2026" in html
    assert "/api/favicon/anthropic.com" not in html and "am-org" not in html   # no lab badges, names only
    assert 'aria-label="Show Gemini 3 Pro on the chart"' in html and "am-frontier" in html   # menu and best-value line
    assert "OpenRouter" in html
    assert "No API price listed" in html                                   # unpriced rivals stay listed, off the graph
    assert modelprices.CACHE_FILE.exists()


def test_org_logos_and_money():
    assert aimodels.org_site("Meta AI") == "meta.com" and aimodels.org_site("AI21 Labs") == "ai21.com"
    assert aimodels.org_site("Allen Institute for AI") == "allenai.org" and aimodels.org_site("Some Lab") == ""
    rupee = {"code": "INR", "symbol": "₹", "per_usd": 88.21}
    assert aimodels.money(10) == "$10" and aimodels.money(3.4375) == "$3.44" and aimodels.money(.0021) == "$0.0021"
    assert aimodels.money(2000, rupee) == "₹1,76,420"                       # lakh grouping


def test_swe_bench_tab_is_pinned():
    cat = aimodels._parse(epoch_zip())
    gpt = aimodels.find(cat, "gpt 5.1")
    assert "SWE-bench Verified" not in gpt.scores
    cat.points["SWE-bench Verified"] = [aimodels.Point(m.released, m.scores["SWE-bench Verified"], m.name, m.name)
                                        for m in cat.models if "SWE-bench Verified" in m.scores]
    labels = [c["label"] for c in aimodels.panel(cat, gpt)["charts"]]
    assert "SWE-bench Verified" in labels                                  # shown even though GPT-5.1 has no result


def test_ai_model_unavailable_falls_back_to_entity(client):
    html = client.get("/search", params={"q": "zendaya"}).text            # epoch.ai 404s in `upstream`
    assert "hero-entity" in html and "hero-aimodel" not in html


def searxng_reply(results):
    return httpx.MockTransport(lambda r: httpx.Response(200, json={**EMPTY_SEARCH, "results": results}))


SONY_LISTINGS = [
    {"title": "WH-1000XM5 | Wireless Noise Cancelling Headphones - Sony India", "url": "https://www.sony.co.in/wh-1000xm5",
     "content": "Our ultra comfortable WH-1000XM5 headphones rewrite the rules.", "engines": ["google"]},
    {"title": "SONY WH-1000XM5 Wireless Noise Cancellation (Midnight Blue)", "url": "https://www.flipkart.com/sony-wh-1000xm5/p/1",
     "content": "Buy SONY WH-1000XM5 for Rs.34990 Online ; 30 Hours Battery Life", "engines": ["google"]},
    {"title": "Sony WH-1000XM5 price drop", "url": "https://www.croma.com/sony-wh-1000xm5/p/2", "content": "Now ₹24,990.",
     "engines": ["google"]},
    {"title": "Sony WH-1000XM5 review - SoundGuys", "url": "https://www.soundguys.com/sony-wh-1000xm5-review",
     "content": "20 May 2022 ... Strong bass and treble emphasis. Score 8.5/10", "engines": ["google"]},
]


def test_product_hero_from_store_listings(client):
    app.state.searx = SearXNG(httpx.AsyncClient(base_url=settings.searxng_url, transport=searxng_reply(SONY_LISTINGS)))
    html = client.get("/search", params={"q": "sony wh-1000xm5"}).text
    assert "hero-product" in html and "Sony WH-1000XM5" in html and "Headphones" in html
    assert "₹24,990" in html and "to ₹34,990" in html and "lowest at Croma" in html
    assert "SoundGuys" in html and "8.5/10" in html and "Video reviews" in html
    assert 'data-ai-question="Is the Sony WH-1000XM5 worth buying?"' in html   # opens AI Mode on that question
    live = client.get("/api/product", params={"name": "Sony WH-1000XM5", "q": "sony wh-1000xm5", "brand": "Sony"}).json()
    assert live["price"]["low_text"] == "₹24,990" and {o["store"] for o in live["offers"]} >= {"Croma", "Flipkart", "Sony"}
    ai = client.get("/ai", params={"q": "sony wh-1000xm5"}).text
    assert "ai-welcome-asks" in ai and "Where is the Sony WH-1000XM5 cheapest right now?" in ai


def test_product_hero_from_wikidata(client):
    page = {"query": {"pages": [{"title": "IPhone 16 Pro", "fullurl": "https://en.wikipedia.org/wiki/IPhone_16_Pro",
                                 "description": "2024 smartphone by Apple", "extract": "The iPhone 16 Pro is a smartphone.",
                                 "pageprops": {"wikibase_item": "Q130267746"},
                                 "original": {"source": "https://upload.wikimedia.org/i16.svg", "width": 600, "height": 900},
                                 "thumbnail": {"source": "https://upload.wikimedia.org/i16.png"}}]}}
    claims = {"entities": {"Q130267746": {"claims": {
        "P31": [item("Q19723451")], "P176": [item("Q312")], "P1716": [item("Q312")], "P155": [item("Q1")], "P156": [item("Q2")],
        "P306": [item("Q48493")],
        "P2067": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "quantity", "value": {"amount": "+199", "unit": "http://www.wikidata.org/entity/Q41803"}}}, "rank": "normal"}],
    }}}}
    labels = {"entities": {"Q312": {"labels": {"en": {"value": "Apple Inc."}}}, "Q1": {"labels": {"en": {"value": "iPhone 15 Pro"}}},
                           "Q2": {"labels": {"en": {"value": "iPhone 17 Pro"}}}, "Q48493": {"labels": {"en": {"value": "iOS"}}},
                           "Q19723451": {"labels": {"en": {"value": "smartphone model"}}}}}

    def reply(request):
        if request.url.host == "en.wikipedia.org":
            return httpx.Response(200, json=page)
        if request.url.host == "www.wikidata.org":
            return httpx.Response(200, json=claims if request.url.params.get("props", "").startswith("claims") else labels)
        return httpx.Response(404)

    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    app.state.searx = SearXNG(httpx.AsyncClient(base_url=settings.searxng_url, transport=searxng_reply([
        {"title": "Apple iPhone 16 Pro (Desert Titanium, 256 GB)", "url": "https://www.flipkart.com/iphone-16-pro/p/3",
         "content": "₹1,19,900 ; A18 Pro Chip, 6 Core Processor", "engines": ["google"]}])))
    html = client.get("/search", params={"q": "iphone 16 pro price"}).text
    assert "hero-product" in html and "iPhone 16 Pro" in html and "Smartphone" in html
    assert "https://upload.wikimedia.org/i16.png" in html                    # the SVG drawing's PNG render
    assert "iPhone 15 Pro" in html and "iPhone 17 Pro" in html                # model line
    assert "Operating system" in html and "199 g" in html and "₹1,19,900" in html and "Desert Titanium" in html


# ------------------------------------------------------------ video games

def text_claim(value, qualifiers=None):
    claim = {"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": value}}, "rank": "normal"}
    if qualifiers:
        claim["qualifiers"] = qualifiers
    return claim


GAME_CLAIMS = {
    "P31": [item("Q7889")], "P348": [text_claim("1.12")],                  # a version used to make it "software"
    "P178": [item("Q2414469")], "P123": [item("Q1194689")], "P400": [item("Q5014725"), item("Q63184502")],
    "P404": [item("Q208850")], "P908": [item("Q14915516")], "P1733": [text_claim("1245620")],
    "P1651": [text_claim("E3Huy2cdih0")], "P5971": [text_claim("EP0700-PPSA04609_00-ELDENRING0000000")],
    "P444": [text_claim("95/100", {"P447": [{"snaktype": "value", "datavalue": {"value": {"id": "Q21039459"}}}]})],
    "P2664": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "quantity", "value": {"amount": "+30000000", "unit": "1"}}}, "rank": "normal"}],
}
GAME_LABELS = {"Q2414469": "FromSoftware", "Q1194689": "Bandai Namco Entertainment", "Q5014725": "Microsoft Windows",
               "Q63184502": "PlayStation 5", "Q208850": "single-player video game", "Q14915516": "PEGI 16",
               "Q21039459": "OpenCritic", "Q7889": "video game"}


def game_wikidata(request):
    if request.url.host == "www.wikidata.org":
        if request.url.params.get("props", "").startswith("claims"):
            return httpx.Response(200, json={"entities": {"Q189489": {"claims": GAME_CLAIMS}}})
        return httpx.Response(200, json={"entities": {k: {"labels": {"en": {"value": v}}} for k, v in GAME_LABELS.items()}})
    return upstream(request)


def test_video_game_gets_its_own_panel(client):
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(game_wikidata))
    html = client.get("/search", params={"q": "elden ring"}).text
    assert "hero-game" in html and "Video game" in html and "hero-entity--software" not in html
    assert 'data-game-appid="1245620"' in html and "youtube-nocookie.com/embed/E3Huy2cdih0" in html
    assert "store.playstation.com/en-in/product/EP0700-PPSA04609_00-ELDENRING0000000" in html     # its own store page
    assert "https://store.steampowered.com/app/1245620/" in html and "OpenCritic" in html and "PEGI 16" in html
    assert "30 million" in html and "FromSoftware" in html and ">PC<" in html and "Single-player" in html


STEAM_APP = {"1245620": {"success": True, "data": {
    "name": "ELDEN RING", "is_free": False, "header_image": "https://shared.steamstatic.com/h.jpg",
    "price_overview": {"currency": "INR", "initial": 449900, "final": 359900, "discount_percent": 20,
                       "initial_formatted": "₹ 4,499", "final_formatted": "₹ 3,599"},
    "metacritic": {"score": 94, "url": "https://www.metacritic.com/game/pc/elden-ring"},
    "platforms": {"windows": True, "mac": False, "linux": False}, "release_date": {"coming_soon": False, "date": "24 Feb, 2022"},
    "categories": [{"id": 2, "description": "Single-player"}, {"id": 38, "description": "Online Co-op"}, {"id": 999, "description": "Other"}],
    "screenshots": [{"path_thumbnail": "https://shared.steamstatic.com/s1t.jpg", "path_full": "https://shared.steamstatic.com/s1.jpg"}],
    "pc_requirements": {"minimum": "<strong>Minimum:</strong><br><ul class=\"bb_ul\"><li>Requires a 64-bit processor<br></li>"
                                   "<li><strong>OS:</strong> Windows 10<br></li><li><strong>Memory:</strong> 12 GB RAM<br></li>"
                                   "<li><strong>Graphics:</strong> NVIDIA GTX 1060 &amp; AMD RX 580<br></li></ul>",
                        "recommended": "<ul><li><strong>OS *:</strong> Windows 11</li></ul>"},
    "dlc": [1, 2, 3], "achievements": {"total": 42}}}}


def game_upstream(request):
    host, path = request.url.host, request.url.path
    if host == "store.steampowered.com" and path == "/api/appdetails":
        return httpx.Response(200, json=STEAM_APP)
    if host == "store.steampowered.com" and path.startswith("/appreviews/"):
        return httpx.Response(200, json={"success": 1, "query_summary": {"review_score_desc": "Very Positive", "total_positive": 930,
                                                                         "total_negative": 70, "total_reviews": 1000}})
    if host == "api.steampowered.com":
        return httpx.Response(200, json={"response": {"player_count": 19065, "result": 1}})
    if host == "www.cheapshark.com" and path.endswith("/stores"):
        return httpx.Response(200, json=[{"storeID": "23", "storeName": "GameBillet", "isActive": 1}])
    if host == "www.cheapshark.com" and request.url.params.get("steamAppID"):
        return httpx.Response(200, json=[{"gameID": "236717"}])
    if host == "www.cheapshark.com":
        return httpx.Response(200, json={"cheapestPriceEver": {"price": "29.95", "date": 1751353695}, "deals": [
            {"storeID": "23", "dealID": "abc", "price": "52.89", "retailPrice": "59.99", "savings": "11.8"}]})
    if host == "api.frankfurter.app":
        return httpx.Response(200, json={"date": "2026-10-16", "rates": {"INR": 95.0}})
    return httpx.Response(404)


def test_game_api_has_steam_price_reviews_and_pc_deals(client):
    from archer.sources import games
    games._cache._data.clear()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(game_upstream))
    d = client.get("/api/game", params={"appid": "1245620"}).json()
    assert d["price"] == {"free": False, "text": "₹3,599", "original": "₹4,499", "discount": 20}
    assert d["reviews"] == {"label": "Very Positive", "total": 1000, "percent": 93} and d["players"] == 19065
    assert d["metacritic"]["score"] == 94 and d["features"] == ["Single-player", "Online co-op"] and d["systems"] == ["Windows"]
    best = d["deals"]["deals"][0]
    assert best["store"] == "GameBillet" and best["usd"] == "$52.89" and best["home"] == "₹5,025" and best["cut"] == 12
    assert d["deals"]["lowest"]["usd"] == "$29.95" and d["dlc"] == 3 and d["achievements"] == 42
    assert d["requirements"]["minimum"] == [["OS", "Windows 10"], ["Memory", "12 GB RAM"], ["Graphics", "NVIDIA GTX 1060 & AMD RX 580"]]
    assert client.get("/api/game", params={"appid": "12x"}).status_code == 422                # ids are digits only


def test_game_api_without_steam_is_empty(client):
    from archer.sources import games
    games._cache._data.clear()
    games._appids._data.clear()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []})))
    assert client.get("/api/game", params={"title": "Some Console Exclusive"}).json() == {}


# ------------------------------------------------------------ motorsport

def when(value):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "time",
            "value": {"time": value, "precision": 11}}}, "rank": "normal"}


def quantity(amount, unit="1"):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "quantity",
            "value": {"amount": amount, "unit": "http://www.wikidata.org/entity/" + unit}}}, "rank": "normal"}


def article(title, qid, description, extract):
    return {"query": {"pages": [{"title": title, "fullurl": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                                 "description": description, "extract": extract,
                                 "pageprops": {"wikibase_item": qid},
                                 "original": {"source": "https://upload.wikimedia.org/p.jpg", "width": 1200, "height": 800}}]}}


# Real claim shapes: a car model typed Q3231690, a race typed only "recurring sporting event" and found by
# its sport, an F1 driver typed "human" with the series as an occupation, and a team typed Q10497835.
CAR_CLAIMS = {
    "P31": [item("Q3231690")], "P176": [item("Q20195")],
    "P279": [item("Q11030"), item("Q3231690")],              # "sports car" is kept, "car model" is not
    "P571": [when("+1963-01-01T00:00:00Z")],
    "P2052": [quantity("+340", "Q180154")],                  # km/h, not the "kilometre per hour" label
    "P1092": [quantity("+1500000")], "P155": [item("Q177250")],
}
RACE_CLAIMS = {
    "P31": [item("Q18608583")],                              # a recurring event, not an "automobile racing series"
    "P641": [item("Q1805420")], "P279": [item("Q1805420")],  # endurance racing, three different ways below
    "P571": [when("+1923-06-10T00:00:00Z")], "P17": [item("Q142")],
    "P1346": [item("Q310687")],
}
F1_DRIVER_CLAIMS = {
    "P31": [item("Q5")], "P106": [item("Q10349745"), item("Q10841764")],
    "P54": [item("Q169898")], "P569": [when("+1997-09-30T00:00:00Z")], "P27": [item("Q55")],
}
F1_TEAM_CLAIMS = {
    "P31": [item("Q10497835")], "P641": [item("Q5386")],
    "P17": [item("Q38")], "P571": [when("+1929-01-01T00:00:00Z")], "P159": [item("Q9178")],
}

GP_CLAIMS = {"P31": [item("Q108886259")], "P641": [item("Q5367")], "P17": [item("Q235")],
             "P571": [when("+1929-04-14T00:00:00Z")]}
CIRCUIT_CLAIMS = {"P31": [item("Q2338524")], "P17": [item("Q145")],
                  "P625": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "globecoordinate",
                            "value": {"latitude": 52.0786, "longitude": -1.01694}}}, "rank": "normal"}],
                  "P2043": [quantity("+5.891", "Q828224")], "P1619": [when("+1948-10-02T00:00:00Z")]}

MOTORSPORT_PAGES = {
    "Porsche 911": ("Q219357", "Sports car produced by Porsche", "The Porsche 911 is a sports car."),
    "24 Hours of Le Mans": ("Q187579", "Annual sports car race held in France", "The 24 Hours of Le Mans is a race."),
    "Max Verstappen": ("Q2239218", "Dutch and Belgian racing driver", "Max Verstappen is a Formula One driver."),
    "Scuderia Ferrari": ("Q169898", "Formula One team", "Scuderia Ferrari is an Italian Formula One team."),
    "Monaco Grand Prix": ("Q9102", "Formula One race in Monaco", "The Monaco Grand Prix is a Formula One race."),
    "Silverstone Circuit": ("Q171402", "Motor racing circuit in England", "Silverstone Circuit is in England."),
}
MOTORSPORT_CLAIMS = {"Q219357": CAR_CLAIMS, "Q187579": RACE_CLAIMS, "Q2239218": F1_DRIVER_CLAIMS,
                     "Q169898": F1_TEAM_CLAIMS, "Q9102": GP_CLAIMS, "Q171402": CIRCUIT_CLAIMS}
MOTORSPORT_LABELS = {
    "Q5": "human", "Q38": "Italy", "Q55": "Netherlands", "Q142": "France", "Q20195": "Porsche",
    "Q11030": "sports car", "Q5386": "auto racing", "Q9178": "Maranello", "Q10349745": "racing automobile driver",
    "Q10841764": "Formula One driver", "Q169898": "Scuderia Ferrari", "Q177250": "Porsche 356",
    "Q1805420": "endurance racing", "Q18608583": "recurring sporting event", "Q310687": "Ferrari 499P",
    "Q3231690": "car model", "Q108886259": "Formula One event", "Q5367": "motorsport", "Q235": "Monaco",
    "Q2338524": "motorsport racing track", "Q145": "United Kingdom", "Q828224": "kilometre",
}


def motorsport_upstream(request: httpx.Request) -> httpx.Response:
    host, params = request.url.host, request.url.params
    # classify() lowercases the candidate before it reaches Wikipedia, and titles are matched here.
    title = next((t for t in MOTORSPORT_PAGES if t.casefold() == params.get("titles", "").casefold()), "")
    if host == "en.wikipedia.org":
        if title:
            qid, description, extract = MOTORSPORT_PAGES[title]
            return httpx.Response(200, json=article(title, qid, description, extract))
        return httpx.Response(200, json={"query": {"pages": []}})
    if host == "www.wikidata.org":
        if params.get("props", "").startswith("claims"):
            if params.get("sites"):                       # the by-title guess made alongside the page lookup
                qid = MOTORSPORT_PAGES.get(title, ("", "", ""))[0]
            else:
                qid = params.get("ids", "")
            return httpx.Response(200, json={"entities": {qid: {"claims": MOTORSPORT_CLAIMS[qid]}} if qid in MOTORSPORT_CLAIMS else {}})
        return httpx.Response(200, json={"entities": {q: {"labels": {"en": {"value": n}}} for q, n in MOTORSPORT_LABELS.items()}})
    return upstream(request)


def test_car_gets_a_car_centric_panel_with_one_matted_hero_picture(client):
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(motorsport_upstream))
    html = client.get("/search", params={"q": "Porsche 911"}).text
    assert "hero-car" in html and ">Car<" in html and "hero-entity--product" not in html
    assert 'class="ms-stage ms-stage--car"' in html and 'class="ms-shot"' in html
    assert "sports car" in html and "340 km/h" in html and "1.5 million" in html
    assert "Porsche 356" in html                          # the previous generation


def test_car_model_is_not_filed_under_product():
    from archer.sources.wiki import _kind
    assert _kind({"claims": CAR_CLAIMS}) == "car"


def test_racing_series_panel_tags_its_discipline(client):
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(motorsport_upstream))
    html = client.get("/search", params={"q": "24 hours of le mans"}).text
    assert "hero-race" in html and "Racing series" in html
    assert ">Endurance<" in html                            # one tag, not three overlapping spellings
    assert "Ferrari 499P" in html and "1923" in html


def test_discipline_tags_collapse_overlapping_names():
    from archer.sources.wiki import _disciplines
    assert _disciplines(["endurance racing", "sports car racing", "automobile endurance racing"]) == ["Endurance"]
    assert _disciplines(["rallying", "world championship"]) == ["Rally"]
    assert _disciplines(["rally raid race"]) == ["Rally raid"]
    assert _disciplines(["motorsport racing", "sport", "recurring sporting event"]) == []


def test_formula_one_driver_and_team_get_their_own_panels(client):
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(motorsport_upstream))
    driver = client.get("/search", params={"q": "max verstappen"}).text
    assert 'class="hero hero-entity hero-f1 is-driver"' in driver and "Formula One driver" in driver
    assert 'data-f1-title="Max Verstappen"' in driver and "Scuderia Ferrari" in driver
    assert "30 September 1997" in driver
    team = client.get("/search", params={"q": "scuderia ferrari"}).text
    assert 'class="hero hero-entity hero-f1 is-team"' in team and "Formula One team" in team
    assert "Maranello" in team and "1929" in team


def test_a_racing_driver_without_formula_one_stays_a_person(client):
    """The F1 occupation is the only thing that makes a driver an F1 panel; nobody else is promoted."""
    from archer.sources.wiki import _kind
    assert _kind({"claims": {"P31": [item("Q5")], "P106": [item("Q10349745")]}}) == "person"
    assert _kind({"claims": F1_DRIVER_CLAIMS}) == "f1driver"
    assert _kind({"claims": {"P31": [item("Q5")], "P54": [item("Q169898")]}}) == "person"


def test_grand_prix_and_circuit_get_their_own_panels(client):
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(motorsport_upstream))
    gp = client.get("/search", params={"q": "monaco grand prix"}).text
    assert 'data-f1-gp="Monaco Grand Prix"' in gp and ">Grand Prix<" in gp and ">Formula One<" in gp
    circuit = client.get("/search", params={"q": "silverstone circuit"}).text
    assert "hero-circuit" in circuit and 'data-f1-circuit="Silverstone Circuit"' in circuit
    assert 'data-lat="52.0786"' in circuit and "5.891 km" in circuit and "1948" in circuit


def test_kinds_for_circuits_grands_prix_and_works_teams():
    from archer.sources.wiki import _kind
    assert _kind({"claims": CIRCUIT_CLAIMS}) == "circuit"
    assert _kind({"claims": {"P31": [item("Q926439")]}}) == "circuit"            # a street circuit
    assert _kind({"claims": {"P31": [item("Q18608583")], "P641": [item("Q5367")]}}) == "race"
    assert _kind({"claims": {"P31": [item("Q21484471")]},
                  "labels": {"en": {"value": "Mercedes-Benz in Formula One"}}}) == "f1team"


# Real Jolpica responses for the 2026 season after round 17, shared with tests/motorsport_browser_server.py.
JOLPICA_DIR = Path(__file__).with_name("fixtures") / "jolpica"
SCHUMACHER = {"driverId": "michael_schumacher", "url": "https://en.wikipedia.org/wiki/Michael_Schumacher",
              "givenName": "Michael", "familyName": "Schumacher", "code": "MSC", "nationality": "German"}


def jolpica(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/ergast/f1/", 1)[-1].removesuffix(".json")
    params = dict(request.url.params)
    if path in ("drivers", "constructors"):
        rows = [SCHUMACHER] if path == "drivers" else []
        table = "DriverTable" if path == "drivers" else "ConstructorTable"
        return httpx.Response(200, json={"MRData": {"total": str(len(rows)), table: {path.capitalize()[:-1] + "s": rows}}})
    name = "_".join([path.replace("/", "_")] + [f"{k}_{params[k]}" for k in ("limit", "offset") if k in params])
    file = JOLPICA_DIR / f"{name}.json"
    return httpx.Response(200, content=file.read_bytes()) if file.exists() else httpx.Response(404)


@pytest.fixture
def f1_api(client, monkeypatch):
    from datetime import datetime, timezone
    from archer.sources import f1
    f1._cache._data.clear()
    f1._LOOKUP.clear()
    f1._NAMES.clear()
    f1._track._data.clear()
    monkeypatch.setattr(f1, "_now", lambda: datetime(2026, 10, 18, 12, tzinfo=timezone.utc))
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(jolpica))
    return client


def test_f1_driver_gets_standing_season_and_career(f1_api):
    d = f1_api.get("/api/f1", params={"title": "Lewis Hamilton"}).json()
    assert d["role"] == "driver" and d["active"] and (d["code"], d["number"], d["team"]) == ("HAM", "44", "Ferrari")
    assert d["color"] == "#E8002D"
    s = d["standing"]
    assert (s["position"], s["points"], s["wins"], s["to_leader"]) == (3, 229, 1, 111)
    assert [r["position"] for r in s["ladder"]] == [1, 2, 3, 4, 5] and s["ladder"][2]["me"]
    assert len(d["results"]) == 17 and d["rounds"] == 23
    spain = next(r for r in d["results"] if r["round"] == 14)
    assert not spain["classified"] and spain["status"] == "Retired" and spain["grid"] == 4
    assert d["form"]["dnf"] == 1 and d["form"]["best"] == {"position": 1, "name": "Barcelona Grand Prix"}
    c = d["career"]
    assert (c["starts"], c["wins"], c["poles"], c["seasons"], c["first"]) == (397, 106, 119, 20, 2007)
    assert c["titles"] == [2008, 2014, 2015, 2017, 2018, 2019, 2020]
    assert c["podiums"] > c["wins"]                                     # wins, seconds and thirds
    assert sum(1 for r in d["calendar"] if r["done"]) == 17


def test_f1_team_gets_lineup_points_per_round_and_titles(f1_api):
    t = f1_api.get("/api/f1", params={"title": "Scuderia Ferrari"}).json()
    assert t["role"] == "team" and t["name"] == "Ferrari" and t["standing"]["position"] == 2
    assert {p["name"] for p in t["lineup"]} == {"Lewis Hamilton", "Charles Leclerc"}
    assert len(t["rounds"]) == 17 and all(len(r["cars"]) == 2 for r in t["rounds"])
    assert t["rounds"][0]["points"] == sum(c["points"] for c in t["rounds"][0]["cars"])
    assert len(t["career"]["titles"]) == 16 and "starts" not in t["career"]   # a team's results count every car


def test_f1_retired_driver_is_found_in_the_full_list_with_career_only(f1_api):
    d = f1_api.get("/api/f1", params={"title": "Michael Schumacher"}).json()
    assert d["role"] == "driver" and not d["active"] and d["standing"] == {} and d["results"] == []
    assert d["career"]["titles"] == [1994, 1995, 2000, 2001, 2002, 2003, 2004]
    assert f1_api.get("/api/f1", params={"title": "Sunderland"}).json() == {}


def test_f1_driver_is_found_when_jolpica_links_a_redirect(f1_api):
    """Jolpica links Andrea_Kimi_Antonelli and Alexander_Albon; the articles are Kimi Antonelli and Alex Albon."""
    from archer.sources import f1
    assert f1_api.get("/api/f1", params={"title": "Kimi Antonelli"}).json()["standing"]["position"] == 1
    assert f1_api.get("/api/f1", params={"title": "Alex Albon"}).json()["id"] == "albon"
    assert f1._by_name("Carlos Sainz Jr.") == "sainz" and f1._by_name("Lewis Russell") == "" and f1._by_name("Max") == ""


def test_a_search_naming_a_team_or_driver_in_its_own_words_finds_the_article(f1_api):
    from archer.sources import f1
    ext = httpx.AsyncClient(transport=httpx.MockTransport(jolpica))
    title = lambda text: asyncio.run(f1.title_for_query(ext, text))   # noqa: E731
    assert title("mercedes petronas amg f1 team") == "Mercedes-Benz in Formula One"
    assert title("red bull f1") == "Red Bull Racing" and title("racing bulls") == "Racing Bulls"
    assert title("aston martin f1 team") == "Aston Martin in Formula One"
    assert title("hamilton f1") == "Lewis Hamilton"
    assert title("mercedes") == "" and title("red bull") == ""          # no F1 cue: the brand, not the team


def test_title_cased_spelling_finds_a_redirect_the_lowercase_query_missed():
    from archer.sources.wiki import _find_page
    asked = []

    def reply(request):
        titles = request.url.params.get("titles", "")
        asked.append(titles or "search")
        if titles == "Andrea Kimi Antonelli":
            return httpx.Response(200, json={"query": {"pages": [{"title": "Kimi Antonelli", "pageprops": {"wikibase_item": "Q112073790"}}]}})
        return httpx.Response(200, json={"query": {"pages": [{"title": titles, "missing": True}]}})
    page, corrected = asyncio.run(_find_page(httpx.AsyncClient(transport=httpx.MockTransport(reply)), "andrea kimi antonelli"))
    assert page["title"] == "Kimi Antonelli" and not corrected and asked == ["andrea kimi antonelli", "Andrea Kimi Antonelli"]


def test_f1_api_is_empty_when_jolpica_is_down(client):
    from archer.sources import f1
    f1._cache._data.clear()
    f1._LOOKUP.clear()
    f1._NAMES.clear()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert client.get("/api/f1", params={"title": "Lewis Hamilton"}).json() == {}
    assert client.get("/api/f1", params={"title": "   "}).json() == {}
    assert client.get("/api/f1/next").json() == {}
    assert client.get("/api/f1/gp", params={"title": "Monaco Grand Prix"}).json() == {}


def test_f1_next_round_comes_from_the_calendar_with_its_circuit_drawn(f1_api):
    d = f1_api.get("/api/f1/next").json()
    assert (d["round"], d["name"], d["rounds"]) == (18, "United States Grand Prix", 23)
    assert d["circuit"] == "Circuit of the Americas" and d["flag"] == "https://flagcdn.com/us.svg"
    assert d["start"] == "2026-10-25T20:00:00Z" and d["schedule"]["Qualifying"] == "2026-10-24T21:00:00Z"
    assert d["map"]["id"] == "us-2012" and d["map"]["length"] == 5514 and len(d["map"]["points"]) > 100
    assert [r["done"] for r in d["calendar"]].count(True) == 17


def test_f1_grand_prix_has_its_round_podium_and_winners_there(f1_api):
    gp = f1_api.get("/api/f1/gp", params={"title": "Monaco Grand Prix"}).json()
    assert gp["round"] == 6 and gp["done"] and gp["map"]["id"] == "mc-1929"
    assert [p["position"] for p in gp["podium"]] == [1, 2, 3]
    assert gp["held"] == 72 and len(gp["winners"]) == 8 and gp["winners"][0]["season"] == "2026"
    assert gp["winners"][0]["season"] >= gp["winners"][-1]["season"]          # newest first
    assert f1_api.get("/api/f1/gp", params={"title": "2026 Monaco Grand Prix"}).json()["round"] == 6
    assert f1_api.get("/api/f1/gp", params={"title": "Indianapolis 500"}).json() == {}


def test_f1_circuit_is_matched_by_article_with_outline_and_winners(f1_api):
    c = f1_api.get("/api/f1/circuit", params={"title": "Silverstone Circuit", "lat": "52.0786", "lon": "-1.01694"}).json()
    assert c["id"] == "silverstone" and c["map"]["id"] == "gb-1948" and c["held"] == 61
    assert c["race"]["round"] == 9 and c["race"]["done"]
    # A Grand Prix circuit long off the calendar has no bundled outline; the page asks OpenStreetMap instead.
    brands = f1_api.get("/api/f1/circuit", params={"title": "Brands Hatch", "lat": "51.3569", "lon": "0.2631"}).json()
    assert brands["id"] == "brands_hatch" and brands["map"] == {}
    # Never a Grand Prix circuit at all.
    assert f1_api.get("/api/f1/circuit", params={"title": "Mount Panorama Circuit", "lat": "-33.4475", "lon": "149.5582"}).json() == {"map": {}}
    assert f1_api.get("/api/f1/circuit", params={"title": "X", "lat": "abc", "lon": "1"}).json() == {}


def test_every_circuit_on_the_calendar_has_a_bundled_outline():
    from archer.sources.f1 import circuit_map
    races = json.loads((JOLPICA_DIR / "current_limit_40.json").read_bytes())["MRData"]["RaceTable"]["Races"]
    for race in races:
        where = race["Circuit"]["Location"]
        assert circuit_map(where["lat"], where["long"]), race["raceName"]
    assert circuit_map("0", "0") == {} and circuit_map("x", "1") == {}


def test_a_season_finished_after_the_titles_table_is_asked_for_once(f1_api, monkeypatch):
    from datetime import datetime, timezone
    from archer.sources import f1
    monkeypatch.setattr(f1, "_now", lambda: datetime(2027, 3, 1, tzinfo=timezone.utc))
    monkeypatch.setitem(f1._CHAMPIONS, "driver", dict(f1.DRIVER_CHAMPIONS))
    asked = []

    def standings(request):
        asked.append(request.url.path)
        return httpx.Response(200, json={"MRData": {"StandingsTable": {"StandingsLists": [
            {"season": "2026", "DriverStandings": [{"Driver": {"driverId": "antonelli"}}]}]}}})
    ext = httpx.AsyncClient(transport=httpx.MockTransport(standings))
    assert asyncio.run(f1._titles(ext, "driver", "antonelli")) == [2026]
    assert asyncio.run(f1._titles(ext, "driver", "antonelli")) == [2026] and len(asked) == 1


def quali_grid(driver_id, given, family, position):
    return {"position": str(position), "number": "1", "Q1": "1:33.241", "Q2": "1:31.639", "Q3": "1:31.373",
            "Driver": {"driverId": driver_id, "permanentNumber": "1", "givenName": given, "familyName": family,
                       "code": family[:3].upper(), "url": f"https://en.wikipedia.org/wiki/{given}_{family}"},
            "Constructor": {"constructorId": "red_bull", "name": "Red Bull"}}


def calendar(request: httpx.Request) -> httpx.Response:
    """Jolpica's last grid, OpenStreetMap's raceways, and the usual Wikipedia/Wikidata behind them."""
    if request.url.host == "overpass-api.de":
        return httpx.Response(200, json={"elements": [
            {"type": "way", "geometry": [{"lat": 30.10 + i / 100, "lon": -97.70 + i / 50} for i in range(40)],
             "tags": {"highway": "raceway"}}]})
    if request.url.path.endswith("/last/qualifying.json"):
        return httpx.Response(200, json={"MRData": {"RaceTable": {"Races": [
            {"season": "2026", "round": "17", "raceName": "Singapore Grand Prix", "date": "2026-10-11",
             "time": "12:00:00Z", "Qualifying": {"date": "2026-10-10", "time": "13:00:00Z"}, "QualifyingResults": [
                 quali_grid("max_verstappen", "Max", "Verstappen", 1),
                 quali_grid("leclerc", "Charles", "Leclerc", 2)]}]}}})
    if request.url.host in ("en.wikipedia.org", "www.wikidata.org"):
        return motorsport_upstream(request)
    return jolpica(request)


def f1_caches():
    from archer.sources import f1
    f1._cache._data.clear()
    f1._LOOKUP.clear()
    f1._NAMES.clear()
    f1._track._data.clear()


def test_f1_qualifying_returns_the_grid_with_every_session_time(client):
    f1_caches()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(calendar))
    d = client.get("/api/f1/qualifying").json()
    assert d["name"] == "Singapore Grand Prix" and len(d["grid"]) == 2 and d["session"] == "2026-10-10T13:00:00Z"
    top = d["grid"][0]
    assert (top["position"], top["name"], top["team"], top["best"]) == (1, "Max Verstappen", "Red Bull", "1:31.373")
    assert top["times"] == {"Q1": "1:33.241", "Q2": "1:31.639", "Q3": "1:31.373"} and top["color"] == "#3671C6"


def test_f1_panel_arrives_as_skeletons_with_nothing_live_or_spoiling_in_it(client):
    """Standing, season, career and weekend are filled by archer.js; the qualifying grid is drawn only on a
    click, so opening the page on the Saturday after a session cannot spoil it."""
    f1_caches()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(calendar))
    html = client.get("/search", params={"q": "max verstappen"}).text
    for hook in ("data-f1-standing", "data-f1-form", "data-f1-career", "data-f1-next", "data-f1-quali", "data-f1-plate"):
        assert hook in html, hook
    assert "f1-quali-grid" not in html and "1:31.373" not in html
    assert "flagcdn.com/nl.svg" in html                        # the nationality flag
    car = client.get("/search", params={"q": "porsche 911"}).text
    assert "ms-facts" in car and "#b-car-porsche" in car and "From 1963" in car


def test_f1_track_draws_an_openstreetmap_outline_fitted_into_the_box(client):
    f1_caches()
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(calendar))
    d = client.get("/api/f1/track", params={"lat": "30.1328", "lon": "-97.6411", "id": "americas"}).json()
    assert d["path"].startswith("M")
    points = [float(v) for v in re.findall(r"-?\d+\.?\d*", d["path"])]
    xs, ys = points[0::2], points[1::2]
    assert 0 <= min(xs) and max(xs) <= 100 and 0 <= min(ys) and max(ys) <= 100
    assert client.get("/api/f1/track", params={"lat": "abc", "lon": "1"}).json() == {"path": ""}


def test_circuit_outline_is_thinned_but_keeps_its_whole_shape():
    from archer.sources.f1 import _outline
    ways = [[(30.0 + i / 500, -97.7 + i / 900) for i in range(600)] for _ in range(2)]
    path = _outline(ways)
    points = [float(v) for v in re.findall(r"-?\d+\.?\d*", path)]
    xs, ys = points[0::2], points[1::2]
    assert len(xs) <= 260, "thinned to roughly the budget"
    assert min(xs) > 2 and max(xs) < 98 and min(ys) > 2 and max(ys) < 98
    assert _outline([[(1.0, 1.0)]]) == ""                       # too little geometry to draw


def test_country_flags_map_from_names_and_nationalities():
    from archer.sources.f1 import flag
    assert flag("USA") == "https://flagcdn.com/us.svg" and flag("Dutch") == "https://flagcdn.com/nl.svg"
    assert flag("Great Britain") == "https://flagcdn.com/gb.svg" and flag("Atlantis") == ""


def test_facts_take_the_form_of_what_they_hold():
    from datetime import date
    from archer.facts import shape
    today = date(2026, 10, 18)
    released = shape({"label": "Released", "value": "2021", "sub": "22 October 2021"}, today)
    assert released["shape"] == "when" and released["ago"] == "5 years ago" and released["detail"] == "22 October 2021"
    aired = shape({"label": "Aired", "value": "2007–present", "sub": ""}, today)
    assert aired["shape"] == "span" and aired["ongoing"] and aired["length"] == "20 years" and aired["cells"] == 20
    long_run = shape({"label": "Production", "value": "1963–present"}, today)
    assert long_run["cells"] <= 30                       # a long span steps several years per cell
    height = shape({"label": "Height", "value": "178 cm"}, today)
    assert (height["shape"], height["num"], height["unit"]) == ("measure", "178", "cm")
    age = shape({"label": "Age", "value": "30 years", "sub": "1 September 1996"}, today)
    assert (age["num"], age["unit"], age["sub"]) == ("30", "years", "Born 1 September 1996")
    people = shape({"label": "Members", "value": "Ann Lee, Bo Ray, Cy Diaz, Di Fox"}, today)
    assert [p["initials"] for p in people["people"]] == ["AL", "BR", "CD"] and people["more"] == 1
    assert shape({"label": "Genre", "value": "drama, comedy"}, today)["tags"] == ["Drama", "Comedy"]
    assert shape({"label": "Born in", "value": "Oakland"}, today)["shape"] == "place"
    assert shape({"label": "Scientific name", "value": "Panthera leo"}, today)["shape"] == "text"


def test_entity_facts_are_shaped(client):
    html = client.get("/search", params={"q": "zendaya"}).text
    assert "fact--measure" in html and "fact--place" in html and "fact--people" in html
    assert 'class="monogram"' in html and ">TH<" in html                     # Tom Holland as a monogram link
    assert 'href="/search?q=Tom+Holland"' in html


def test_time_and_sun_draw_day_rails(client):
    html = client.get("/search", params={"q": "sunrise bengaluru"}).text
    rail = html.split('class="dayrails"')[1].split("</section>")[0]
    assert rail.count('class="dayrail is-sun"') == 2 and "is-day" in rail and "dayrail-now" in rail
    time = client.get("/search", params={"q": "time in bengaluru"}).text
    assert 'class="dayrails"' in time and "is-now" in time


def test_colour_has_shades_and_contrast(client):
    html = client.get("/search", params={"q": "#1a73e8"}).text
    assert html.count('class="swatch-step') == 9 and "swatch-step is-self" in html
    assert "White text: 4.5:1 contrast" in html and "Black text: 4.6:1 contrast" in html                                 # white text on #1a73e8


def test_weather_days_and_crypto_range_use_pixel_bars(client):
    assert html_has(client, "weather bengaluru", 'class="rangebar"')
    html = client.get("/search", params={"q": "bitcoin price"}).text
    assert "cx-range" in html and "rangebar-at" in html and "24h high" not in html   # the range replaces the two tiles


def html_has(client, q, marker):
    return marker in client.get("/search", params={"q": q}).text
