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
from archer.sources import aimodels, crypto, dictionary, fx, modelprices, places, products, translate, weather, wiki
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
    if query.entity_hint:
        # "archer show": search for the TV series by name; the bare "Archer" is Archery.
        kind, words = query.entity_hint
        data = await wiki.lookup(ext, f"{query.entity_candidate} {words}")
        if data and data["kind"] == kind:
            return Hero("entity", data)
    data = await wiki.lookup(ext, query.entity_candidate)
    if data and query.entity_hint and data["kind"] != query.entity_hint[0]:
        return None  # the wrong kind of thing; the web results may still name the right article
    return Hero("entity", data) if data else None


async def _about(ext: httpx.AsyncClient, title: str) -> Hero | None:
    data = await wiki.lookup(ext, title)
    return Hero("entity", data) if data else None


def _site_name(host: str) -> str:
    """"apple" for apple.com / www.apple.co.in: the name a brand's own domain spells."""
    labels = host.removeprefix("www.").split(".")
    while len(labels) > 1 and labels[-1] in ("com", "org", "net", "co", "in", "uk", "io", "tv", "us", "de", "fr", "jp", "au", "ca"):
        labels.pop()
    return labels[-1] if labels else ""


async def _settle_meaning(ext: httpx.AsyncClient, query: Query, hero: Hero | None, results: list[Result],
                          about: str = "") -> Hero | None:
    """Pick the meaning people searching this usually want, and list the others.

    Wikipedia's exact title is often the dictionary sense ("Dune" the landform, "Apple" the fruit,
    "Archer" redirecting to Archery). The web results already rank meanings by what people want,
    so the panel follows them: a brand's own site at #1 means its company (Wikidata's official-
    website property), otherwise the first Wikipedia article about this name. The meanings left
    over become "Other meanings" chips; `about` (from a chip) pins one and skips the choice.
    """
    current = hero.data if hero and hero.kind == "entity" else None
    if hero and not current:
        return hero  # an AI model card or similar: not a Wikipedia meaning
    core, hint = query.entity_candidate, query.entity_hint
    ranked = []  # Wikipedia articles about this name, in the web's order
    for r in results[:10]:
        title = wiki.article_title(r.url)
        if title and wiki.matches(core, title) and title not in ranked:
            ranked.append(title)
    picks = []
    if not about:
        top = results[0] if results else None
        if top and not wiki.article_title(top.url) and _site_name(top.host) == re.sub(r"[^a-z0-9]", "", core.lower()):
            site = await _safe(wiki.title_for_site(ext, top.url, core), "wikidata site")
            if site and site is not _FAILED:
                picks.append(site)
        picks += ranked[:1]
    chosen, replaced = current, None
    for title in picks:
        if chosen and title.lower() == chosen["title"].lower():
            break
        data = await _safe(wiki.lookup(ext, title), "wikipedia")
        if data and data is not _FAILED and (not hint or data["kind"] == hint[0]) and data["title"] != (chosen or {}).get("title"):
            chosen, replaced = data, chosen
            break
    if not chosen:
        return None
    others, seen = [], {chosen["title"].lower()}
    if replaced:
        others.append({"title": replaced["title"], "desc": replaced.get("description", "")})
        seen.add(replaced["title"].lower())
    for title in ranked:
        if title.lower() not in seen:
            seen.add(title.lower())
            others.append({"title": title, "desc": ""})
    # Cached panels are shared between searches: copy before adding this search's chips.
    return Hero("entity", {**chosen, "others": others[:4], "corrected": chosen["corrected"] and chosen is current})


async def _ai_model_or_entity(ext: httpx.AsyncClient, query: Query) -> Hero | None:
    """AI models match a local Epoch AI catalog first; anything else falls through to Wikipedia."""
    if (cat := await aimodels.ensure(ext)) and (model := aimodels.find(cat, query.text)):
        prices, rate = await asyncio.gather(modelprices.ensure(ext), fx.usd_rate(ext, settings.home_currency))
        return Hero("aimodel", aimodels.panel(cat, model, prices=prices, rate=rate))
    return await _entity(ext, query) if query.entity_candidate else None


def _product_hero(query: Query, hero: Hero | None, response: SearchResponse, related: list[str]) -> Hero | None:
    """A product panel when Wikidata calls the entity a product, or when stores list the model searched for."""
    entity = hero.data if hero and hero.kind == "entity" else None
    if hero and not entity:
        return None  # an AI model card or another dedicated hero
    name = query.entity_candidate or ""
    if entity and entity["kind"] == "product":
        return Hero("product", products.panel(name or entity["title"], response.results, entity=entity,
                                              intent=query.product_intent, related=related, home=settings.home_currency,
                                              country=settings.home_country))
    if entity and entity["kind"] != "thing" and products.Matcher(name)(entity["title"]):
        return None  # a film, person or company of this name, not something to buy
    if name and products.detect(name, response.results):
        return Hero("product", products.panel(name, response.results, intent=query.product_intent, related=related,
                                              home=settings.home_currency, country=settings.home_country))
    return None


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


_SPOTIFY_PAGE = re.compile(r"^https://open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album)/([A-Za-z0-9]{22})(?:[/?#]|$)")


def spotify_from_results(results: list[Result], title: str, artist: str = "") -> dict | None:
    """A Spotify track or album page among search results that names this title (and artist, when known)."""
    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", text.casefold())
    want, by = norm(title), norm(re.split(r",| and | & | feat", artist)[0]) if artist else ""
    for result in results[:15]:
        match = _SPOTIFY_PAGE.match(result.url)
        text = norm(f"{result.title} {result.content}")
        if match and want and want in text and (not by or by in text):
            kind, item = match.groups()
            return {"embed": f"https://open.spotify.com/embed/{kind}/{item}", "url": f"https://open.spotify.com/{kind}/{item}"}
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

async def build(searx: SearXNG, ext: httpx.AsyncClient, q: str, tab: str, page: int, about: str = "") -> View:
    query = classify(q, settings.home_currency)
    view = View(q=query.text, tab=tab, page=page, query=query)
    first_page_all = tab == "all" and page == 1

    hero_task = None
    if first_page_all:
        if query.kind in LOCAL_KINDS:
            view.hero = Hero(query.kind, query.data)
        elif builder := ASYNC_HEROES.get(query.kind):
            hero_task = _safe(builder(ext, query), query.kind)
        elif query.kind in ("general", "question") and about:
            hero_task = _safe(_about(ext, about), "wikipedia")
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
    if first_page_all and query.kind in ("general", "question") and (query.entity_candidate or about)             and (view.hero is None or view.hero.kind == "entity"):
        if not query.entity_candidate:  # a chip on a longer query: still offer the way back
            query.entity_candidate = about
        view.hero = await _settle_meaning(ext, query, view.hero, response.results, about)
    if first_page_all and query.kind == "general" and query.entity_candidate:
        view.hero = _product_hero(query, view.hero, response, view.related) or view.hero
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
        product = view.hero.data if view.hero and view.hero.kind == "product" else None
        view.lazy = {
            "news": bool(query.wants_news or kind in NEWS_KINDS or (view.hero and view.hero.kind in ("aimodel", "product"))),
            "videos": query.wants_videos,
            "images": query.wants_images or kind in IMAGE_KINDS,
            "works": {"qid": entity["qid"], "role": entity["works_role"]}
            if entity and entity.get("works_role") in ("author", "performer") else None,
            "filmography": {"qid": entity["qid"], "tmdb": entity.get("tmdb_person", "")}
            if entity and entity.get("works_role") == "cast" else None,
            "cast": {"qid": entity["qid"], "media": entity["watch"]["media"], "tmdb": entity["watch"]["tmdb"],
                     "names": entity.get("cast", [])} if kind in ("film", "tv") and entity.get("watch") else None,
            "weather_at": entity["coords"] if kind in WEATHER_KINDS and entity.get("coords") else None,
            # More prices and reviews (two cached searches) and a picture gallery, after the page is up.
            "product": {"name": product["name"], "q": query.text} if product else None,
        }
    return view
