"""SearXNG client with a small TTL cache.

Upstream engines rate-limit and CAPTCHA quickly, so identical requests (reloads,
back/forward, lazy modules) are served from memory instead of fanning out again.
"""

import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

CACHE_TTL = 600  # seconds
_CACHE_MAX = 256


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

    async def search(self, q: str, category: str = "general", page: int = 1) -> SearchResponse:
        key = (q, category, page)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]

        params = {"q": q, "format": "json", "categories": category, "pageno": page}
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

    async def autocomplete(self, q: str) -> list:
        resp = await self._client.get("/autocompleter", params={"q": q})
        resp.raise_for_status()
        return resp.json()
