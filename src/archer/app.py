from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote_plus
import re
import zlib

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from archer import __version__, views
from archer.imagerank import rank_images, similar_query
from archer.config import settings
from archer.query import PLACE_TYPES, classify
from archer.searxng import SearXNG, SUGGEST_MIN, SUGGEST_MAX_LENGTH
from archer.sources import USER_AGENT, TTLCache, aimodels, lyrics, watch, weather, wiki
from archer.ai_routes import router as ai_router, service as ai_service, set_cookie as ai_cookie
from archer.ai_service import AIService
from archer.settings_routes import page_context as settings_context, router as settings_router
from archer.config import DATA_DIR


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
        yield
        await app.state.ai.close()


app = FastAPI(title="Archer", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.include_router(ai_router)
app.include_router(settings_router)

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
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ------------------------------------------------------------ template helpers

def _ago(value: str) -> str:
    if not value:
        return ""
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
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
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "home.html", {})


@app.get("/search")
async def search(request: Request, q: str = Query(""), tab: str = Query("all"), p: int = Query(1, ge=1, le=20), full: bool = False) -> Response:
    if not q.strip() and not full:
        return RedirectResponse("/", status_code=302)
    if any(tok.startswith("!") for tok in q.split()):
        # Bangs: SearXNG's own page handles !engine and !!external redirects.
        return RedirectResponse(settings.results_url.format(q=quote_plus(q)), status_code=302)
    tab = tab if tab in views.TABS else "all"
    view = await views.build(request.app.state.searx, request.app.state.ext, q, tab, p) if q.strip() else views.View("", "all", 1, classify(""))
    if view.response and view.response.redirect:
        return RedirectResponse(view.response.redirect, status_code=302)
    ai = ai_service(request)
    session = ai.session(request.cookies.get("archer_session"))
    snapshot = ai.snapshot(session, view) if tab == "all" and p == 1 else None
    response = templates.TemplateResponse(request, "search.html", {
        "v": view, "searxng_page": settings.results_url.format(q=quote_plus(q)),
        "ai": ai.info(session, snapshot),
        "ai_view": full,
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
                      "length": r.length})
        if len(items) >= 12:
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


@app.get("/api/works")
async def api_works(request: Request, qid: str, role: str) -> JSONResponse:
    try:
        return JSONResponse(await wiki.works(request.app.state.ext, qid, role))
    except (httpx.HTTPError, ValueError, KeyError):
        return JSONResponse([])


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


# ------------------------------------------------------------------- setup

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    session, context = settings_context(request)
    response = templates.TemplateResponse(request, "settings.html", context)
    response.headers["Cache-Control"] = "no-store"
    ai_cookie(response, session)
    return response


@app.get("/setup")
def setup() -> RedirectResponse:
    # Browser setup now lives in the Settings app.
    return RedirectResponse("/settings#browser", status_code=307)


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
