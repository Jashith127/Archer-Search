"""Builds the results-page view model: SearXNG results plus one hero module per query type."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx

from archer.config import settings
from archer.imagerank import rank_images
from archer.query import PLACE_TYPES, Query, classify
from archer.searxng import Result, SearchResponse, SearXNG, suggestion_terms
from archer.sources import aimodels, crypto, dictionary, places, translate, weather, wiki
from archer.utilities import parse_currency_answer

log = logging.getLogger("archer")

TABS = {"all": "general", "images": "images", "news": "news", "videos": "videos"}
DISCUSSION_HOSTS = re.compile(
    r"(^|\.)(reddit\.com|stackoverflow\.com|stackexchange\.com|superuser\.com|serverfault\.com|"
    r"askubuntu\.com|quora\.com|news\.ycombinator\.com|lemmy\.\w+|discourse\.\w+)$"
)
NEWS_KINDS = {"person", "org", "film", "tv", "music", "place", "country", "software", "landmark"}
IMAGE_KINDS = {"person", "place", "species", "thing", "music", "landmark", "country", "character"}
WEATHER_KINDS = {"place", "landmark"}
# Heroes computed entirely from the query text (no network).
LOCAL_KINDS = {"calc", "unit", "color", "timestamp", "random", "timer", "metronome", "finance",
               "solve", "graph", "track"}


@dataclass
class Hero:
    kind: str  # template name under templates/heroes/
    data: dict


@dataclass
class View:
    q: str
    tab: str
    page: int
    query: Query
    hero: Hero | None = None
    results: list[Result] = field(default_factory=list)
    discussions: list[Result] = field(default_factory=list)
    lazy: dict = field(default_factory=dict)
    response: SearchResponse | None = None
    related: list[str] = field(default_factory=list)
    hero_failed: str | None = None  # source name when the hero's data source failed


_FAILED = object()


async def _safe(coro, what: str):
    """Await a hero builder; on upstream failure log it and return _FAILED (not None = "no card")."""
    try:
        return await coro
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        # HTTP exception strings include query parameters; keep query text out of logs.
        log.warning("%s failed: %s", what, type(exc).__name__)
        return _FAILED


# ------------------------------------------------------------ hero builders

async def _place_hero(ext: httpx.AsyncClient, query: Query) -> Hero | None:
    loc = await weather.geocode(ext, query.data["place"] or settings.home_location)
    if not loc:
        return None
    label = weather.place_label(loc)
    if query.kind == "weather":
        return Hero("weather", {**await weather.forecast(ext, loc["latitude"], loc["longitude"]), "place": label})
    if query.kind == "sun":
        data = await weather.sun(ext, loc["latitude"], loc["longitude"])
        return Hero("sun", {**data, "place": label, "event": query.data["event"]})
    t = weather.local_time(loc.get("timezone", ""))
    return Hero("time", {**t, "place": label}) if t else None


async def _define(ext: httpx.AsyncClient, query: Query) -> Hero | None:
    data = await dictionary.define(ext, query.data["word"])
    return Hero("define", data) if data else None


async def _translate(ext: httpx.AsyncClient, query: Query) -> Hero | None:
    data = await translate.translate(ext, query.data["text"], query.data["lang"])
    return Hero("translate", {**data, "target_name": query.data["lang_name"]}) if data else None


async def _crypto(ext: httpx.AsyncClient, query: Query) -> Hero | None:
    data = await crypto.price(ext, query.data["coin"], query.data["fiat"])
    return Hero("crypto", data) if data else None


async def home_coords(ext: httpx.AsyncClient) -> tuple[float, float] | None:
    loc = await weather.geocode(ext, settings.home_location)
    return (loc["latitude"], loc["longitude"]) if loc else None


async def places_payload(ext: httpx.AsyncClient, cat: str, area: str, near_me: bool) -> dict:
    """Data for the lazily loaded places module (see /api/places). Raises on upstream failure."""
    key, value, label = PLACE_TYPES[cat]
    loc = await places.area(ext, area or settings.home_location, await home_coords(ext))
    if not loc:
        return {"error": f"OpenStreetMap doesn't know a place called “{area}”."}
    radius = 1500 if near_me or loc["small"] else 3500
    found, source = await places.nearby(ext, key, value, loc["lat"], loc["lon"], radius)
    lat, lon = loc["lat"], loc["lon"]
    return {"label": label, "area": loc["label"], "radius_km": radius / 1000, "places": found, "source": source,
            "map": f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=15/{lat:.5f}/{lon:.5f}"}


def _places_hero(query: Query, response: SearchResponse) -> Hero:
    """Header now, places later: Overpass can take 10–15 s, which must not hold the page."""
    d = query.data
    typed = d["area"]
    fixed = places.correct_area(typed, [f"{r.title} {r.content}" for r in response.results[:10]]) if typed else None
    return Hero("places", {**d, "area": fixed or typed, "typed_area": typed if fixed else ""})


async def _entity(ext: httpx.AsyncClient, query: Query) -> Hero | None:
    data = await wiki.lookup(ext, query.entity_candidate)
    return Hero("entity", data) if data else None


async def _ai_model_or_entity(ext: httpx.AsyncClient, query: Query) -> Hero | None:
    """AI models match a local Epoch AI catalog first; anything else falls through to Wikipedia."""
    if (cat := await aimodels.ensure(ext)) and (model := aimodels.find(cat, query.text)):
        return Hero("aimodel", aimodels.panel(cat, model))
    return await _entity(ext, query) if query.entity_candidate else None


ASYNC_HEROES = {"weather": _place_hero, "time": _place_hero, "sun": _place_hero, "define": _define,
                "translate": _translate, "crypto": _crypto}
# Named in the "couldn't load" notice when a hero's source fails.
SOURCES = {"weather": "Open-Meteo", "time": "Open-Meteo", "sun": "Open-Meteo", "define": "Wiktionary",
           "translate": "MyMemory", "crypto": "CoinGecko"}


def _quick_answer(response: SearchResponse) -> Hero | None:
    """DuckDuckGo instant answers that SearXNG passes through, shown as a featured-snippet card."""
    for answer in response.answers:
        text = (answer.get("answer") or "").strip()
        url = answer.get("url") or ""
        if answer.get("engine") == "duckduckgo" and len(text) > 40 and url.startswith("https://"):
            host = url.split("/")[2].removeprefix("www.")
            return Hero("answer", {"text": text, "url": url, "host": host})
    return None


_LYRICS_RESULT = re.compile(r"^(.+?)\s+[–—-]\s+(.+?)\s+Lyrics(?:\s*[|–-].*)?$", re.I)


def _song_from_results(query: str, results: list[Result]) -> Hero | None:
    """Rescue songs without a Wikipedia article using a matching lyrics result."""
    wanted = re.sub(r"[^a-z0-9]+", "", query.casefold())
    for result in results[:5]:
        if result.host not in ("genius.com", "azlyrics.com"):
            continue
        match = _LYRICS_RESULT.match(result.title)
        if not match:
            continue
        artist, title = match.groups()
        title = title.strip()
        found = re.sub(r"[^a-z0-9]+", "", title.casefold())
        if SequenceMatcher(None, wanted, found).ratio() < .78:
            continue
        video = next((r for r in results[:8] if r.host in ("youtube.com", "youtu.be")
                      and SequenceMatcher(None, found, re.sub(r"[^a-z0-9]+", "", r.title.casefold())).ratio() > .35), None)
        video_id = ""
        if video:
            parsed = urlsplit(video.url)
            video_id = (parse_qs(parsed.query).get("v", [""])[0] if parsed.hostname in ("youtube.com", "www.youtube.com")
                        else parsed.path.strip("/"))
            if not re.fullmatch(r"[\w-]{11}", video_id):
                video_id = ""
        spotify_id = next((urlsplit(r.url).path.split("/track/")[1].split("/")[0]
                           for r in results[:12] if r.host == "open.spotify.com"
                           and "/track/" in urlsplit(r.url).path
                           and found in re.sub(r"[^a-z0-9]+", "", r.title.casefold())), "")
        if not re.fullmatch(r"[A-Za-z0-9]{22}", spotify_id):
            spotify_id = ""
        return Hero("song", {"title": title, "artist": artist.strip(), "lyrics_url": result.url,
                             "spotify_search": f"https://open.spotify.com/search/{quote_plus(title + ' ' + artist)}",
                             "spotify_id": spotify_id, "video_id": video_id,
                             "video_url": video.url if video_id else ""})
    return None


# --------------------------------------------------------------------- build

async def build(searx: SearXNG, ext: httpx.AsyncClient, q: str, tab: str, page: int) -> View:
    query = classify(q, settings.home_currency)
    view = View(q=query.text, tab=tab, page=page, query=query)
    first_page_all = tab == "all" and page == 1

    hero_task = None
    if first_page_all:
        if query.kind in LOCAL_KINDS:
            view.hero = Hero(query.kind, query.data)
        elif builder := ASYNC_HEROES.get(query.kind):
            hero_task = _safe(builder(ext, query), query.kind)
        elif query.kind in ("general", "question"):
            hero_task = _safe(_ai_model_or_entity(ext, query), "wikipedia")

    search_task = searx.search(query.text, TABS[tab], page)
    if hero_task:
        response, hero = await asyncio.gather(search_task, hero_task)
        if hero is _FAILED:
            # Say so instead of silently dropping the card (entity panels are optional, so stay quiet).
            if query.kind in SOURCES:
                view.hero_failed = SOURCES[query.kind]
        else:
            view.hero = hero
    else:
        response = await search_task
    view.response = response
    view.related = suggestion_terms(response.suggestions, view.q)
    if first_page_all and not view.hero and query.kind in ("general", "question"):
        view.hero = _song_from_results(query.text, response.results)
    if first_page_all and query.kind == "places":
        view.hero = _places_hero(query, response)

    if first_page_all and not view.hero:
        for answer in response.answers:
            if answer.get("engine") == "currency" and (cur := parse_currency_answer(answer.get("answer", ""))):
                view.hero = Hero("currency", cur)
                break
    if first_page_all and not view.hero and query.kind in ("question", "general"):
        view.hero = _quick_answer(response)

    results = response.results
    if tab == "all":
        view.discussions = [r for r in results if DISCUSSION_HOSTS.search(r.host)][:4]
        if len(view.discussions) >= 2:
            ids = {id(r) for r in view.discussions}
            results = [r for r in results if id(r) not in ids]
        else:
            view.discussions = []
    if tab == "images":
        results = rank_images(query.text, results)
    view.results = results

    if first_page_all:
        entity = view.hero.data if view.hero and view.hero.kind == "entity" else None
        kind = entity["kind"] if entity else None
        view.lazy = {
            "news": bool(query.wants_news or kind in NEWS_KINDS or (view.hero and view.hero.kind == "aimodel")),
            "videos": query.wants_videos,
            "images": query.wants_images or kind in IMAGE_KINDS,
            "works": {"qid": entity["qid"], "role": entity["works_role"]} if entity and entity.get("works_role") else None,
            "weather_at": entity["coords"] if kind in WEATHER_KINDS and entity.get("coords") else None,
        }
    return view
