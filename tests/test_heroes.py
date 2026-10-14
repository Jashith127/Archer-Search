"""Render every hero template against canned upstream data, so template bugs fail here, not in the browser."""

import httpx
import pytest
from fastapi.testclient import TestClient

from archer.app import app
from archer.config import settings
from archer.searxng import SearXNG
from archer.sources import crypto, dictionary, places, translate, weather, wiki

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
def client():
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
    assert 'data-works-role="cast"' in html


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


def test_results_header_has_no_branding(client):
    html = client.get("/search", params={"q": "zendaya"}).text
    assert 'class="brand"' not in html and 'class="kbd"' in html


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
