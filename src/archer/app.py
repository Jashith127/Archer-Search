from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote_plus
import logging
import re
import threading
import zlib

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from archer import __version__, views, websearch
from archer.fetcher import lead_image
from archer.imagerank import rank_images, similar_query
from archer.config import settings
from archer.query import PLACE_TYPES, classify
from archer.searxng import SearXNG, SUGGEST_MIN, SUGGEST_MAX_LENGTH, active_limits
from archer.sources import USER_AGENT, TTLCache, aimodels, credits, lyrics, products, watch, weather, wiki
from archer.ai_routes import router as ai_router, service as ai_service, set_cookie as ai_cookie
from archer.ai_provider import readiness
from archer.ai_service import AIService
from archer.settings_routes import page_context as settings_context, router as settings_router
from archer.config import DATA_DIR
from archer.embed import embeddings
from archer.history import History
from archer.history_routes import history as history_store, personalizer, router as history_router
from archer.personalize import Personal, Personalizer, site_pref

log = logging.getLogger("archer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    searx_http = httpx.AsyncClient(base_url=settings.searxng_url, timeout=settings.suggest_timeout,
                                   headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    ext = httpx.AsyncClient(timeout=5, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    async with searx_http, ext:
        app.state.searxng = searx_http
        app.state.searx = SearXNG(searx_http)
        app.state.ext = ext
        app.state.ai = AIService(settings, DATA_DIR)
        app.state.history = History()
        app.state.personal = Personalizer(app.state.history)
        try:
            app.state.history.prune(settings.history_days)
            # Vectors from a model you switched away from are recomputed if you switch back.
            app.state.history.drop_orphan_vectors(keep_models={"lexical", embeddings.configured().name})
        except Exception as exc:  # a locked or damaged file must not stop the search engine
            log.warning("history prune failed: %s", type(exc).__name__)
        if settings.personalize:
            threading.Thread(target=embeddings.warm, daemon=True).start()
        yield
        await app.state.ai.close()
        app.state.history.close()


app = FastAPI(title="Archer", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.include_router(ai_router)
app.include_router(settings_router)
app.include_router(history_router)

_PKG = files("archer")
_STATIC = _PKG / "static"
_SUGGEST_MIME = "application/x-suggestions+json"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
templates = Jinja2Templates(directory=str(_PKG / "templates"))

# Frames: only the three music players, and only loaded after a click or tab switch.
_CSP = ("default-src 'self'; img-src * data:; media-src *; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; "
        "frame-src https://www.youtube-nocookie.com https://open.spotify.com https://embed.music.apple.com; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") and response.status_code == 200:
        # CSS and JS carry a content hash (`asset()`), so a cached copy can never go stale: the
        # browser keeps it without re-checking on every page. Fonts and icons are re-checked daily.
        versioned = "v" in request.query_params
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "public, max-age=86400"
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ------------------------------------------------------------ template helpers

def _ago(value: str | float) -> str:
    if not value:
        return ""
    try:
        when = (datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float))
                else datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError, OSError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - when).total_seconds()
    if secs < 0:
        return when.strftime("%d %b %Y")
    for size, unit in ((86400 * 30, None), (86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= size:
            if unit is None:
                return when.strftime("%d %b %Y").lstrip("0")
            n = int(secs // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "nic", "gen", "ne", "or"}


def _site_name(host: str) -> str:
    """en.wikipedia.org -> Wikipedia, bbc.co.uk -> Bbc: the registrable name, Brave-style."""
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL:
        name = parts[-3]
    else:
        name = parts[-2] if len(parts) >= 2 else host
    return name[:1].upper() + name[1:]


def _hue(text: str) -> int:
    return zlib.crc32(text.encode()) % 360


# Currency code -> flag country (ISO 3166). EUR uses the EU flag.
_CURRENCY_COUNTRY = {
    "USD": "us", "INR": "in", "EUR": "eu", "GBP": "gb", "JPY": "jp", "AUD": "au", "CAD": "ca", "SGD": "sg",
    "AED": "ae", "CHF": "ch", "CNY": "cn", "HKD": "hk", "NZD": "nz", "SEK": "se", "NOK": "no", "DKK": "dk",
    "KRW": "kr", "RUB": "ru", "BRL": "br", "ZAR": "za", "MXN": "mx", "THB": "th", "MYR": "my", "IDR": "id",
    "PHP": "ph", "SAR": "sa", "PKR": "pk", "BDT": "bd", "LKR": "lk", "NPR": "np", "TRY": "tr", "PLN": "pl",
}


def _currency_flag(code: str) -> str:
    return wiki.flag_url(_CURRENCY_COUNTRY.get((code or "").upper(), ""))


_asset_versions: dict[tuple[str, float], str] = {}


def _asset(name: str) -> str:
    """/static/<name>?v=<content hash>: a changed CSS/JS file gets a new URL, so browsers
    never pair new HTML with a cached old stylesheet."""
    path = Path(str(_STATIC / name))
    mtime = path.stat().st_mtime
    key = (name, mtime)
    if key not in _asset_versions:
        _asset_versions[key] = zlib.crc32(path.read_bytes()).to_bytes(4, "big").hex()
    return f"/static/{name}?v={_asset_versions[key]}"


# Brand colours for the rate-limit banner, by engine or provider name (SearXNG's names included).
ENGINE_COLORS = {"searxng": "#3050ff", "duckduckgo": "#de5833", "google": "#4285f4", "brave": "#fb542b",
                 "startpage": "#6573ff", "bing": "#00809d", "qwant": "#5c97ff", "mojeek": "#7ab93c", "yahoo": "#6001d2",
                 "yandex": "#fc3f1d", "presearch": "#2d8eff", "wikipedia": "#a2a9b1", "wikidata": "#339966",
                 "serper": "#4285f4", "ecosia": "#4a9e4f", "marginalia": "#c58f2b"}
ENGINE_NAMES = {"duckduckgo": "DuckDuckGo", "google cse": "Google", "google": "Google", "brave": "Brave",
                "brave search": "Brave", "searxng": "SearXNG", "startpage": "Startpage", "bing": "Bing", "qwant": "Qwant"}


def _rate_limits() -> list[dict]:
    """Recent rate limits with a display name and brand colour, for templates/ratelimit_banner.html."""
    out = []
    for entry in active_limits():
        key = entry["engine"].lower()
        color = next((c for name, c in ENGINE_COLORS.items() if name in key), "#8f94ff")
        label = ENGINE_NAMES.get(key, entry["engine"][:1].upper() + entry["engine"][1:])
        reason = re.sub(r"^Suspended:\s*", "", entry["reason"]).strip().rstrip(".")
        out.append({**entry, "key": key, "label": label, "color": color, "reason": reason[:1].lower() + reason[1:] if reason[1:2].islower() else reason})  # keeps "CAPTCHA"
    return out


templates.env.globals["rate_limits"] = _rate_limits
templates.env.globals["asset"] = _asset
templates.env.globals["tmdb_enabled"] = lambda: bool(settings.tmdb_api_key)
templates.env.globals["suggest_min"] = SUGGEST_MIN
templates.env.globals["suggest_max_length"] = SUGGEST_MAX_LENGTH
templates.env.filters["ago"] = _ago
templates.env.filters["currency_flag"] = _currency_flag
templates.env.filters["hue"] = _hue
templates.env.filters["site_name"] = _site_name
templates.env.filters["qs"] = quote_plus
templates.env.filters["money"] = aimodels.money


# ------------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    # Only whether a key exists; the key itself never reaches the page.
    recent = history_store(request).recent(limit=6) if settings.history and settings.personalize else []
    ai = ai_service(request)
    session = ai.session(request.cookies.get("archer_session"))
    response = templates.TemplateResponse(request, "home.html", {"ai_ready": readiness(settings)[0], "recent": recent,
                                                                "csrf": session.csrf})
    ai_cookie(response, session)
    return response


def _prefetch(request: Request) -> bool:
    """Chromium and Firefox mark speculative loads; a search nobody looked at isn't history."""
    return "prefetch" in (request.headers.get("sec-purpose", "") + request.headers.get("purpose", "")).lower()


async def _personal_view(request: Request, view: views.View) -> Personal | None:
    """Rerank this page's results and gather history for it (blocks and site preferences apply even
    with history paused, since they are settings)."""
    personal = None
    if settings.personalize and view.q.strip():
        try:
            personal = await personalizer(request).personalize(view.q, view.results, rerank=view.tab == "all")
            view.results = personal.results
            prefs = history_store(request).sites()
            view.discussions = [d for d in view.discussions if site_pref(d.host, prefs) != -2]
        except Exception as exc:  # personalization is a bonus; the plain page must still render
            log.warning("personalization failed: %s", type(exc).__name__)
            personal = None
    return personal


@app.get("/search")
async def search(request: Request, q: str = Query(""), tab: str = Query("all"), p: int = Query(1, ge=1, le=20), full: bool = False,
                 about: str = "") -> Response:
    if not q.strip() and not full:
        return RedirectResponse("/", status_code=302)
    if any(tok.startswith("!") for tok in q.split()):
        # Bangs: SearXNG's own page handles !engine and !!external redirects; otherwise DuckDuckGo's bangs do.
        target = settings.results_url if settings.search_provider == "searxng" else "https://duckduckgo.com/?q={q}"
        return RedirectResponse(target.format(q=quote_plus(q)), status_code=302)
    tab = tab if tab in views.TABS else "all"
    about = about.strip()[:200]
    store: History = history_store(request)
    chosen = about
    if not about and settings.personalize and tab == "all" and p == 1 and q.strip():
        # A meaning picked from "Other meanings" before is what this name means to you.
        about = store.meaning(q)
    view = await views.build(request.app.state.searx, request.app.state.ext, q, tab, p, about) if q.strip() else views.View("", "all", 1, classify(""))
    if view.response and view.response.redirect:
        return RedirectResponse(view.response.redirect, status_code=302)
    view.remembered = about if about and not chosen else ""
    personal = await _personal_view(request, view)
    history_id = None
    if settings.history and q.strip() and p == 1 and not _prefetch(request):
        hero = ""
        if view.hero:
            hero = view.hero.kind + (":" + str(view.hero.data.get("title", "")) if isinstance(view.hero.data, dict) else "")
        history_id = store.record_search(q, tab, "ai" if full else "search", chosen, hero[:200])
        if chosen:
            store.remember_meaning(q, chosen)
    ai = ai_service(request)
    session = ai.session(request.cookies.get("archer_session"))
    snapshot = ai.snapshot(session, view) if tab == "all" and p == 1 else None
    provider = view.response.provider if view.response else settings.search_provider
    response = templates.TemplateResponse(request, "search.html", {
        "v": view, "provider_page": websearch.page_url(provider, q, views.TABS[tab]),
        "provider_label": websearch.LABELS.get(provider, provider),
        "ai": ai.info(session, snapshot),
        "ai_view": full,
        "history_id": history_id, "personal": personal,
    })
    ai_cookie(response, session)
    return response


@app.get("/ai")
async def ai_page(request: Request, q: str = Query("", max_length=1000)):
    # This renders a workspace/evidence only. Starting research still requires a user POST.
    return await search(request, q, "all", 1, full=True)


@app.get("/suggest")
async def suggest(request: Request, q: str = Query("")) -> JSONResponse:
    result = await request.app.state.searx.autocomplete(q, timeout=settings.suggest_timeout)
    if settings.history and settings.personalize and len(result) >= 2 and isinstance(result[1], list) and q.strip():
        # Your own past searches that start the same way come first, as Google's history suggestions do.
        mine = history_store(request).recent(limit=3, prefix=q)
        if mine:
            seen = {m.casefold() for m in mine}
            result = [result[0], (mine + [t for t in result[1] if isinstance(t, str) and t.casefold() not in seen])[:10], *result[2:]]
    return JSONResponse(result, media_type=_SUGGEST_MIME, headers={"Cache-Control": "no-store"})


# ------------------------------------------------- lazy modules (JSON, GET-safe)

@app.get("/api/strip/{kind}")
async def strip(request: Request, kind: str, q: str = Query(..., min_length=1, max_length=300)) -> JSONResponse:
    if kind not in ("images", "news", "videos"):
        return JSONResponse([], status_code=404)
    resp = await request.app.state.searx.search(q, kind)
    results = rank_images(q, resp.results) if kind == "images" else resp.results
    items = []
    for r in results:
        thumb = r.thumbnail or r.img_src
        if kind != "news" and not thumb:
            continue
        items.append({"title": r.title, "url": r.url, "host": r.host, "thumb": thumb,
                      "ago": _ago(r.date), "source": r.source if kind == "images" else r.host,
                      "length": r.length, **({"full": r.img_src} if kind == "images" and r.img_src else {})})
        if len(items) >= 16:
            break
    return JSONResponse(items)


@app.get("/api/similar")
async def similar(request: Request, title: str = Query("", max_length=300), q: str = Query("", max_length=300),
                  exclude: str = Query("", max_length=2000)) -> JSONResponse:
    """'More like this' for the image side panel: an image search on the picked image's own title."""
    terms = similar_query(title, q)
    if not terms.strip():
        return JSONResponse([])
    resp = await request.app.state.searx.search(terms, "images")
    items = []
    for r in rank_images(terms, resp.results):
        thumb = r.thumbnail or r.img_src
        if not thumb or exclude in (r.img_src, r.thumbnail):
            continue
        items.append({"title": r.title, "url": r.url, "host": r.host, "thumb": thumb, "full": r.img_src,
                      "source": r.source or r.host, "res": r.resolution})
        if len(items) >= 24:
            break
    return JSONResponse(items)


@app.get("/api/places")
async def api_places(request: Request, cat: str, area: str = Query("", max_length=100), near: bool = False) -> JSONResponse:
    # Only known categories reach the Overpass query; the area only goes to the geocoder.
    if cat not in PLACE_TYPES:
        return JSONResponse({"error": "Unknown place type."}, status_code=404)
    try:
        return JSONResponse(await views.places_payload(request.app.state.ext, cat, area, near))
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return JSONResponse({"error": "OpenStreetMap's servers didn't answer in time. Try again in a minute."})


@app.get("/api/product")
async def api_product(request: Request, name: str = Query(..., min_length=1, max_length=120),
                      q: str = Query(..., min_length=1, max_length=300), brand: str = Query("", max_length=60),
                      alias: str = Query("", max_length=120), used: bool = False, stale: bool = False) -> JSONResponse:
    """More prices and reviews for a product panel: this search plus a price search and a review search,
    and for an older model a search on the home country's second-hand market."""
    market = products.secondhand(name, 1, settings.home_country, 9999)["market"] if used else ""
    results = await products.gather(request.app.state.searx, name, q, market)
    return JSONResponse(products.live_parts(name, results, brand=brand, home=settings.home_currency,
                                            aliases=(alias,) if alias else (), country=settings.home_country, old=stale))


@app.get("/api/product/photos")
async def api_product_photos(request: Request, name: str = Query(..., min_length=1, max_length=120),
                             brand: str = Query("", max_length=60), alias: str = Query("", max_length=120)) -> JSONResponse:
    """Photos for a product panel, credited to the maker's site, a store or a review site."""
    resp = await request.app.state.searx.search(name, "images")
    return JSONResponse(products.photos(rank_images(name, resp.results), name, brand, (alias,) if alias else ()))


@app.get("/api/watch")
async def api_watch(request: Request, media: str, tmdb: str) -> JSONResponse:
    """Country-specific streaming providers (TMDB / JustWatch). Needs TMDB_API_KEY."""
    if media not in ("movie", "tv") or not re.fullmatch(r"\d{1,9}", tmdb) or not settings.tmdb_api_key:
        return JSONResponse({"error": "unavailable"}, status_code=404)
    try:
        data = await watch.tmdb_providers(request.app.state.ext, settings.tmdb_api_key, media, tmdb,
                                          settings.home_country)
    except (httpx.HTTPError, ValueError, KeyError):
        return JSONResponse({"error": "TMDB didn't respond"})
    return JSONResponse(data)


@app.get("/api/portraits")
async def api_portraits(request: Request, t: list[str] = Query([], max_length=6)) -> JSONResponse:
    """Wikipedia thumbnails for the people, places and works an AI answer's key facts name."""
    titles = [title[:120] for title in t if title.strip()]
    if not titles:
        return JSONResponse({})
    try:
        return JSONResponse(await wiki.portraits(request.app.state.ext, titles))
    except (httpx.HTTPError, ValueError, KeyError):
        return JSONResponse({})


@app.get("/api/works")
async def api_works(request: Request, qid: str, role: str) -> JSONResponse:
    try:
        return JSONResponse(await wiki.works(request.app.state.ext, qid, role))
    except (httpx.HTTPError, ValueError, KeyError):
        return JSONResponse([])


_QID = re.compile(r"Q\d{1,12}")
_TMDB_ID = re.compile(r"\d{0,9}")


@app.get("/api/cast")
async def api_cast(request: Request, qid: str, media: str = "movie", tmdb: str = "") -> JSONResponse:
    """Full cast and key crew of a film or series, with photos (TMDB when keyed, else Wikidata + Wikipedia)."""
    if not _QID.fullmatch(qid) or media not in ("movie", "tv") or not _TMDB_ID.fullmatch(tmdb):
        return JSONResponse({"error": "bad request"}, status_code=400)
    try:
        return JSONResponse(await credits.cast(request.app.state.ext, qid, media, tmdb, settings.tmdb_api_key))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return JSONResponse({"cast": [], "crew": [], "error": "unavailable"})


@app.get("/api/filmography")
async def api_filmography(request: Request, qid: str, tmdb: str = "") -> JSONResponse:
    """An actor's films and series with posters, roles and years."""
    if not _QID.fullmatch(qid) or not _TMDB_ID.fullmatch(tmdb):
        return JSONResponse({"error": "bad request"}, status_code=400)
    try:
        return JSONResponse(await credits.filmography(request.app.state.ext, qid, tmdb, settings.tmdb_api_key))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return JSONResponse({"items": [], "error": "unavailable"})


@app.get("/api/spotify")
async def api_spotify(request: Request, title: str = Query(..., min_length=1, max_length=120),
                      artist: str = Query("", max_length=120)) -> JSONResponse:
    """The Spotify player for a song or album Wikidata has no Spotify ID for, found through a web search."""
    resp = await request.app.state.searx.search(f"{title} {artist} spotify".strip())
    return JSONResponse(views.spotify_from_results(resp.results, title, artist) or {})


@app.get("/api/news/thumb")
async def api_news_thumb(request: Request, url: str = Query(..., min_length=8, max_length=2000)) -> JSONResponse:
    """A news card's picture when the engine sent none: the article's own share image (og:image).
    Only this page's own scripts may ask; the reader only connects to public addresses."""
    if request.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        return JSONResponse({}, status_code=403)
    return JSONResponse({"thumb": await lead_image(url)})


@app.get("/api/lyrics")
async def api_lyrics(request: Request, title: str = Query("", max_length=120),
                     artist: str = Query("", max_length=120)) -> JSONResponse:
    """Lyrics matched by song title and optional artist, loaded only for music panels."""
    try:
        return JSONResponse(await lyrics.lookup(request.app.state.ext, title, artist))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return JSONResponse({})


_FAVICON_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
_FAVICON_TYPES = ("image/x-icon", "image/vnd.microsoft.icon", "image/png", "image/jpeg", "image/gif", "image/webp")
_favicons = TTLCache(ttl=7 * 86400, size=2000)


@app.get("/api/favicon/{host}")
async def favicon(request: Request, host: str) -> Response:
    """Site icons for result badges, fetched server-side and cached, so the browser
    never contacts a third party per result. Misses return 404 and the page keeps the letter."""
    host = host.lower()
    if not _FAVICON_HOST.match(host):
        return Response(status_code=404)
    hit = _favicons.get(host)
    if hit is None:
        hit = ()
        # Google's 64 px icon first (crisp on high-DPI badges; unknown sites get a 404), then DuckDuckGo's.
        for url in (f"https://www.google.com/s2/favicons?domain={host}&sz=64", f"https://icons.duckduckgo.com/ip3/{host}.ico"):
            try:
                resp = await request.app.state.ext.get(url, timeout=4)
            except httpx.HTTPError:
                continue
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.status_code == 200 and ctype in _FAVICON_TYPES and 0 < len(resp.content) < 200_000:
                hit = (resp.content, ctype)
                break
        _favicons.put(host, hit, ttl=None if hit else 3600)
    if not hit:
        return Response(status_code=404)
    # Third-party bytes on our origin: never sniff, never execute (SVG is refused above).
    return Response(hit[0], media_type=hit[1], headers={
        "Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


@app.get("/api/weather")
async def api_weather(request: Request, lat: float, lon: float) -> JSONResponse:
    try:
        data = await weather.forecast(request.app.state.ext, lat, lon)
    except (httpx.HTTPError, ValueError, KeyError):
        return JSONResponse({})
    return JSONResponse({"temp": data["temp"], "label": data["label"], "icon": data["icon"],
                         "time": weather.local_time(data["timezone"])})


# ------------------------------------------------------------------- history

def _day_label(at: float) -> str:
    day = datetime.fromtimestamp(at).date()
    today = datetime.now().date()
    if day == today:
        return "Today"
    if (today - day).days == 1:
        return "Yesterday"
    if (today - day).days < 7:
        return day.strftime("%A")
    return day.strftime("%A, %d %B %Y").replace(" 0", " ")


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, q: str = Query("", max_length=300), before: float | None = None):
    """Your searches by day, each with the results you opened; searchable by meaning."""
    store: History = history_store(request)
    personal: Personalizer = personalizer(request)
    if q.strip():
        entries = store.entries(ids=await personal.search_history(q))
    else:
        entries = store.entries(before=before, limit=60)
    days: list[tuple[str, list]] = []
    for entry in entries:
        entry["time"] = datetime.fromtimestamp(entry["last_at"]).strftime("%H:%M")
        label = _day_label(entry["last_at"])
        if not days or days[-1][0] != label:
            days.append((label, []))
        days[-1][1].append(entry)
    prefs = store.sites()
    ai = ai_service(request)
    session = ai.session(request.cookies.get("archer_session"))
    response = templates.TemplateResponse(request, "history.html", {
        "q": q, "days": days, "stats": store.stats(), "csrf": session.csrf,
        "more": entries[-1]["last_at"] if len(entries) == 60 and not q.strip() else None,
        "topics": [] if q.strip() or before else await personal.topics(),
        "top_sites": [] if q.strip() or before else [h | {"pref": site_pref(h["host"], prefs)} for h in store.top_hosts()],
        "prefs": sorted(prefs.items()), "enabled": settings.history, "personalize": settings.personalize,
        "model": embeddings.status(),
    })
    response.headers["Cache-Control"] = "no-store"
    ai_cookie(response, session)
    return response


# ------------------------------------------------------------------- setup

def _companion(request: Request, template: str):
    session, context = settings_context(request)
    response = templates.TemplateResponse(request, template, context)
    response.headers["Cache-Control"] = "no-store"
    ai_cookie(response, session)
    return response


@app.get("/app", response_class=HTMLResponse)
async def companion_page(request: Request):
    """The companion app: overview and every setting. Opens as its own window from the tray."""
    return _companion(request, "companion.html")


@app.get("/app/welcome", response_class=HTMLResponse)
async def welcome_page(request: Request):
    """First-run setup: search, AI, personal defaults, startup and browser, one step at a time."""
    return _companion(request, "welcome.html")


@app.get("/settings")
def settings_redirect() -> RedirectResponse:
    # Settings became the companion app; the browser keeps any #section across the redirect.
    return RedirectResponse("/app", status_code=307)


@app.get("/setup")
def setup() -> RedirectResponse:
    return RedirectResponse("/app#browser", status_code=307)


@app.get("/opensearch.xml")
def opensearch() -> Response:
    base = escape(settings.base_url)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>Archer</ShortName>
  <Description>Personal AI search engine</Description>
  <InputEncoding>UTF-8</InputEncoding>
  <Url type="text/html" method="get" template="{base}/search?q={{searchTerms}}"/>
  <Url type="{_SUGGEST_MIME}" method="get" template="{base}/suggest?q={{searchTerms}}"/>
</OpenSearchDescription>
"""
    return Response(xml, media_type="application/opensearchdescription+xml")


@app.get("/health")
def health() -> dict[str, str]:
    return {"app": "archer", "version": __version__}
