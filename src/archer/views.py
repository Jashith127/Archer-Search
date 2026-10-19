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
from archer.sources import aimodels, crypto, dictionary, f1, fx, modelprices, places, products, translate, weather, wiki
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
               "solve", "graph", "track", "vct"}


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
    remembered: str = ""  # a meaning chosen on an earlier search of this name, applied again
    # Set when the results went out before the knowledge panel (build(defer=True)): awaiting it fills
    # in the hero and the final `lazy`; `early_lazy` is what the first part of the page already drew.
    pending: object = None
    early_lazy: dict = field(default_factory=dict)


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


def _hint_ok(kind: str, hint: tuple[str, str] | None) -> bool:
    """Whether a panel of `kind` is what "… show" / "… valorant" asked for. "esports" (and "esports:<game>")
    is a team or a player alike."""
    if not hint:
        return True
    wanted = hint[0].split(":", 1)[0]
    return kind in ("esteam", "esplayer") if wanted == "esports" else kind == wanted


def _for_game(hero: Hero | None, hint: tuple[str, str] | None) -> Hero | None:
    """"t1 valorant": the organisation's panel, drawn for the game the search named."""
    if not hero or hero.kind != "entity" or not hint or ":" not in hint[0]:
        return hero
    data = hero.data
    if data.get("kind") not in ("esteam", "esplayer") or not data.get("esports"):
        return hero
    from archer.sources import esports
    game = hint[0].split(":", 1)[1]
    info = esports.GAME_INFO.get(game)
    if not info or data["esports"]["game"] == game:
        return hero
    games = [info] + [g for g in data["esports"]["games"] if g["key"] != game]
    return Hero("entity", {**data, "esports": {**data["esports"], "game": game, "games": games, "color": info["color"]}})


async def _entity(ext: httpx.AsyncClient, query: Query) -> Hero | None:
    if query.entity_hint:
        # "archer show": search for the TV series by name; the bare "Archer" is Archery.
        kind, words = query.entity_hint
        data = await wiki.lookup(ext, f"{query.entity_candidate} {words}")
        if data and _hint_ok(data["kind"], query.entity_hint):
            return Hero("entity", data)
    data = await wiki.lookup(ext, query.entity_candidate)
    if data and not _hint_ok(data["kind"], query.entity_hint):
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


def _ranked_titles(core: str, results: list[Result]) -> list[str]:
    ranked = []
    for r in results[:10]:
        title = wiki.article_title(r.url)
        if title and wiki.matches(core, title) and title not in ranked:
            ranked.append(title)
    return ranked


def _brand_site_first(core: str, results: list[Result]) -> bool:
    top = results[0] if results else None
    return bool(top and not wiki.article_title(top.url) and _site_name(top.host) == re.sub(r"[^a-z0-9]", "", core.lower()))


_prefetches: set[asyncio.Task] = set()


def _prefetch_meaning(ext: httpx.AsyncClient, query: Query, results: list[Result]) -> None:
    """Start the lookup _settle_meaning will most likely make (the web's first Wikipedia article on
    this name) while the panel's own lookup is still running; wiki.lookup shares it when asked."""
    core = query.entity_candidate
    if not core or _brand_site_first(core, results):
        return  # a brand's own site decides first; its company may make this lookup unnecessary
    ranked = _ranked_titles(core, results)
    if ranked:
        task = asyncio.ensure_future(_safe(wiki.lookup(ext, ranked[0]), "wikipedia"))
        _prefetches.add(task)
        task.add_done_callback(_prefetches.discard)


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
    ranked = _ranked_titles(core, results)  # Wikipedia articles about this name, in the web's order
    picks = []
    if not about:
        # A current F1 team or driver named in the search's own words ("mercedes petronas amg f1 team")
        # comes first; Wikipedia's title rarely contains every one of those words.
        racing = await _safe(f1.title_for_query(ext, query.text), "jolpica")
        if racing and racing is not _FAILED:
            picks.append(racing)
        if _brand_site_first(core, results):
            top = results[0]
            site = await _safe(wiki.title_for_site(ext, top.url, core), "wikidata site")
            if site and site is not _FAILED:
                picks.append(site)
        picks += ranked[:1]
    chosen, replaced = current, None
    for title in picks:
        if chosen and title.lower() == chosen["title"].lower():
            break
        data = await _safe(wiki.lookup(ext, title), "wikipedia")
        if data and data is not _FAILED and _hint_ok(data["kind"], hint) and data["title"] != (chosen or {}).get("title"):
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

# How long finished results wait for a knowledge panel that is still loading before going out without it.
HERO_GRACE = 0.3


async def build(searx: SearXNG, ext: httpx.AsyncClient, q: str, tab: str, page: int, about: str = "",
                defer: bool = False) -> View:
    """The page's view. With `defer`, results don't wait for a Wikipedia panel that is still loading:
    the view comes back without it and `view.pending` finishes it (panel, its chips and lazy rows)."""
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
        hero_future = asyncio.ensure_future(hero_task)
        try:
            response = await search_task
        except BaseException:
            hero_future.cancel()
            raise
        view.response = response
        view.related = suggestion_terms(response.suggestions, view.q)
        if not hero_future.done() and not about and query.kind in ("general", "question"):
            _prefetch_meaning(ext, query, response.results)
        if defer and query.kind in ("general", "question") and not hero_future.done():
            await asyncio.wait([hero_future], timeout=HERO_GRACE)
            if not hero_future.done():
                _results(view, query, response)
                view.lazy = _lazy(view, query)
                view.early_lazy = dict(view.lazy)

                async def finish() -> None:
                    _take_hero(view, query, await hero_future)
                    await _hero_phase(view, ext, query, response, about)
                    view.lazy = _lazy(view, query)
                view.pending = finish
                return view
        _take_hero(view, query, await hero_future)
    else:
        response = await search_task
        view.response = response
        view.related = suggestion_terms(response.suggestions, view.q)
    await _hero_phase(view, ext, query, response, about)
    _results(view, query, response)
    view.lazy = _lazy(view, query)
    return view


def _take_hero(view: View, query: Query, hero) -> None:
    if hero is _FAILED:
        # Say so instead of silently dropping the card (entity panels are optional, so stay quiet).
        if query.kind in SOURCES:
            view.hero_failed = SOURCES[query.kind]
    else:
        view.hero = hero


async def _hero_phase(view: View, ext: httpx.AsyncClient, query: Query, response: SearchResponse, about: str) -> None:
    """Everything that decides the page's one hero once the search has answered."""
    if not (view.tab == "all" and view.page == 1):
        return
    if query.kind in ("general", "question") and (query.entity_candidate or about) \
            and (view.hero is None or view.hero.kind == "entity"):
        if not query.entity_candidate:  # a chip on a longer query: still offer the way back
            query.entity_candidate = about
        view.hero = _for_game(await _settle_meaning(ext, query, view.hero, response.results, about), query.entity_hint)
    if query.kind == "general" and query.entity_candidate:
        view.hero = _product_hero(query, view.hero, response, view.related) or view.hero
    if not view.hero and query.kind in ("general", "question"):
        view.hero = _song_from_results(query.text, response.results)
    if query.kind == "places":
        view.hero = _places_hero(query, response)
    if not view.hero:
        for answer in response.answers:
            if answer.get("engine") == "currency" and (cur := parse_currency_answer(answer.get("answer", ""))):
                view.hero = Hero("currency", cur)
                break
    if not view.hero and query.kind in ("question", "general"):
        view.hero = _quick_answer(response)


def _results(view: View, query: Query, response: SearchResponse) -> None:
    results = response.results
    if view.tab == "all":
        view.discussions = [r for r in results if DISCUSSION_HOSTS.search(r.host)][:4]
        if len(view.discussions) >= 2:
            ids = {id(r) for r in view.discussions}
            results = [r for r in results if id(r) not in ids]
        else:
            view.discussions = []
    if view.tab == "images":
        results = rank_images(query.text, results)
    view.results = results


def _lazy(view: View, query: Query) -> dict:
    if not (view.tab == "all" and view.page == 1):
        return {}
    entity = view.hero.data if view.hero and view.hero.kind == "entity" else None
    kind = entity["kind"] if entity else None
    product = view.hero.data if view.hero and view.hero.kind == "product" else None
    return {
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
