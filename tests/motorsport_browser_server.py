"""Isolated motorsport fixture: canned Wikipedia/Wikidata and recorded Jolpica replies, zero external calls.

Run `uv run python tests/motorsport_browser_server.py`, then open http://127.0.0.1:4753/search?q= with
lewis hamilton (driver: standing, season chart, career, next round with the track map),
scuderia ferrari (team: line-up, points per round), michael schumacher (retired: career only),
monaco grand prix (this season's round, the circuit, winners there), silverstone circuit (circuit panel),
porsche 911 (car) or 24 hours of le mans (racing series).

Jolpica replies are real responses recorded in tests/fixtures/jolpica (the 2026 season after round 17);
pictures are generated placeholders served by this script. The clock is real, so the next round and the
countdown depend on today's date against that season.
"""
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import Response

from archer.app import app
from archer.searxng import SearXNG
from archer.sources import f1, wiki

PORT = 4753
ART = f"http://localhost:{PORT}/fixture-art/"
JOLPICA = Path(__file__).with_name("fixtures") / "jolpica"


def snak(v):
    if isinstance(v, dict):
        return {"snaktype": "value", "datavalue": {"type": "globecoordinate", "value": v}}
    if isinstance(v, str) and v.startswith("Q"):
        return {"snaktype": "value", "datavalue": {"type": "wikibase-entityid", "value": {"id": v}}}
    return {"snaktype": "value", "datavalue": {"type": "string", "value": v}}


def claim(v, **quals):
    return {"mainsnak": snak(v), "rank": "normal", "qualifiers": {k: [snak(x)] for k, x in quals.items()}}


def when(text, **quals):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": text, "precision": 11}}},
            "rank": "normal", "qualifiers": quals}


def ended(qid, year):
    return {"mainsnak": snak(qid), "rank": "normal", "qualifiers": {"P582": [
        {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": f"+{year}-12-31T00:00:00Z", "precision": 9}}}]}}


def quantity(amount, unit="1"):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "quantity", "value": {
        "amount": f"+{amount}", "unit": "http://www.wikidata.org/entity/" + unit if unit != "1" else "1"}}}, "rank": "normal"}


CLAIMS = {
    "Q9673": {"P31": [claim("Q5")], "P106": [claim("Q10349745"), claim("Q10841764")],
              "P54": [ended("Q172030", 2012), ended("Q65954812", 2024), claim("Q169898")],
              "P569": [when("+1985-01-07T00:00:00Z")], "P27": [claim("Q145")], "P19": [claim("Q211520")],
              "P2048": [quantity("1.74", "Q11573")]},
    "Q9671": {"P31": [claim("Q5")], "P106": [claim("Q10841764")],
              "P54": [ended("Q47099", 1991), ended("Q166960", 1995), ended("Q169898", 2006), ended("Q65954812", 2012)],
              "P569": [when("+1969-01-03T00:00:00Z")], "P27": [claim("Q183")], "P19": [claim("Q4084")]},
    "Q169898": {"P31": [claim("Q10497835")], "P641": [claim("Q5386")], "P17": [claim("Q38")],
                "P571": [when("+1929-11-16T00:00:00Z")], "P159": [claim("Q9178")], "P1037": [claim("Q3087316")],
                "P127": [claim("Q27586")]},
    "Q9102": {"P31": [claim("Q108886259")], "P641": [claim("Q5367")], "P17": [claim("Q235")],
              "P571": [when("+1929-04-14T00:00:00Z")], "P276": [claim("Q171400")]},
    "Q171402": {"P31": [claim("Q2338524")], "P17": [claim("Q145")], "P131": [claim("Q23115")],
                "P625": [claim({"latitude": 52.0786, "longitude": -1.01694})],
                "P2043": [quantity("5.891", "Q828224")], "P1619": [when("+1948-10-02T00:00:00Z")],
                "P1083": [quantity("164000")], "P127": [claim("Q1063397")]},
    "Q219357": {"P31": [claim("Q3231690")], "P176": [claim("Q40993")], "P279": [claim("Q11030")],
                "P571": [when("+1963-09-12T00:00:00Z")], "P2052": [quantity("340", "Q180154")],
                "P1092": [quantity("1500000")], "P155": [claim("Q177250")], "P17": [claim("Q183")]},
    "Q187579": {"P31": [claim("Q18608583")], "P641": [claim("Q1805420")], "P279": [claim("Q1805420")],
                "P571": [when("+1923-05-26T00:00:00Z")], "P17": [claim("Q142")], "P1346": [claim("Q310687")],
                "P276": [claim("Q1067346")]},
}
LABELS = {
    "Q5": "human", "Q10349745": "racing automobile driver", "Q10841764": "Formula One driver", "Q172030": "McLaren",
    "Q65954812": "Mercedes-Benz in Formula One", "Q169898": "Scuderia Ferrari", "Q145": "United Kingdom",
    "Q211520": "Stevenage", "Q11573": "metre", "Q47099": "Jordan Grand Prix", "Q166960": "Benetton Formula",
    "Q183": "Germany", "Q4084": "Hürth", "Q10497835": "Formula One team", "Q5386": "auto racing", "Q38": "Italy",
    "Q9178": "Maranello", "Q3087316": "Frédéric Vasseur", "Q27586": "Ferrari", "Q108886259": "Formula One event",
    "Q5367": "motorsport", "Q235": "Monaco", "Q171400": "Circuit de Monaco", "Q2338524": "motorsport racing track",
    "Q23115": "Northamptonshire", "Q828224": "kilometre", "Q1063397": "British Racing Drivers' Club",
    "Q3231690": "car model", "Q40993": "Porsche", "Q11030": "sports car", "Q180154": "kilometre per hour",
    "Q177250": "Porsche 356", "Q18608583": "recurring sporting event", "Q1805420": "endurance racing",
    "Q142": "France", "Q310687": "Ferrari 499P", "Q1067346": "Circuit de la Sarthe",
}
PAGES = {
    "Lewis Hamilton": ("Q9673", "British racing driver (born 1985)", "tall",
                       "Sir Lewis Carl Davidson Hamilton is a British racing driver who competes in Formula One for Ferrari. "
                       "He has won a joint-record seven Formula One World Drivers' Championship titles."),
    "Michael Schumacher": ("Q9671", "German racing driver (born 1969)", "tall",
                           "Michael Schumacher is a German former racing driver who competed in Formula One for Jordan, "
                           "Benetton, Ferrari and Mercedes."),
    "Scuderia Ferrari": ("Q169898", "Racing division of Ferrari", "wide",
                         "Scuderia Ferrari is the racing division of luxury Italian auto manufacturer Ferrari and the racing "
                         "team that competes in Formula One racing."),
    "Monaco Grand Prix": ("Q9102", "Formula One motor race held in Monaco", "wide",
                          "The Monaco Grand Prix is a Formula One motor racing event held annually on the Circuit de Monaco."),
    "Silverstone Circuit": ("Q171402", "Motor racing circuit in England", "wide",
                            "Silverstone Circuit is a motor racing circuit in England, near the Northamptonshire villages of "
                            "Silverstone and Whittlebury. It is the home of the British Grand Prix."),
    "Porsche 911": ("Q219357", "Sports car produced by Porsche", "wide", "The Porsche 911 is a two-door sports car."),
    "24 Hours of Le Mans": ("Q187579", "Annual sports car race held in France", "wide",
                            "The 24 Hours of Le Mans is an endurance-focused sports car race held annually near Le Mans."),
}


@app.get("/fixture-art/{name}.svg")
def fixture_art(name: str) -> Response:
    if name.startswith("logo"):         # a stand-in team badge: a shield in yellow and red
        return Response('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 80"><path d="M4 4h52v40c0 18-14 28-26 32C18 72 4 62 4 44z" '
                        'fill="#ffd400"/><rect x="4" y="4" width="52" height="10" fill="#1a8a3a"/><rect x="22" y="4" width="16" height="10" '
                        'fill="#fff"/><path d="M24 30c6-6 10-2 12 4s-2 14-6 18l-4-6z" fill="#111"/></svg>', media_type="image/svg+xml")
    hue = int(hashlib.md5(name.encode()).hexdigest()[:2], 16) * 360 // 256
    w, h = (600, 800) if name.startswith("tall") else (1200, 700)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="hsl({hue},40%,30%)"/><stop offset="1" stop-color="hsl({(hue + 50) % 360},35%,10%)"/></linearGradient></defs>'
           f'<rect width="{w}" height="{h}" fill="url(#g)"/><circle cx="{w / 2}" cy="{h * .38}" r="{min(w, h) * .2}" fill="rgba(255,255,255,.16)"/>'
           f'<rect x="{w * .25}" y="{h * .62}" width="{w * .5}" height="{h * .5}" rx="{w * .2}" fill="rgba(255,255,255,.1)"/></svg>')
    return Response(svg, media_type="image/svg+xml")


def jolpica(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/ergast/f1/", 1)[-1].removesuffix(".json")
    params = dict(request.url.params)
    if path in ("drivers", "constructors"):          # the full lists, only Schumacher matters here
        rows = [{"driverId": "michael_schumacher", "url": "https://en.wikipedia.org/wiki/Michael_Schumacher",
                 "givenName": "Michael", "familyName": "Schumacher", "code": "MSC", "permanentNumber": "",
                 "nationality": "German"}] if path == "drivers" else []
        key = "DriverTable" if path == "drivers" else "ConstructorTable"
        return httpx.Response(200, json={"MRData": {"total": str(len(rows)), key: {path.capitalize()[:-1] + "s": rows}}})
    name = "_".join([path.replace("/", "_")] + [f"{k}_{params[k]}" for k in ("limit", "offset") if k in params]) + ".json"
    file = JOLPICA / name
    if file.exists():
        return httpx.Response(200, content=file.read_bytes(), headers={"content-type": "application/json"})
    return httpx.Response(404, json={"detail": "not recorded"})


def reply(request: httpx.Request) -> httpx.Response:
    host, p = request.url.host, request.url.params
    if host == "api.jolpi.ca":
        return jolpica(request)
    if host == "en.wikipedia.org":
        asked = (p.get("titles", "") + "|" + p.get("gsrsearch", "")).casefold()
        if p.get("prop", "").startswith("pageimages") and "|" in p.get("titles", ""):
            return httpx.Response(200, json={"query": {"pages": []}})
        title = next((t for t in PAGES if t.casefold() in asked), "")
        if not title:
            return httpx.Response(200, json={"query": {"pages": []}})
        qid, desc, shape, extract = PAGES[title]
        w, h = (600, 800) if shape == "tall" else (1200, 700)
        return httpx.Response(200, json={"query": {"pages": [{
            "title": title, "fullurl": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"), "description": desc,
            "extract": extract, "pageprops": {"wikibase_item": qid},
            "original": {"source": f"{ART}{shape}-{qid}.svg", "width": w, "height": h}}]}})
    if host == "www.wikidata.org":
        if p.get("action") == "wbgetclaims":
            return httpx.Response(200, json={"claims": {}})
        if p.get("props", "").startswith("claims"):
            if p.get("sites"):
                title = next((t for t in PAGES if t.casefold() == p.get("titles", "").casefold()), "")
                qid = PAGES[title][0] if title else ""
            else:
                qid = p.get("ids", "")
            return httpx.Response(200, json={"entities": {qid: {"id": qid, "claims": CLAIMS[qid],
                                                                "labels": {"en": {"value": next((t for t, v in PAGES.items() if v[0] == qid), "")}}}}
                                             if qid in CLAIMS else {}})
        return httpx.Response(200, json={"entities": {q: {"labels": {"en": {"value": n}}} for q, n in LABELS.items()}})
    if request.url.path == "/search":
        return httpx.Response(200, json={"results": [], "suggestions": []})
    return httpx.Response(404)


@asynccontextmanager
async def lifespan(application):
    async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(reply)) as client:
        application.state.searx = SearXNG(client)
        application.state.ext = client
        yield


async def fixture_lookup(client, candidate, *, fresh=False, _real=wiki.lookup):
    """The panel only takes https pictures, so the placeholder art is put back after the build."""
    out = await _real(client, candidate, fresh=fresh)
    page = next((v for t, v in PAGES.items() if out and v[0] == out.get("qid")), None)
    return {**out, "image": f"{ART}{page[2]}-{page[0]}.svg", "image_logo": False} if page else out


async def fixture_logo(client, url):
    """Team logos come from Commons; here a local stand-in, so the page makes no outside request."""
    return f"{ART}logo-{f1.article(url).replace(' ', '-')}.svg" if url else ""


if __name__ == "__main__":
    wiki.lookup = fixture_lookup
    f1.team_logo = fixture_logo
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
