"""Small, on-demand LRCLIB lookup for song panels."""

import re
from difflib import SequenceMatcher

import httpx

from archer.sources import TTLCache

_cache = TTLCache(ttl=3600)
_PAREN = re.compile(r"\s*\([^)]*(?:song|single|album)[^)]*\)$", re.I)
_TIMESTAMP = re.compile(r"^\[(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?\]\s*")


def clean_title(title: str) -> str:
    return _PAREN.sub("", title).strip()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _plain(record: dict) -> str:
    plain = record.get("plainLyrics") or ""
    if not plain and record.get("syncedLyrics"):
        plain = "\n".join(_TIMESTAMP.sub("", line) for line in record["syncedLyrics"].splitlines())
    return plain.strip()[:16000]


async def lookup(client: httpx.AsyncClient, title: str, artist: str = "") -> dict:
    title = clean_title(title)[:100]
    artist = artist[:100]
    key = (title.casefold(), artist.casefold())
    if (hit := _cache.get(key)) is not None:
        return hit
    if len(title) < 2:
        return {}
    resp = await client.get("https://lrclib.net/api/search", params={"track_name": title}, timeout=6)
    resp.raise_for_status()
    candidates = resp.json()
    if not isinstance(candidates, list):
        return {}
    title_key, artist_key = _norm(title), _norm(artist)

    def score(record: dict) -> float:
        if not isinstance(record, dict):
            return -1
        track = _norm(record.get("trackName") or record.get("name") or "")
        name = _norm(record.get("artistName") or "")
        title_score = SequenceMatcher(None, title_key, track).ratio()
        artist_score = max((SequenceMatcher(None, part.strip(), name).ratio()
                            for part in artist_key.split(",") if part.strip()), default=0)
        return title_score + (artist_score * .3 if artist_key else 0)

    ranked = sorted(candidates, key=score, reverse=True)
    best = next((r for r in ranked if score(r) >= (.98 if not artist_key else .95) and _plain(r)), None)
    if not best:
        return _cache.put(key, {})
    out = {"title": best.get("trackName") or best.get("name") or title,
           "artist": best.get("artistName") or "", "text": _plain(best),
           "url": f"https://lrclib.net/tracks/{best['id']}" if isinstance(best.get("id"), int) else "https://lrclib.net/"}
    return _cache.put(key, out)
