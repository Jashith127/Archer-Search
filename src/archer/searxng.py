"""SearXNG client with a small TTL cache.

Upstream engines rate-limit and CAPTCHA quickly, so identical requests (reloads,
back/forward, lazy modules) are served from memory instead of fanning out again.
"""

import asyncio
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


class SearXNG:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._cache: dict[tuple, tuple[float, SearchResponse]] = {}
        self._search_pending: dict[tuple, asyncio.Task] = {}
        self._suggest_cache: dict[str, tuple[float, list]] = {}
        self._suggest_pending: dict[str, asyncio.Task] = {}

    async def search(self, q: str, category: str = "general", page: int = 1) -> SearchResponse:
        # Search preferences are part of the key, so changing them never serves stale results.
        key = (q, category, page, settings.safesearch, settings.search_language)
        if key not in self._search_pending:
            task = asyncio.create_task(self._search(q, category, page))
            self._search_pending[key] = task
            task.add_done_callback(lambda done: self._search_pending.pop(key, None))
        return await asyncio.shield(self._search_pending[key])

    async def _search(self, q: str, category: str = "general", page: int = 1) -> SearchResponse:
        key = (q, category, page, settings.safesearch, settings.search_language)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]

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
            results=[r for r in map(_to_result, data.get("results", [])) if r],
            answers=data.get("answers", []),
            infoboxes=data.get("infoboxes", []),
            suggestions=data.get("suggestions", []),
            corrections=data.get("corrections", []),
            unresponsive=[tuple(u) for u in data.get("unresponsive_engines", [])],
        )
        if out.results or out.answers:  # don't pin an empty, rate-limited response
            if len(self._cache) >= _CACHE_MAX:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.monotonic(), out)
        return out

    async def autocomplete(self, q: str, timeout: float = 1.5) -> list:
        if not suggestion_eligible(q):
            return [q, []]
        hit = self._suggest_cache.get(q)
        if hit and time.monotonic() < hit[0]:
            return hit[1]
        task = self._suggest_pending.get(q)
        if task is None:
            task = asyncio.create_task(self._autocomplete(q, timeout))
            self._suggest_pending[q] = task
            task.add_done_callback(lambda done: self._suggest_pending.pop(q, None))
        # A disconnected page must not cancel a lookup the address bar also awaits.
        return await asyncio.shield(task)

    async def _autocomplete(self, q: str, timeout: float) -> list:
        try:
            resp = await self._client.get("/autocompleter", params={"q": q}, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
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
        self._suggest_cache[q] = (time.monotonic() + (SUGGEST_TTL if terms else 5), result)
        return result
