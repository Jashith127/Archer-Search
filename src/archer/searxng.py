"""Archer's search front: one cached entry point for results and suggestions.

The configured provider answers (SearXNG here; DuckDuckGo, Google and Brave in `websearch.py`).
Upstream engines rate-limit and CAPTCHA quickly, so identical requests (reloads,
back/forward, lazy modules) are served from memory instead of fanning out again. When the
provider fails or is rate-limited, the next provider that is set up answers instead (if the fallback is
on) and the page says so. Rate limits are remembered for a cooldown, so later searches go straight to a
free provider, and the results page shows them in a banner until dismissed.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from archer.config import settings

CACHE_TTL = 600  # seconds
_CACHE_MAX = 256
SUGGEST_MIN = 2
SUGGEST_MAX_LENGTH = 300
SUGGEST_LIMIT = 8
SUGGEST_TTL = 60


def suggestion_terms(values: object, q: str) -> list[str]:
    """Bound and deduplicate provider text without changing its ordering."""
    if not isinstance(values, list):
        return []
    out = []
    seen = {q.strip().casefold()}
    for value in values:
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value or len(value) > SUGGEST_MAX_LENGTH or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        out.append(value)
        if len(out) == SUGGEST_LIMIT:
            break
    return out


def suggestion_eligible(q: str) -> bool:
    text = q.strip()
    return len(q) <= SUGGEST_MAX_LENGTH and bool(text) and (
        len(text) >= SUGGEST_MIN or text.startswith(("!", ":"))
    )


@dataclass
class Result:
    title: str
    url: str
    host: str
    crumbs: list[str]
    content: str
    engines: list[str]
    date: str = ""
    thumbnail: str = ""
    img_src: str = ""
    length: str = ""
    source: str = ""
    resolution: str = ""
    visited: float = 0.0  # when you last opened this result (history), else 0
    personal: str = ""    # why personalization moved it: "raised", "lowered", "often"


@dataclass
class SearchResponse:
    results: list[Result] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)
    infoboxes: list[dict] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    unresponsive: list[tuple[str, str]] = field(default_factory=list)
    redirect: str | None = None
    error: str | None = None
    provider: str = "searxng"  # which provider answered
    notice: str | None = None  # e.g. "SearXNG didn't answer, so these results are from DuckDuckGo"
    limited: list[tuple[str, str]] = field(default_factory=list)  # (engine, reason) rate-limited for this search


# ------------------------------------------------------------- rate limits
# Engine suspensions inside SearXNG ("Suspended: too many requests", "CAPTCHA", "access denied") and
# provider errors (429, used-up credits, DuckDuckGo's bot check) all count.
RATE_LIMITED = re.compile(r"suspend|too many|captcha|access denied|rate.?limit|\b429\b|credits|used up|limit", re.I)
COOLDOWN = 600       # seconds a rate-limited provider is skipped
SHOWN_FOR = 3600     # seconds a rate limit stays in the banner after it was last seen
_limits: dict[str, dict] = {}


def note_limit(engine: str, reason: str, provider: str = "") -> None:
    """Remember a rate limit. `provider` names the whole provider to skip ("searxng", "duckduckgo")."""
    now = time.time()
    # One entry per engine family: SearXNG reports "brave.images", "google cse" and "startpage news" separately.
    engine = re.split(r"[ .]", engine.strip())[0] if not provider else engine
    key = f"{provider}|{engine}".lower()
    entry = _limits.get(key) or {"engine": engine, "provider": provider, "since": now}
    entry.update(reason=reason, seen=now, until=now + COOLDOWN)
    _limits[key] = entry


def cooling(provider: str) -> bool:
    now = time.time()
    return any(e["provider"] == provider and e["until"] > now for e in _limits.values())


def active_limits() -> list[dict]:
    """Rate limits seen within the last hour, newest first, for the banner."""
    now = time.time()
    for key in [k for k, e in _limits.items() if now - e["seen"] > SHOWN_FOR]:
        _limits.pop(key)
    return sorted(_limits.values(), key=lambda e: e["seen"], reverse=True)


def fallback_order(primary: str) -> list[str]:
    """The configured provider first, then every other one that can answer: Google and Brave when their
    keys are set, keyless DuckDuckGo, then a local SearXNG."""
    ready = {"google:serper": bool(settings.serper_api_key), "google:cse": bool(settings.google_api_key and settings.google_cx),
             "brave": bool(settings.brave_api_key), "duckduckgo": True, "searxng": True}
    return [primary] + [name for name, ok in ready.items() if ok and name != primary]


def _http_url(value: str | None) -> str:
    return value if value and value.startswith(("http://", "https://")) else ""


def _to_result(raw: dict) -> Result | None:
    url = _http_url(raw.get("url"))
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    crumbs = [p for p in parts.path.split("/") if p][:3]
    date = raw.get("publishedDate") or ""
    return Result(
        title=(raw.get("title") or url).strip(),
        url=url,
        host=host.removeprefix("www."),
        crumbs=crumbs,
        content=(raw.get("content") or "").strip(),
        engines=raw.get("engines") or [raw.get("engine", "")],
        date=date,
        thumbnail=_http_url(raw.get("thumbnail_src") or raw.get("thumbnail")),
        img_src=_http_url(raw.get("img_src")),
        length=str(raw.get("length") or ""),
        source=raw.get("source") or raw.get("metadata") or "",
        resolution=raw.get("resolution") or "",
    )


class Search:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._cache: dict[tuple, tuple[float, SearchResponse]] = {}
        self._search_pending: dict[tuple, asyncio.Task] = {}
        self._suggest_cache: dict[str, tuple[float, list]] = {}
        self._suggest_pending: dict[str, asyncio.Task] = {}

    async def search(self, q: str, category: str = "general", page: int = 1) -> SearchResponse:
        # Search preferences are part of the key, so changing them never serves stale results.
        key = self._key(q, category, page)
        if key not in self._search_pending:
            task = asyncio.create_task(self._search(q, category, page))
            self._search_pending[key] = task
            task.add_done_callback(lambda done: self._search_pending.pop(key, None))
        return await asyncio.shield(self._search_pending[key])

    @staticmethod
    def _key(q, category, page):
        from archer import websearch
        return (q, category, page, settings.safesearch, settings.search_language, settings.home_country,
                websearch.backend(), settings.search_fallback)

    async def _search(self, q: str, category: str = "general", page: int = 1) -> SearchResponse:
        from archer import websearch
        key = self._key(q, category, page)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]

        name = websearch.backend()
        if category not in websearch.CATEGORIES.get(name, ()):
            name = "duckduckgo"  # e.g. news from a Google key that only covers web and images
        names = fallback_order(name) if settings.search_fallback else [name]
        names = [n for n in names if category in websearch.CATEGORIES.get(n, ())]
        # A provider still cooling down from a rate limit is skipped, unless nothing else is left.
        fresh = [n for n in names if not cooling(n.split(":")[0])]
        names = fresh or names[:1]
        primary = websearch.LABELS.get(name.split(":")[0], name)
        # (who failed first, why): the configured provider, even when its cooldown meant it wasn't asked.
        first = (primary, "rate-limited") if names[0] != name else None
        out, limited = SearchResponse(error="No search provider is available."), []
        for current in names:
            got = await (self._searxng(q, category, page) if current == "searxng"
                         else websearch.search(self._client, current, q, category, page))
            base, label = current.split(":")[0], websearch.LABELS.get(current.split(":")[0], current)
            engines = [(engine, why) for engine, why in got.unresponsive if RATE_LIMITED.search(why or "")]
            for engine, why in engines:
                note_limit(engine, why)
            empty = not (got.results or got.answers or got.redirect)
            if (got.error and RATE_LIMITED.search(got.error)) or (base == "searxng" and empty and engines):
                reason = got.error or "every engine is rate-limited"
                note_limit(label, reason, base)  # skip the whole provider for the cooldown
                engines.append((label, reason))
            limited += [e for e in engines if e not in limited]
            if first is None:
                first = (label, "rate-limited" if engines else got.error or "no results")
            out = got
            if not empty or not (got.error or engines):
                break  # an answer, or a genuine "nothing found" from a working provider
        out.limited = limited
        if limited and not (out.results or out.answers or out.redirect or out.error):
            out.error = "Every search provider Archer can use is rate-limited right now" if len(names) > 1 else \
                f"{websearch.LABELS.get(out.provider, out.provider)} is rate-limited right now"
        if first and out.results and out.provider != name.split(":")[0]:
            why = "is rate-limited" if first[1] == "rate-limited" else f"didn't answer ({first[1]})"
            out.notice = f"{first[0]} {why}, so these results are from {websearch.LABELS.get(out.provider, out.provider)}."
        if out.results or out.answers:  # don't pin an empty, rate-limited response
            if len(self._cache) >= _CACHE_MAX:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic(), out)
        return out

    async def _searxng(self, q: str, category: str, page: int) -> SearchResponse:
        params = {"q": q, "format": "json", "categories": category, "pageno": page}
        if settings.safesearch is not None:
            params["safesearch"] = settings.safesearch
        if settings.search_language:
            params["language"] = settings.search_language
        try:
            resp = await self._client.get("/search", params=params, timeout=12)
        except httpx.HTTPError as exc:
            return SearchResponse(error=f"SearXNG unreachable ({type(exc).__name__})")
        if resp.is_redirect:  # external bangs (!!g) make SearXNG redirect
            return SearchResponse(redirect=resp.headers.get("location"))
        if resp.status_code != 200:
            return SearchResponse(error=f"SearXNG returned HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            return SearchResponse(error="SearXNG returned invalid JSON")

        out = SearchResponse(
            provider="searxng",
            results=[r for r in map(_to_result, data.get("results", [])) if r],
            answers=data.get("answers", []),
            infoboxes=data.get("infoboxes", []),
            suggestions=data.get("suggestions", []),
            corrections=data.get("corrections", []),
            unresponsive=[tuple(u) for u in data.get("unresponsive_engines", [])],
        )
        return out

    async def autocomplete(self, q: str, timeout: float = 1.5) -> list:
        from archer import websearch
        source = websearch.suggestion_source()
        if not suggestion_eligible(q) or source == "off":
            return [q, []]
        key = q if source == "searxng" else f"{source}|{q}"
        hit = self._suggest_cache.get(key)
        if hit and time.monotonic() < hit[0]:
            return hit[1]
        task = self._suggest_pending.get(key)
        if task is None:
            task = asyncio.create_task(self._autocomplete(q, timeout, source, key))
            self._suggest_pending[key] = task
            task.add_done_callback(lambda done: self._suggest_pending.pop(key, None))
        # A disconnected page must not cancel a lookup the address bar also awaits.
        return await asyncio.shield(task)

    async def _autocomplete(self, q: str, timeout: float, source: str = "searxng", key: str = "") -> list:
        from archer import websearch
        try:
            if source == "searxng":
                try:
                    resp = await self._client.get("/autocompleter", params={"q": q}, timeout=timeout)
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.HTTPError, ValueError):
                    data = await websearch.suggest(self._client, "duckduckgo", q, timeout)  # SearXNG down or limited
            else:
                data = await websearch.suggest(self._client, source, q, timeout)
        except (httpx.HTTPError, ValueError):
            return [q, []]
        if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
            return [q, []]
        terms = suggestion_terms(data[1], q)
        result: list = [q, []]
        if terms:
            # Keep relevance aligned after filtering; do not forward navigation metadata.
            metadata = data[4] if len(data) > 4 and isinstance(data[4], dict) else {}
            scores = metadata.get("google:suggestrelevance")
            relevance = []
            for rank, term in enumerate(terms):
                index = next(i for i, raw in enumerate(data[1]) if isinstance(raw, str) and raw.strip() == term)
                score = scores[index] if isinstance(scores, list) and index < len(scores) else None
                relevance.append(score if type(score) is int and 0 <= score <= 1000 else 600 - rank)
            result = [q, terms, [], [], {"google:suggestrelevance": relevance}]
        if len(self._suggest_cache) >= _CACHE_MAX:
            self._suggest_cache.pop(next(iter(self._suggest_cache)))
        self._suggest_cache[key or q] = (time.monotonic() + (SUGGEST_TTL if terms else 5), result)
        return result


SearXNG = Search  # the name earlier code and tests import
