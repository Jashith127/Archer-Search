"""Render every hero template against canned upstream data, so template bugs fail here, not in the browser."""

import asyncio
import json
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
