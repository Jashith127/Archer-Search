"""Search backends other than SearXNG, and the keyless suggestion endpoints.

Every backend turns its provider's reply into the same `SearchResponse` the SearXNG client
returns, so the results page, strips and AI tools don't know which one answered.

- DuckDuckGo: no key. DuckDuckGo has no official web-results API, so this reads its HTML-only
  page (html.duckduckgo.com) and the JSON its own image/news/video tabs use. Best effort: it may
  rate-limit or change markup, which is why it is also the automatic fallback rather than the
  only option.
- Google: Serper.dev (a Google results API; 2,500 free queries on sign-up), or a Programmable
  Search Engine key + engine ID. Google closed that JSON API to new customers and turns it off
  on 1 January 2027, so it is offered only for people who already have one.
- Brave: Brave's official Search API (subscription token; a small monthly credit).
"""
import html
import re
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx

from archer.config import settings
from archer.searxng import Result, SearchResponse, _http_url, _to_result

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

LABELS = {"searxng": "SearXNG", "duckduckgo": "DuckDuckGo", "google": "Google", "brave": "Brave Search"}
# Categories each provider can answer itself; anything else is borrowed from DuckDuckGo.
CATEGORIES = {
    "searxng": {"general", "images", "news", "videos"},
    "duckduckgo": {"general", "images", "news", "videos"},
    "brave": {"general", "images", "news", "videos"},
    "google:serper": {"general", "images", "news", "videos"},
    "google:cse": {"general", "images"},
}


class ProviderError(Exception):
    pass


def backend() -> str:
    provider = settings.search_provider
    return f"google:{settings.google_backend}" if provider == "google" else provider


def configured(provider: str | None = None) -> tuple[bool, str]:
    """Whether a provider has what it needs, and if not, what to add."""
    provider = provider or settings.search_provider
    if provider == "brave" and not settings.brave_api_key:
        return False, "Add a Brave Search API key."
    if provider == "google":
        if settings.google_backend == "serper" and not settings.serper_api_key:
            return False, "Add a Serper API key for Google results."
        if settings.google_backend == "cse" and not (settings.google_api_key and settings.google_cx):
            return False, "Add a Google API key and search engine ID."
    return True, ""


def page_url(provider: str, q: str, category: str = "general") -> str:
    """The provider's own results page for a query, for the 'view on…' link."""
    term = quote_plus(q)
    if provider == "searxng":
        return settings.results_url.format(q=term)
    if provider.startswith("google"):
        tab = {"images": "&udm=2", "news": "&tbm=nws", "videos": "&tbm=vid"}.get(category, "")
        return f"https://www.google.com/search?q={term}{tab}"
    if provider == "brave":
        tab = {"images": "images", "news": "news", "videos": "videos"}.get(category, "search")
        return f"https://search.brave.com/{tab}?q={term}"
    tab = {"images": "&ia=images&iax=images", "news": "&ia=news&iar=news", "videos": "&ia=videos&iax=videos"}.get(category, "")
    return f"https://duckduckgo.com/?q={term}{tab}"


# ------------------------------------------------------------------ locale

def _lang() -> str:
    lang = settings.search_language
    return "en" if not lang or lang in ("all", "auto") else lang.split("-")[0].lower()


def _country() -> str:
    return (settings.home_country or "US").lower()


def ddg_region() -> str:
    if settings.search_language == "all":
        return "wt-wt"
    country = {"gb": "uk"}.get(_country(), _country())
    return f"{country}-{_lang()}"


def _ddg_safe() -> str:
    # DuckDuckGo: 1 strict, -1 moderate, -2 off.
    return {0: "-2", 1: "-1", 2: "1"}.get(settings.safesearch, "-1")


# ---------------------------------------------------------------- DuckDuckGo

_DDG_BLOCK = re.compile(r'<div class="result results_links[^"]*"(.*?)<div class="clear">', re.S)
_DDG_TITLE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</(?:a|div|td)>', re.S)
_TAGS = re.compile(r"<[^>]+>")
_VQD = re.compile(r"""vqd=["']?([\d-]+)""")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", fragment))).strip()


def _ddg_link(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.hostname and parts.hostname.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        href = parse_qs(parts.query).get("uddg", [""])[0]
    return _http_url(href)


def parse_ddg_html(page: str) -> list[Result]:
    out = []
    for block in _DDG_BLOCK.findall(page):
        if "result--ad" in block[:200] or 'class="badge--ad' in block:
            continue
        title = _DDG_TITLE.search(block)
        if not title:
            continue
        url = _ddg_link(title.group(1))
        if not url or "duckduckgo.com/y.js" in url:
            continue
        snippet = _DDG_SNIPPET.search(block)
        result = _to_result({"url": url, "title": _text(title.group(2)), "content": _text(snippet.group(1)) if snippet else "",
                             "engines": ["duckduckgo"]})
        if result:
            out.append(result)
    return out


async def _ddg_vqd(client: httpx.AsyncClient, q: str) -> str:
    resp = await client.get("https://duckduckgo.com/", params={"q": q, "ia": "web"},
                            headers={"User-Agent": BROWSER_UA}, timeout=8)
    match = _VQD.search(resp.text)
    if not match:
        raise ProviderError("DuckDuckGo didn't return a search token")
    return match.group(1)


async def duckduckgo(client: httpx.AsyncClient, q: str, category: str, page: int) -> SearchResponse:
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://html.duckduckgo.com/"}
    region = ddg_region()
    if category == "general":
        data = {"q": q, "kl": region, "kp": _ddg_safe(), "b": ""}
        if page > 1:
            start = (page - 1) * 10
            data.update({"s": str(start), "dc": str(start + 1), "v": "l", "o": "json", "api": "d.js", "nextParams": ""})
        resp = await client.post("https://html.duckduckgo.com/html/", data=data, headers=headers, timeout=10)
        if resp.status_code == 202:
            # DuckDuckGo answers a bot check with 202 and a challenge body rather
            # than a 4xx. Reporting it as "returned HTTP 202" hid the real cause
            # from the rate-limit tracker, so the provider was retried on every
            # subsequent search instead of entering its cooldown.
            raise ProviderError("DuckDuckGo is bot-checking this computer (HTTP 202)")
        if resp.status_code != 200:
            raise ProviderError(f"DuckDuckGo returned HTTP {resp.status_code}")
        if "anomaly-modal" in resp.text or "Unfortunately, bots use DuckDuckGo too" in resp.text:
            raise ProviderError("DuckDuckGo asked for a CAPTCHA; it is rate-limiting this computer")
        return SearchResponse(results=parse_ddg_html(resp.text), provider="duckduckgo")

    vqd = await _ddg_vqd(client, q)
    endpoint = {"images": "i.js", "news": "news.js", "videos": "v.js"}[category]
    params = {"l": region, "o": "json", "q": q, "vqd": vqd, "p": "1" if settings.safesearch == 2 else "-1"}
    if category == "images":
        params["f"] = ",,,,,"
    if page > 1:
        params["s"] = str((page - 1) * 50)
    resp = await client.get(f"https://duckduckgo.com/{endpoint}", params=params,
                            headers={**headers, "Referer": "https://duckduckgo.com/"}, timeout=10)
    if resp.status_code == 202:
        raise ProviderError("DuckDuckGo is bot-checking this computer (HTTP 202)")
    if resp.status_code != 200:
        raise ProviderError(f"DuckDuckGo returned HTTP {resp.status_code}")
    items = resp.json().get("results", [])
    results = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        if category == "images":
            item = {"url": raw.get("url"), "title": raw.get("title"), "img_src": raw.get("image"),
                    "thumbnail_src": raw.get("thumbnail"), "source": raw.get("source") or "",
                    "resolution": f"{raw['width']} × {raw['height']}" if raw.get("width") and raw.get("height") else ""}
        elif category == "news":
            item = {"url": raw.get("url"), "title": raw.get("title"), "content": _text(raw.get("excerpt") or ""),
                    "thumbnail_src": raw.get("image"), "source": raw.get("source") or "",
                    "publishedDate": _iso(raw.get("date"))}
        else:
            images = raw.get("images") if isinstance(raw.get("images"), dict) else {}
            item = {"url": raw.get("content"), "title": raw.get("title"), "content": raw.get("description") or "",
                    "thumbnail_src": images.get("medium") or images.get("small"), "length": raw.get("duration") or "",
                    "source": raw.get("publisher") or "", "publishedDate": raw.get("published") or ""}
        result = _to_result({**item, "engines": ["duckduckgo"]})
        if result:
            results.append(result)
    return SearchResponse(results=results, provider="duckduckgo")


def _iso(epoch) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


# --------------------------------------------------------------------- Brave

async def brave(client: httpx.AsyncClient, q: str, category: str, page: int) -> SearchResponse:
    endpoint = {"general": "web", "images": "images", "news": "news", "videos": "videos"}[category]
    params = {"q": q, "country": _country().upper(), "search_lang": _lang(),
              "safesearch": {0: "off", 2: "strict"}.get(settings.safesearch, "moderate")}
    if category == "images":
        params["count"] = 50
        if params["safesearch"] == "moderate":
            params["safesearch"] = "strict"  # the image endpoint accepts only off / strict
    else:
        params.update({"count": 20, "offset": min(page - 1, 9)})
    resp = await client.get(f"https://api.search.brave.com/res/v1/{endpoint}/search", params=params,
                            headers={"X-Subscription-Token": settings.brave_api_key, "Accept": "application/json"}, timeout=10)
    if resp.status_code in (401, 403):
        raise ProviderError("Brave rejected the API key")
    if resp.status_code == 429:
        raise ProviderError("Brave's rate limit or monthly credit is used up")
    if resp.status_code != 200:
        raise ProviderError(f"Brave returned HTTP {resp.status_code}")
    data = resp.json()
    items = (data.get("web") or {}).get("results", []) if category == "general" else data.get("results", [])
    results = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        thumb = (raw.get("thumbnail") or {}).get("src") if isinstance(raw.get("thumbnail"), dict) else ""
        meta = raw.get("meta_url") if isinstance(raw.get("meta_url"), dict) else {}
        item = {"url": raw.get("url"), "title": raw.get("title"), "content": _text(raw.get("description") or ""),
                "thumbnail_src": thumb, "publishedDate": raw.get("page_age") or "", "source": meta.get("hostname") or ""}
        if category == "images":
            props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
            item.update({"img_src": props.get("url") or thumb, "source": raw.get("source") or ""})
        if category == "videos":
            video = raw.get("video") if isinstance(raw.get("video"), dict) else {}
            item.update({"length": video.get("duration") or "", "source": video.get("publisher") or item["source"]})
        result = _to_result({**item, "engines": ["brave"]})
        if result:
            results.append(result)
    return SearchResponse(results=results, provider="brave")


# -------------------------------------------------------------------- Google

async def serper(client: httpx.AsyncClient, q: str, category: str, page: int) -> SearchResponse:
    endpoint = {"general": "search", "images": "images", "news": "news", "videos": "videos"}[category]
    body = {"q": q, "gl": _country(), "hl": _lang(), "page": page}
    resp = await client.post(f"https://google.serper.dev/{endpoint}", json=body,
                             headers={"X-API-KEY": settings.serper_api_key}, timeout=10)
    if resp.status_code in (401, 403):
        raise ProviderError("Serper rejected the API key")
    if resp.status_code == 429 or resp.status_code == 400 and "credits" in resp.text.lower():
        raise ProviderError("Serper's credits are used up")
    if resp.status_code != 200:
        raise ProviderError(f"Serper returned HTTP {resp.status_code}")
    data = resp.json()
    key = {"general": "organic", "images": "images", "news": "news", "videos": "videos"}[category]
    results = []
    for raw in data.get(key, []) if isinstance(data.get(key), list) else []:
        if not isinstance(raw, dict):
            continue
        item = {"url": raw.get("link"), "title": raw.get("title"), "content": raw.get("snippet") or "",
                "publishedDate": raw.get("date") or "", "source": raw.get("source") or raw.get("channel") or ""}
        if category == "images":
            item.update({"img_src": raw.get("imageUrl"), "thumbnail_src": raw.get("thumbnailUrl"),
                         "source": raw.get("domain") or raw.get("source") or "",
                         "resolution": f"{raw['imageWidth']} × {raw['imageHeight']}" if raw.get("imageWidth") and raw.get("imageHeight") else ""})
        elif category in ("news", "videos"):
            item.update({"thumbnail_src": raw.get("imageUrl"), "length": raw.get("duration") or ""})
        result = _to_result({**item, "engines": ["google"]})
        if result:
            results.append(result)
    related = [r.get("query") for r in data.get("relatedSearches", []) if isinstance(r, dict) and isinstance(r.get("query"), str)]
    return SearchResponse(results=results, suggestions=related, provider="google")


async def google_cse(client: httpx.AsyncClient, q: str, category: str, page: int) -> SearchResponse:
    params = {"key": settings.google_api_key, "cx": settings.google_cx, "q": q, "num": 10,
              "start": (page - 1) * 10 + 1, "gl": _country(), "hl": _lang(),
              "safe": "active" if settings.safesearch in (1, 2) else "off"}
    if settings.search_language and settings.search_language not in ("all", "auto"):
        params["lr"] = "lang_" + _lang()
    if category == "images":
        params["searchType"] = "image"
    resp = await client.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=10)
    if resp.status_code in (400, 403):
        raise ProviderError("Google rejected the API key or search engine ID")
    if resp.status_code == 429:
        raise ProviderError("Google's daily query limit is used up")
    if resp.status_code != 200:
        raise ProviderError(f"Google returned HTTP {resp.status_code}")
    results = []
    for raw in resp.json().get("items", []) or []:
        if not isinstance(raw, dict):
            continue
        item = {"url": raw.get("link"), "title": raw.get("title"), "content": raw.get("snippet") or ""}
        if category == "images":
            image = raw.get("image") if isinstance(raw.get("image"), dict) else {}
            item.update({"url": image.get("contextLink") or raw.get("link"), "img_src": raw.get("link"),
                         "thumbnail_src": image.get("thumbnailLink"), "source": raw.get("displayLink") or "",
                         "resolution": f"{image['width']} × {image['height']}" if image.get("width") and image.get("height") else ""})
        else:
            pagemap = raw.get("pagemap") if isinstance(raw.get("pagemap"), dict) else {}
            thumbs = pagemap.get("cse_thumbnail") if isinstance(pagemap.get("cse_thumbnail"), list) else []
            item["thumbnail_src"] = thumbs[0].get("src") if thumbs and isinstance(thumbs[0], dict) else ""
        result = _to_result({**item, "engines": ["google"]})
        if result:
            results.append(result)
    return SearchResponse(results=results, provider="google")


BACKENDS = {"duckduckgo": duckduckgo, "brave": brave, "google:serper": serper, "google:cse": google_cse}


async def search(client: httpx.AsyncClient, name: str, q: str, category: str, page: int) -> SearchResponse:
    """Run one backend; failures come back as a response with `error`, never an exception."""
    label = LABELS.get(name.split(":")[0], name)
    ok, missing = configured(name.split(":")[0])
    if not ok:
        return SearchResponse(error=f"{label} isn't set up yet. {missing}", provider=name.split(":")[0])
    try:
        return await BACKENDS[name](client, q, category, page)
    except ProviderError as exc:
        return SearchResponse(error=str(exc), provider=name.split(":")[0])
    except httpx.HTTPError as exc:
        return SearchResponse(error=f"{label} unreachable ({type(exc).__name__})", provider=name.split(":")[0])
    except (ValueError, KeyError, TypeError, AttributeError):
        return SearchResponse(error=f"{label} returned an unreadable response", provider=name.split(":")[0])


# --------------------------------------------------------------- suggestions

def suggestion_source() -> str:
    """Where address-bar suggestions come from. 'auto' follows the search provider."""
    choice = settings.suggest_provider
    if choice != "auto":
        return choice
    return {"searxng": "searxng", "google": "google"}.get(settings.search_provider, "duckduckgo")


async def suggest(client: httpx.AsyncClient, source: str, q: str, timeout: float) -> list:
    """Raw OpenSearch reply from a keyless endpoint: [query, [terms], ...]."""
    if source == "google":
        resp = await client.get("https://suggestqueries.google.com/complete/search",
                                params={"client": "firefox", "q": q, "hl": _lang(), "gl": _country(), "ie": "utf-8", "oe": "utf-8"},
                                headers={"User-Agent": BROWSER_UA}, timeout=timeout)
    else:
        resp = await client.get("https://duckduckgo.com/ac/", params={"q": q, "kl": ddg_region(), "type": "list"},
                                headers={"User-Agent": BROWSER_UA}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
