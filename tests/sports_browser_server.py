"""Isolated sports and esports fixture: canned Wikipedia/Wikidata and recorded ESPN, VLR.gg and Liquipedia
replies, zero external calls.

Run `uv run python tests/sports_browser_server.py`, then open http://127.0.0.1:4755/search?q= with
arsenal (football club: table, form, next match, squad on a pitch), boston celtics (NBA team),
premier league (league table and this round), sentinels (Valorant team), tenz (Valorant player),
t1 esports (League of Legends team, Liquipedia roster), faker (League of Legends player, world titles),
gen.g (an organisation naming no game: the compact panel), tenz valorant or t1 valorant (a game named
after the handle), vct 2026 (the season, opened on Champions 2026), vct 2024 (Champions 2024 with the trophy) or vct 2025
(no hub recorded: the empty state).

Replies are real responses recorded in tests/fixtures/espn, tests/fixtures/vlr and tests/fixtures/liquipedia
(October 2026). Pictures are generated placeholders served by this script; crests, logos and agent icons in
the recorded replies point at their real hosts.
"""
import hashlib
import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import Response

from archer.app import app
from archer.searxng import SearXNG
from archer.sources import esports, wiki

PORT = 4755
ART = f"http://localhost:{PORT}/fixture-art/"
FIXTURES = Path(__file__).with_name("fixtures")


def snak(v):
    if isinstance(v, str) and v.startswith("Q"):
        return {"snaktype": "value", "datavalue": {"type": "wikibase-entityid", "value": {"id": v}}}
    return {"snaktype": "value", "datavalue": {"type": "string", "value": v}}


def claim(v):
    return {"mainsnak": snak(v), "rank": "normal", "qualifiers": {}}


def when(text):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"type": "time", "value": {"time": text, "precision": 11}}},
            "rank": "normal", "qualifiers": {}}


CLAIMS = {
    "Q9617": {"P31": [claim("Q476028")], "P641": [claim("Q2736")], "P118": [claim("Q9448")], "P17": [claim("Q145")],
              "P571": [when("+1886-10-01T00:00:00Z")], "P115": [claim("Q180125")], "P286": [claim("Q1046914")]},
    "Q131371": {"P31": [claim("Q13393265")], "P641": [claim("Q5372")], "P118": [claim("Q155223")], "P17": [claim("Q30")],
                "P571": [when("+1946-06-06T00:00:00Z")], "P115": [claim("Q1025396")]},
    "Q9448": {"P31": [claim("Q15991303")], "P641": [claim("Q2736")], "P17": [claim("Q145")],
              "P571": [when("+1992-02-20T00:00:00Z")], "P1346": [claim("Q50602")]},
    "Q24884615": {"P31": [claim("Q61974339")], "P641": [claim("Q300920")], "P17": [claim("Q30")],
                  "P571": [when("+2016-01-01T00:00:00Z")], "P159": [claim("Q65")]},
    "Q113450352": {"P31": [claim("Q5")], "P106": [claim("Q4379701")], "P27": [claim("Q16")],
                   "P569": [when("+2001-05-05T00:00:00Z")], "P54": [claim("Q24884615")]},
    "Q483981": {"P31": [claim("Q61974339")], "P641": [claim("Q300920")], "P17": [claim("Q884")],
                "P571": [when("+2004-01-01T00:00:00Z")], "P159": [claim("Q8684")]},
    # An organisation whose article names no game and that VLR.gg doesn't know here: the sparse, compact panel.
    "Q30891496": {"P31": [claim("Q61974339")], "P641": [claim("Q300920")], "P17": [claim("Q884")]},
    "Q15618298": {"P31": [claim("Q5")], "P106": [claim("Q4379701")], "P27": [claim("Q884")],
                  "P569": [when("+1996-05-07T00:00:00Z")], "P54": [claim("Q483981")]},
}
LABELS = {
    "Q476028": "association football club", "Q2736": "association football", "Q9448": "Premier League", "Q145": "United Kingdom",
    "Q180125": "Emirates Stadium", "Q1046914": "Mikel Arteta", "Q13393265": "basketball team", "Q5372": "basketball",
    "Q155223": "National Basketball Association", "Q30": "United States", "Q1025396": "TD Garden",
    "Q15991303": "association football league", "Q50602": "Manchester City F.C.", "Q61974339": "esports team",
    "Q300920": "esports", "Q65": "Los Angeles", "Q5": "human", "Q4379701": "professional gamer", "Q16": "Canada",
    "Q884": "South Korea", "Q8684": "Seoul",
}
PAGES = {
    "Arsenal F.C.": ("Q9617", "Association football club in London, England", "logo",
                     "Arsenal Football Club is a professional football club based in Islington, London, England. "
                     "They compete in the Premier League, the top tier of English football."),
    "Boston Celtics": ("Q131371", "American professional basketball team", "logo",
                       "The Boston Celtics are an American professional basketball team based in Boston. They compete in the NBA."),
    "Premier League": ("Q9448", "Association football league in England", "logo",
                       "The Premier League is a professional association football league in England and the highest level of the English football league system."),
    "Sentinels (esports)": ("Q24884615", "American esports organization", "logo",
                            "Sentinels is an American professional esports organization based in Los Angeles. "
                            "It fields teams in Valorant, Fortnite and Apex Legends."),
    "TenZ": ("Q113450352", "Canadian Valorant player and streamer", "tall",
             "Tyson Ngo, better known as TenZ, is a Canadian streamer and former professional Valorant player."),
    "T1 (esports)": ("Q483981", "South Korean esports organization", "logo",
                     "T1 is a South Korean esports organization. Its League of Legends team has won six World Championships."),
    "Gen.G": ("Q30891496", "Esports organization", "logo",
              "Gen.G is a professional esports organization with headquarters in Santa Monica, Seoul and Shanghai."),
    "Faker (gamer)": ("Q15618298", "South Korean League of Legends player", "tall",
                      "Lee Sang-hyeok, better known as Faker, is a South Korean professional League of Legends player for T1."),
}
ALIASES = {"arsenal": "Arsenal F.C.", "sentinels": "Sentinels (esports)", "t1": "T1 (esports)", "faker": "Faker (gamer)",
           "t1 esports": "T1 (esports)"}


@app.get("/fixture-art/{name}.svg")
def fixture_art(name: str) -> Response:
    hue = int(hashlib.md5(name.encode()).hexdigest()[:2], 16) * 360 // 256
    if name.startswith("logo"):
        return Response(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 70"><path d="M4 4h52v30c0 18-14 28-26 32C18 62 4 52 4 34z" '
                        f'fill="hsl({hue},60%,45%)"/><circle cx="30" cy="30" r="12" fill="#fff" opacity=".85"/></svg>', media_type="image/svg+xml")
    return Response(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 800"><rect width="600" height="800" fill="hsl({hue},30%,22%)"/>'
                    f'<circle cx="300" cy="300" r="120" fill="rgba(255,255,255,.18)"/><rect x="150" y="470" width="300" height="400" rx="120" '
                    f'fill="rgba(255,255,255,.12)"/></svg>', media_type="image/svg+xml")


def espn(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/sports/", 1)[-1].replace("/", "_")
    if request.url.params.get("fixture"):
        path += "_fixture"
    file = FIXTURES / "espn" / f"{path}.json"
    return httpx.Response(200, content=file.read_bytes()) if file.exists() else httpx.Response(404)


VLR = {"/team/2/": "team_2.html", "/team/stats/2/": "team_stats_2.html", "/player/9/": "player_9.html"}


def vlr(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/search/":
        p = request.url.params
        file = FIXTURES / "vlr" / f"search_{p.get('q', '').lower()}_{p.get('type', '')}.html"
    elif m := re.fullmatch(r"/(vct|gc)-(\d{4})", request.url.path):       # season hubs
        file = FIXTURES / "vlr" / f"{m.group(1)}_{m.group(2)}.html"
    elif m := re.fullmatch(r"/event/(\d+)/", request.url.path):
        file = FIXTURES / "vlr" / f"event_{m.group(1)}.html"
    else:
        file = FIXTURES / "vlr" / VLR.get(request.url.path, "missing")
    return httpx.Response(200, content=file.read_bytes()) if file.exists() else httpx.Response(404)


def liquipedia(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    wiki_name = request.url.path.strip("/").split("/")[0]
    name = f"{wiki_name}_{p.get('titles') or p.get('title', '')}" + ("_roster" if p.get("action") == "parse" else "")
    file = FIXTURES / "liquipedia" / f"{name}.json"
    if file.exists():
        return httpx.Response(200, content=file.read_bytes())
    return httpx.Response(200, json={"query": {"pages": {"-1": {"missing": ""}}}})


def reply(request: httpx.Request) -> httpx.Response:
    host, p = request.url.host, request.url.params
    if host == "site.api.espn.com":
        return espn(request)
    if host == "www.vlr.gg":
        return vlr(request)
    if host == "liquipedia.net":
        return liquipedia(request)
    if host == "en.wikipedia.org":
        asked = (p.get("titles", "") + "|" + p.get("gsrsearch", "")).casefold()
        if p.get("prop", "").startswith("pageimages") and "|" in p.get("titles", ""):
            return httpx.Response(200, json={"query": {"pages": []}})
        title = next((t for t in PAGES if t.casefold() == asked.strip("|")), "") or \
            next((ALIASES[a] for a in sorted(ALIASES, key=len, reverse=True) if a == asked.split("|")[0].strip()), "") or \
            next((t for t in PAGES if t.casefold() in asked), "")
        if not title:
            return httpx.Response(200, json={"query": {"pages": []}})
        qid, desc, shape, extract = PAGES[title]
        return httpx.Response(200, json={"query": {"pages": [{
            "title": title, "fullurl": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"), "description": desc,
            "extract": extract, "pageprops": {"wikibase_item": qid},
            "original": {"source": f"{ART}{shape}-{qid}.svg", "width": 600, "height": 800 if shape == "tall" else 600}}]}})
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
    return {**out, "image": f"{ART}{page[2]}-{page[0]}.svg", "image_logo": page[2] == "logo"} if page else out


if __name__ == "__main__":
    wiki.lookup = fixture_lookup
    esports._last["api"] = esports._last["parse"] = -1e9
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
