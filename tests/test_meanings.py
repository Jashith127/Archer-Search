"""Ambiguous names: the panel follows what the web results say people mean, with the rest as chips. All mocked."""

import httpx
import pytest
from fastapi.testclient import TestClient

from archer.app import app
from archer.config import settings
from archer.query import classify
from archer.searxng import SearXNG
from archer.sources import wiki

from test_heroes import BASE, EMPTY_SEARCH, item

# title -> (qid, P31, description)
PAGES = {
    "Dune": ("Q1", "Q35666", "Hill of loose sand built by aeolian processes"),
    "Dune (2021 film)": ("Q2", "Q11424", "2021 film by Denis Villeneuve"),
    "Archery": ("Q3", "Q31629", "Using a bow to shoot arrows"),
    "Archer (2009 TV series)": ("Q4", "Q5398426", "American adult animated sitcom"),
    "Apple": ("Q5", "Q89", "Edible fruit"),
    "Apple Inc.": ("Q6", "Q4830453", "American technology company"),
}
BY_QID = {qid: (title, p31) for title, (qid, p31, _) in PAGES.items()}
REDIRECTS = {"archer": "Archery", "dune": "Dune", "apple": "Apple"}
SEARCH = {"archer TV series": "Archer (2009 TV series)"}


def wp(title):
    return "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")


def result(url, title):
    return {"url": url, "title": title, "content": title, "engine": "duckduckgo", "engines": ["duckduckgo"]}


WEB = {
    "dune": [result(wp("Dune (2021 film)"), "Dune (2021 film) - Wikipedia"), result(wp("Dune"), "Dune - Wikipedia")],
    "ARCHER": [result(wp("Archer (2009 TV series)"), "Archer (2009 TV series) - Wikipedia")],
    "archer show": [result("https://www.fxnetworks.com/shows/archer", "Official site")],
    "apple": [result("https://www.apple.com/in/", "Apple (India)"), result(wp("Apple"), "Apple - Wikipedia")],
}


def page(title):
    qid, _, desc = PAGES[title]
    return {"title": title, "fullurl": wp(title), "description": desc, "extract": desc + ".", "pageprops": {"wikibase_item": qid}}


def upstream(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    if request.url.host == "en.wikipedia.org":
        asked = p.get("titles") or ""
        title = REDIRECTS.get(asked.lower(), asked) if asked else SEARCH.get(p.get("gsrsearch", ""), "")
        return httpx.Response(200, json={"query": {"pages": [page(title)] if title in PAGES else [{"title": asked, "missing": True}]}})
    if request.url.host == "www.wikidata.org":
        if p.get("list") == "search":
            assert "P856=https://www.apple.com/" in p["srsearch"]
            return httpx.Response(200, json={"query": {"search": [{"title": "Q6"}]}})
        if p.get("props") == "sitelinks":
            return httpx.Response(200, json={"entities": {"Q6": {"sitelinks": {"enwiki": {"title": "Apple Inc."}}}}})
        if p.get("props", "").startswith("claims"):
            return httpx.Response(200, json={"entities": {p["ids"]: {"claims": {"P31": [item(BY_QID[p["ids"]][1])]}}}})
        return httpx.Response(200, json={"entities": {}})
    return httpx.Response(404)


@pytest.fixture
def client():
    wiki._entity_cache._data.clear()

    def searx(request):
        return httpx.Response(200, json={**EMPTY_SEARCH, "results": WEB.get(request.url.params.get("q"), [])})

    with TestClient(app, base_url=BASE) as c:
        app.state.searx = SearXNG(httpx.AsyncClient(base_url=settings.searxng_url, transport=httpx.MockTransport(searx)))
        app.state.ext = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        yield c


def hero_title(html):
    return html.split('class="hero-title">')[1].split("<")[0]


def test_kind_words_become_a_hint():
    q = classify("archer show", "inr")
    assert (q.entity_candidate, q.entity_hint) == ("archer", ("tv", "TV series"))
    assert classify("dune movie", "inr").entity_hint == ("film", "film")
    assert classify("show me the money", "inr").entity_hint is None


def test_web_results_pick_the_film_over_the_landform(client):
    html = client.get("/search", params={"q": "dune"}).text
    assert hero_title(html) == "Dune (2021 film)" and "hero-entity--film" in html
    meanings = html.split('class="hero-meanings"')[1].split("</nav>")[0]
    assert "about=Dune&" in meanings.replace('"', "&") and "Hill of loose sand" in meanings


def test_redirect_to_archery_loses_to_the_series_people_search_for(client):
    assert hero_title(client.get("/search", params={"q": "ARCHER"}).text) == "Archer (2009 TV series)"


def test_show_hint_finds_the_series_without_web_help(client):
    html = client.get("/search", params={"q": "archer show"}).text
    assert hero_title(html) == "Archer (2009 TV series)" and "hero-entity--tv" in html


def test_brand_site_ranked_first_means_the_company(client):
    html = client.get("/search", params={"q": "apple"}).text
    assert hero_title(html) == "Apple Inc." and "hero-entity--org" in html
    assert "Edible fruit" in html.split('class="hero-meanings"')[1]


def test_a_meaning_chip_pins_that_article(client):
    html = client.get("/search", params={"q": "dune", "about": "Dune"}).text
    assert hero_title(html) == "Dune"
    assert "about=Dune+%282021+film%29" in html   # and the film is one click away


def test_quick_answer_has_a_centred_label_not_a_badge(client):
    def searx(request):
        return httpx.Response(200, json={**EMPTY_SEARCH, "answers": [
            {"engine": "duckduckgo", "answer": "Archer is an American adult animated sitcom created by Adam Reed.",
             "url": "https://en.wikipedia.org/wiki/Archer"}]})
    app.state.searx = SearXNG(httpx.AsyncClient(base_url=settings.searxng_url, transport=httpx.MockTransport(searx)))
    html = client.get("/search", params={"q": "what does archer mean in tv"}).text
    answer = html.split('class="hero hero-answer"')[1].split("</section>")[0]
    assert '<p class="answer-label">Quick answer</p>' in answer and "hero-badge" not in answer
