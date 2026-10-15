"""Isolated cast + filmography fixture: canned Wikipedia/Wikidata replies, zero external calls.

Run `uv run python tests/screen_browser_server.py`, then open
http://127.0.0.1:4751/search?q=dune (film: cast, crew, where to watch),
http://127.0.0.1:4751/search?q=skins (series with one service and very tall pictures) or
http://127.0.0.1:4751/search?q=zendaya (actor: poster grid). Posters and photos are generated
SVG placeholders served by this script; the names are real, everything else is made up.
"""
import hashlib
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import Response

from archer.app import app
from archer.searxng import SearXNG
from archer.sources import wiki

PORT = 4751
ART = f"http://localhost:{PORT}/fixture-art/"

CAST = [("Timothée Chalamet", "Paul Atreides"), ("Rebecca Ferguson", "Lady Jessica"), ("Oscar Isaac", "Duke Leto Atreides"),
        ("Josh Brolin", "Gurney Halleck"), ("Stellan Skarsgård", "Baron Vladimir Harkonnen"), ("Dave Bautista", "Glossu Rabban"),
        ("Stephen McKinley Henderson", "Thufir Hawat"), ("Zendaya", "Chani"), ("Chang Chen", "Dr. Wellington Yueh"),
        ("Sharon Duncan-Brewster", "Dr. Liet Kynes"), ("Charlotte Rampling", "Reverend Mother Mohiam"), ("Jason Momoa", "Duncan Idaho"),
        ("Javier Bardem", "Stilgar"), ("David Dastmalchian", "Piter De Vries"), ("Babs Olusanmokun", "Jamis"),
        ("Golda Rosheuvel", "Shadout Mapes"), ("Benjamin Clémentine", "Herald of the Change"), ("Souad Faress", "Bene Gesserit Sister")]
CREW = [("P57", "Denis Villeneuve"), ("P58", "Jon Spaihts"), ("P58", "Eric Roth"), ("P86", "Hans Zimmer"), ("P344", "Greig Fraser")]
FILMS = [("Dune: Part Two", "Q11424", "2024-03-01", "Chani", 90), ("Challengers", "Q11424", "2024-04-26", "Tashi Duncan", 70),
         ("Euphoria", "Q5398426", "2019-06-16", "Rue Bennett", 85), ("Dune", "Q11424", "2021-10-22", "Chani", 95),
         ("Spider-Man: Homecoming", "Q11424", "2017-07-07", "Michelle Jones", 88), ("Spider-Man: Far From Home", "Q11424", "2019-07-02", "MJ", 84),
         ("Spider-Man: No Way Home", "Q11424", "2021-12-17", "MJ", 92), ("The Greatest Showman", "Q11424", "2017-12-20", "Anne Wheeler", 75),
         ("Malcolm & Marie", "Q11424", "2021-02-05", "Marie", 40), ("Space Jam: A New Legacy", "Q11424", "2021-07-16", "Lola Bunny (voice)", 50),
         ("Smallfoot", "Q11424", "2018-09-28", "Meechee (voice)", 35), ("Shake It Up", "Q5398426", "2010-11-07", "Rocky Blue", 30),
         ("K.C. Undercover", "Q5398426", "2015-01-18", "K.C. Cooper", 25), ("Frenemies", "Q506240", "2012-01-13", "Halley Brandon", 10),
         ("Zapped", "Q506240", "2014-06-27", "Zoey Stevens", 12), ("The Odyssey", "Q11424", "2026-07-17", "", 60),
         ("Untitled Project", "Q11424", "", "", 1)]


@app.get("/fixture-art/{name}.svg")
@app.get("/fixture-art/{name}.jpg")   # image search drops .svg URLs as icons
def fixture_art(name: str) -> Response:
    """Placeholder art. "tall-*" is an unreasonably tall picture (1:3.5) whose subject sits low
    on a flat background, to exercise cropping that follows the content."""
    hue = int(hashlib.md5(name.encode()).hexdigest()[:2], 16) * 360 // 256
    if name.startswith("tall"):
        dots = "".join(f'<circle cx="{40 + 30 * i}" cy="{470 + (i % 2) * 40}" r="26" fill="hsl({(hue + 40 * i) % 360},70%,60%)"/>' for i, _ in enumerate(range(5)))
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 700"><rect width="200" height="700" fill="hsl({hue},20%,12%)"/>'
               f'<rect x="20" y="400" width="160" height="190" rx="12" fill="hsl({hue},45%,30%)"/>{dots}'
               f'<text x="100" y="560" font-size="34" font-family="sans-serif" font-weight="700" text-anchor="middle" fill="#fff">skins</text></svg>')
        return Response(svg, media_type="image/svg+xml")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="hsl({hue},55%,42%)"/><stop offset="1" stop-color="hsl({(hue + 60) % 360},45%,14%)"/></linearGradient></defs>'
           f'<rect width="200" height="300" fill="url(#g)"/><circle cx="100" cy="110" r="46" fill="rgba(255,255,255,.18)"/></svg>')
    return Response(svg, media_type="image/svg+xml")


def value(v):
    if isinstance(v, str) and v.startswith("Q"):
        return {"snaktype": "value", "datavalue": {"type": "wikibase-entityid", "value": {"id": v}}}
    return {"snaktype": "value", "datavalue": {"type": "string", "value": v}}


def claim(v, **quals):
    return {"mainsnak": value(v), "rank": "normal", "qualifiers": {k: [value(x)] for k, x in quals.items()}}


PEOPLE = {f"Q{100 + i}": name for i, (name, _) in enumerate(CAST)} | {f"Q{200 + i}": name for i, (_, name) in enumerate(CREW)}
FILM_CLAIMS = {"P31": [claim("Q11424")], "P4947": [claim("438631")], "P1874": [claim("81037848")], "P8055": [claim("B09XYZ")],
               "P9586": [claim("umc.cmc.dune")],
               "P161": [claim(f"Q{100 + i}", P4633=role) for i, (_, role) in enumerate(CAST)],
               **{prop: [claim(f"Q{200 + i}") for i, (p, _) in enumerate(CREW) if p == prop] for prop, _ in CREW}}
ACTOR_CLAIMS = {"P31": [claim("Q5")], "P106": [claim("Q33999")], "P19": [claim("Q17042")],
                "P569": [{"mainsnak": {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": "+1996-09-01T00:00:00Z", "precision": 11}}}, "rank": "normal"}]}
def when(text):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": text, "precision": 11}}}, "rank": "normal"}


def amount(n):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "quantity", "value": {"amount": f"+{n}", "unit": "1"}}}, "rank": "normal"}


# One streaming service only: the compact where-to-watch layout from the Skins screenshot.
SERIES_CLAIMS = {"P31": [claim("Q5398426")], "P1874": [claim("70136112")], "P580": [when("+2007-01-25T00:00:00Z")],
                 "P582": [when("+2013-08-05T00:00:00Z")], "P2437": [amount(7)], "P1113": [amount(61)], "P136": [claim("Q1366112")],
                 "P161": FILM_CLAIMS["P161"][:12]}
PAGES = {"Skins (British TV series)": ("Q3", "British teen drama television series (2007–2013)", "Skins is a British teen drama television series."),
         "Dune (2021 film)": ("Q1", "2021 film by Denis Villeneuve", "Dune is a 2021 American epic science fiction film."),
         "Zendaya": ("Q2", "American actress and singer", "Zendaya Maree Stoermer Coleman is an American actress and singer.")}


def reply(request: httpx.Request) -> httpx.Response:
    host, p = request.url.host, request.url.params
    if host == "en.wikipedia.org":
        titles = p.get("titles", "")
        if p.get("prop", "").startswith("pageimages"):   # batched thumbnails
            # Every fifth one has no picture, to show the fallbacks.
            pages = [{"title": t, "pageprops": {"wikibase_item": "Q9"},
                      **({} if i % 5 == 4 else {"thumbnail": {"source": ART + hashlib.md5(t.encode()).hexdigest()[:8] + ".svg"}})}
                     for i, t in enumerate(titles.split("|"))]
            return httpx.Response(200, json={"query": {"pages": pages}})
        asked = (titles + p.get("gsrsearch", "")).lower()
        key = "Zendaya" if "zendaya" in asked else "Skins (British TV series)" if "skins" in asked else "Dune (2021 film)"
        qid, desc, extract = PAGES[key]
        page = {"title": key, "fullurl": "https://en.wikipedia.org/wiki/" + key.replace(" ", "_"), "description": desc,
                "extract": extract, "pageprops": {"wikibase_item": qid}}
        if not key.startswith("Skins"):   # Skins: no page image, so the collage fills from image search
            page["original"] = {"source": ART + key[:4] + ".svg", "width": 600, "height": 900}
        return httpx.Response(200, json={"query": {"pages": [page]}})
    if host == "www.wikidata.org":
        if p.get("action") == "wbgetclaims":
            if p.get("property"):
                return httpx.Response(200, json={"claims": {}})
            return httpx.Response(200, json={"claims": SERIES_CLAIMS if p.get("entity") == "Q3" else FILM_CLAIMS})
        if p.get("props", "").startswith("claims"):
            ids = p["ids"]
            return httpx.Response(200, json={"entities": {ids: {"claims": {"Q2": ACTOR_CLAIMS, "Q3": SERIES_CLAIMS}.get(ids, FILM_CLAIMS)}}})
        ents = {}
        for qid in p["ids"].split("|"):
            name = PEOPLE.get(qid, {"Q33999": "actor", "Q17042": "Oakland", "Q11424": "film"}.get(qid, qid))
            ents[qid] = {"labels": {"en": {"value": name}}, "sitelinks": {"enwiki": {"title": name}} if qid in PEOPLE else {}}
        return httpx.Response(200, json={"entities": ents})
    if host == "query.wikidata.org":
        rows = []
        for i, (title, kind, when, role, links) in enumerate(FILMS):
            row = {"work": {"value": f"http://www.wikidata.org/entity/Q{900 + i}"}, "title": {"value": title},
                   "kind": {"value": f"http://www.wikidata.org/entity/{kind}"}, "pop": {"value": str(links)},
                   "page": {"value": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")}}
            if when:
                row["date"] = {"value": when + "T00:00:00Z"}
            if role:
                row["role"] = {"value": role}
            rows.append(row)
        return httpx.Response(200, json={"results": {"bindings": rows}})
    if request.url.path == "/search":
        if "images" in p.get("categories", ""):
            return httpx.Response(200, json={"results": [
                {"url": f"https://example.com/still-{i}", "title": f"Skins poster {i}", "img_src": f"{ART}tall-{i}.jpg",
                 "resolution": "600 x 2100", "engine": "fixture"} for i in range(4)], "suggestions": []})
        return httpx.Response(200, json={"results": [], "suggestions": []})
    return httpx.Response(404)


async def fixture_thumbs(client, titles, size=160, *, nonfree=False):
    """Local placeholder art (the real helper only accepts https Wikimedia URLs). Every fifth
    title has no picture, to show the fallbacks."""
    # Every third person photo is unreasonably tall, too.
    return {t: ART + ("tall-" if i % 3 == 1 else "") + hashlib.md5(t.encode()).hexdigest()[:8] + ".svg"
            for i, t in enumerate(titles) if i % 5 != 4}


@asynccontextmanager
async def lifespan(application):
    async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(reply)) as client:
        application.state.searx = SearXNG(client)
        application.state.ext = client
        yield


if __name__ == "__main__":
    wiki.thumbs = fixture_thumbs
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
