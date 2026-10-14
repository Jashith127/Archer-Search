"""Definitions from Wiktionary's REST API; pronunciation from dictionaryapi.dev when it answers."""

import asyncio
import html
import re
from urllib.parse import quote

import httpx

from archer.sources import TTLCache

_cache = TTLCache(ttl=7 * 86400)
_TAGS = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", fragment or "")).split())


async def _wiktionary(client: httpx.AsyncClient, word: str) -> list[dict]:
    resp = await client.get(f"https://en.wiktionary.org/api/rest_v1/page/definition/{quote(word)}")
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    meanings = []
    for entry in resp.json().get("en", []):
        defs = []
        for d in entry.get("definitions", []):
            text = _text(d.get("definition", ""))
            if not text:
                continue
            examples = d.get("parsedExamples") or [{"example": e} for e in d.get("examples", [])]
            example = _text(examples[0].get("example", "")) if examples else ""
            defs.append({"text": text, "example": example})
            if len(defs) == 3:
                break
        if defs:
            meanings.append({"pos": entry.get("partOfSpeech", "").lower(), "defs": defs, "synonyms": []})
    return meanings[:4]


async def _pronunciation(client: httpx.AsyncClient, word: str) -> tuple[str, str]:
    resp = await client.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(word)}", timeout=2.5)
    if resp.status_code != 200:
        return "", ""
    first = resp.json()[0]
    phonetic = first.get("phonetic") or next((p.get("text") for p in first.get("phonetics", []) if p.get("text")), "")
    audio = next((p["audio"] for p in first.get("phonetics", []) if p.get("audio", "").startswith("https://")), "")
    return phonetic, audio


async def define(client: httpx.AsyncClient, word: str) -> dict | None:
    key = word.lower().strip()
    if (hit := _cache.get(key)) is not None:
        return hit or None
    meanings, pron = await asyncio.gather(_wiktionary(client, key), _pronunciation(client, key), return_exceptions=True)
    if isinstance(meanings, BaseException):
        raise meanings
    if not meanings:
        _cache.put(key, {})
        return None
    phonetic, audio = pron if isinstance(pron, tuple) else ("", "")
    out = {"word": key, "phonetic": phonetic, "audio": audio, "meanings": meanings,
           "source": f"https://en.wiktionary.org/wiki/{quote(key)}"}
    return _cache.put(key, out)
