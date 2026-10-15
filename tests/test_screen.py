"""Cast and filmography sections: Wikidata + Wikipedia by default, TMDB when keyed. All mocked."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from archer.app import app
from archer.config import settings
from archer.searxng import SearXNG
from archer.sources import credits, wiki

from test_heroes import BASE, EMPTY_SEARCH, WP_PAGE, item, upstream


@pytest.fixture(autouse=True)
def clean_caches(monkeypatch):
    monkeypatch.setattr(settings, "tmdb_api_key", "")
    for cache in (credits._cast_cache, credits._films_cache, wiki._thumb_cache, wiki._entity_cache):
        cache._data.clear()
    monkeypatch.setattr(wiki, "_cooldown_until", 0.0)


def run(handler, coro_fn):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await coro_fn(client)
    return asyncio.run(go())


def snak_item(qid):
    return {"snaktype": "value", "datavalue": {"type": "wikibase-entityid", "value": {"id": qid}}}


def statement(qid, **qualifiers):
    return {"mainsnak": snak_item(qid), "rank": "normal", "qualifiers": qualifiers}


def thumbs_reply(request, mapping):
    titles = request.url.params["titles"].split("|")
    pages = [{"title": t, "pageprops": {"wikibase_item": "Q1"}, **({"thumbnail": {"source": mapping[t]}} if t in mapping else {})}
             for t in titles]
    return httpx.Response(200, json={"query": {"pages": pages}})


# ------------------------------------------------------------------ cast

FILM_CLAIMS = {"claims": {
    "P161": [statement("Q10", P453=[snak_item("Q20")]),
             statement("Q11", P4633=[{"snaktype": "value", "datavalue": {"type": "string", "value": "Chani"}}]),
             statement("Q10", P453=[snak_item("Q21")]),            # second role, same actor: merged
             {**statement("Q12"), "rank": "deprecated"}],
    "P57": [statement("Q30")], "P86": [statement("Q31")],
}}
ENTITIES = {"entities": {
    "Q10": {"labels": {"en": {"value": "Timothée Chalamet"}}, "sitelinks": {"enwiki": {"title": "Timothée Chalamet"}}},
    "Q11": {"labels": {"mul": {"value": "Zendaya"}}, "sitelinks": {"enwiki": {"title": "Zendaya"}}},
    "Q20": {"labels": {"en": {"value": "Paul Atreides"}}},
    "Q21": {"labels": {"en": {"value": "Muad'Dib"}}},
    "Q30": {"labels": {"en": {"value": "Denis Villeneuve"}}, "sitelinks": {"enwiki": {"title": "Denis Villeneuve"}}},
    "Q31": {"labels": {"en": {"value": "Hans Zimmer"}}, "sitelinks": {}},
}}


def test_wikidata_cast_keeps_billing_order_roles_and_photos():
    def handler(request):
        p = request.url.params
        if p.get("action") == "wbgetclaims":
            return httpx.Response(200, json=FILM_CLAIMS)
        if p.get("action") == "wbgetentities":
            assert p["sitefilter"] == "enwiki"
            return httpx.Response(200, json=ENTITIES)
        if request.url.host == "en.wikipedia.org":
            return thumbs_reply(request, {"Zendaya": "https://upload.wikimedia.org/z.jpg"})
        return httpx.Response(404)

    data = run(handler, lambda c: credits.cast(c, "Q1", "movie", "", ""))
    assert data["source"] == "wikidata"
    assert [(p["name"], p["role"]) for p in data["cast"]] == [("Timothée Chalamet", "Paul Atreides / Muad'Dib"), ("Zendaya", "Chani")]
    assert data["cast"][1]["photo"] == "https://upload.wikimedia.org/z.jpg" and data["cast"][0]["photo"] == ""
    assert [(c["name"], c["job"], c["q"]) for c in data["crew"]] == [("Denis Villeneuve", "Director", "Denis Villeneuve"),
                                                                    ("Hans Zimmer", "Music", "Hans Zimmer")]


TMDB_TV = {"created_by": [{"id": 5, "name": "Vince Gilligan", "profile_path": "/vg.jpg"}],
           "aggregate_credits": {
               "cast": [{"id": 2, "name": "Aaron Paul", "order": 1, "profile_path": "/ap.jpg", "total_episode_count": 62,
                         "roles": [{"character": "Jesse Pinkman", "episode_count": 62}]},
                        {"id": 1, "name": "Bryan Cranston", "order": 0, "profile_path": None, "total_episode_count": 62,
                         "roles": [{"character": "Walter White", "episode_count": 62}, {"character": "Heisenberg", "episode_count": 3}]}],
               "crew": [{"id": 7, "name": "Dave Porter", "jobs": [{"job": "Original Music Composer"}]},
                        {"id": 8, "name": "Some Director", "jobs": [{"job": "Director"}]}]}}


def test_tmdb_cast_for_a_series_links_people_to_their_articles(monkeypatch):
    def handler(request):
        if request.url.host == "api.themoviedb.org":
            assert request.url.path == "/3/tv/1396" and request.url.params["append_to_response"] == "aggregate_credits"
            return httpx.Response(200, json=TMDB_TV)
        if request.url.host == "query.wikidata.org":
            assert "P4985" in request.url.params["query"]
            return httpx.Response(200, json={"results": {"bindings": [
                {"prop": {"value": "P4985"}, "id": {"value": "1"}, "article": {"value": "https://en.wikipedia.org/wiki/Bryan_Cranston"}}]}})
        return httpx.Response(404)

    data = run(handler, lambda c: credits.cast(c, "Q1079", "tv", "1396", "v3key"))
    assert data["source"] == "tmdb"
    walter, jesse = data["cast"]
    assert walter["name"] == "Bryan Cranston" and walter["role"] == "Walter White / Heisenberg" and walter["episodes"] == 62
    assert walter["q"] == "Bryan Cranston" and walter["photo"] == ""
    assert jesse["photo"] == "https://image.tmdb.org/t/p/w185/ap.jpg"
    # A series lists its creator and composer, not its many episode directors.
    assert [(c["name"], c["job"]) for c in data["crew"]] == [("Vince Gilligan", "Creator"), ("Dave Porter", "Music")]


def test_tmdb_failure_falls_back_to_wikidata():
    def handler(request):
        if request.url.host == "api.themoviedb.org":
            return httpx.Response(401, json={"status_message": "Invalid API key"})
        if request.url.params.get("action") == "wbgetclaims":
            return httpx.Response(200, json=FILM_CLAIMS)
        if request.url.params.get("action") == "wbgetentities":
            return httpx.Response(200, json=ENTITIES)
        return thumbs_reply(request, {})

    data = run(handler, lambda c: credits.cast(c, "Q1", "movie", "438631", "badkey"))
    assert data["source"] == "wikidata" and data["cast"][0]["name"] == "Timothée Chalamet"


# ----------------------------------------------------------- filmography

def sparql_row(work, title, kind, when, role="", page="", links="10"):
    row = {"work": {"value": f"http://www.wikidata.org/entity/{work}"}, "title": {"value": title},
           "kind": {"value": f"http://www.wikidata.org/entity/{kind}"}, "pop": {"value": links}}
    if when:
        row["date"] = {"value": when}
    if role:
        row["role"] = {"value": role}
    if page:
        row["page"] = {"value": "https://en.wikipedia.org/wiki/" + page.replace(" ", "_")}
    return row


def test_wikidata_filmography_has_posters_roles_and_article_queries():
    rows = [sparql_row("Q1", "Dune", "Q11424", "2021-10-22T00:00:00Z", "Chani", "Dune (2021 film)", "80"),
            sparql_row("Q2", "Euphoria", "Q5398426", "2019-06-16T00:00:00Z", "Rue Bennett", "Euphoria (American TV series)", "60"),
            sparql_row("Q3", "Future Film", "Q11424", "2099-01-01T00:00:00Z")]

    def handler(request):
        if request.url.host == "query.wikidata.org":
            assert "wd:Q189489" in request.url.params["query"]
            return httpx.Response(200, json={"results": {"bindings": rows}})
        if request.url.host == "en.wikipedia.org":
            assert request.url.params["pilicense"] == "any"   # posters are non-free
            return thumbs_reply(request, {"Dune (2021 film)": "https://upload.wikimedia.org/dune.jpg"})
        return httpx.Response(404)

    data = run(handler, lambda c: credits.filmography(c, "Q189489", "", ""))
    items = {i["title"]: i for i in data["items"]}
    assert data["source"] == "wikidata" and list(items) == ["Future Film", "Dune", "Euphoria"]   # newest first
    assert items["Dune"] == {"title": "Dune", "date": "2021-10-22", "year": "2021", "media": "movie", "role": "Chani",
                             "episodes": 0, "pop": 80, "upcoming": False,
                             "poster": "https://upload.wikimedia.org/dune.jpg", "q": "Dune (2021 film)"}
    assert items["Euphoria"]["media"] == "tv" and items["Euphoria"]["poster"] == ""
    assert items["Future Film"]["upcoming"] and items["Future Film"]["q"] == "Future Film"


TMDB_CREDITS = {"cast": [
    {"media_type": "movie", "id": 438631, "title": "Dune", "release_date": "2021-09-15", "character": "Chani",
     "poster_path": "/dune.jpg", "vote_count": 12000, "genre_ids": [878]},
    {"media_type": "tv", "id": 85552, "name": "Euphoria", "first_air_date": "2019-06-16", "character": "Rue Bennett",
     "poster_path": "/eu.jpg", "vote_count": 9000, "episode_count": 18, "genre_ids": [18]},
    {"media_type": "tv", "id": 85552, "name": "Euphoria", "first_air_date": "2019-06-16", "character": "Narrator",
     "episode_count": 2, "genre_ids": [18]},
    {"media_type": "tv", "id": 22980, "name": "The Tonight Show", "first_air_date": "2014-02-17", "character": "Self - Guest",
     "genre_ids": [10767]},
    {"media_type": "movie", "id": 99, "title": "A Documentary", "release_date": "2020-01-01", "character": "Herself",
     "genre_ids": [99]},
]}


def test_tmdb_filmography_drops_appearances_and_merges_roles():
    def handler(request):
        if request.url.host == "api.themoviedb.org":
            assert request.url.path == "/3/person/505710/combined_credits"
            assert request.headers["authorization"] == "Bearer eyJtoken"
            return httpx.Response(200, json=TMDB_CREDITS)
        if request.url.host == "query.wikidata.org":
            return httpx.Response(200, json={"results": {"bindings": [
                {"prop": {"value": "P4947"}, "id": {"value": "438631"}, "article": {"value": "https://en.wikipedia.org/wiki/Dune_(2021_film)"}}]}})
        return httpx.Response(404)

    data = run(handler, lambda c: credits.filmography(c, "Q189489", "505710", "eyJtoken"))
    assert data["source"] == "tmdb"
    assert [(i["title"], i["q"]) for i in data["items"]] == [("Dune", "Dune (2021 film)"), ("Euphoria", "Euphoria")]
    euphoria = data["items"][1]
    assert euphoria["role"] == "Rue Bennett / Narrator" and euphoria["episodes"] == 20
    assert data["items"][0]["poster"] == "https://image.tmdb.org/t/p/w342/dune.jpg"


# ------------------------------------------------------------- pages + API

@pytest.fixture
def client():
    with TestClient(app, base_url=BASE) as c:
        app.state.searx = SearXNG(httpx.AsyncClient(base_url=settings.searxng_url,
                                                    transport=httpx.MockTransport(lambda r: httpx.Response(200, json=EMPTY_SEARCH))))
        yield c


def film_upstream(request):
    if request.url.host == "en.wikipedia.org" and "titles" in request.url.params and request.url.params.get("prop", "").startswith("pageprops|pageimages"):
        page = {**WP_PAGE["query"]["pages"][0], "title": "Dune (2021 film)"}
        return httpx.Response(200, json={"query": {"pages": [page]}})
    if request.url.host == "www.wikidata.org" and request.url.params.get("props", "").startswith("claims"):
        string = lambda v: {"mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": v}}, "rank": "normal"}
        return httpx.Response(200, json={"entities": {"Q189489": {"claims": {
            "P31": [item("Q11424")], "P161": [item("Q2023710")], "P4947": [string("438631")], "P1874": [string("81037848")]}}}})
    return upstream(request)


def test_film_page_has_cast_section_instead_of_name_chips(client):
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(film_upstream))
    html = client.get("/search", params={"q": "dune 2021 film"}).text
    assert "hero-entity--film" in html
    section = html.split('class="screen screen-cast"')[1].split("</section>")[0]
    assert 'data-cast-qid="Q189489"' in section and 'data-cast-media="movie"' in section and 'data-cast-tmdb="438631"' in section
    assert "Tom Holland" in section          # Wikidata's names render before the photos load
    assert "about-cast" not in html
    # No TMDB key: Netflix from Wikidata, drawn with its own mark.
    assert 'href="#b-netflix"' in html and "https://www.netflix.com/title/81037848" in html


def test_cast_and_filmography_endpoints_validate_ids(client):
    app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert client.get("/api/cast", params={"qid": "Q1 OR 1=1"}).status_code == 400
    assert client.get("/api/cast", params={"qid": "Q1", "media": "book"}).status_code == 400
    assert client.get("/api/filmography", params={"qid": "Q1", "tmdb": "12a"}).status_code == 400
    # Upstream failures come back as an empty, well-formed payload.
    assert client.get("/api/cast", params={"qid": "Q1"}).json()["cast"] == []
    assert client.get("/api/filmography", params={"qid": "Q1"}).json()["items"] == []
