"""Translation via MyMemory (free, no key; anonymous use is capped per day)."""

import html

import httpx

from archer.query import LANGUAGES
from archer.sources import TTLCache

_cache = TTLCache(ttl=7 * 86400)
NAMES = {code.split("-")[0]: name.title() for name, code in LANGUAGES.items()}


async def translate(client: httpx.AsyncClient, text: str, target: str) -> dict | None:
    key = (text.lower(), target)
    if (hit := _cache.get(key)) is not None:
        return hit or None
    resp = await client.get("https://api.mymemory.translated.net/get",
                            params={"q": text, "langpair": f"autodetect|{target}"})
    resp.raise_for_status()
    data = resp.json()
    result = data.get("responseData") or {}
    translated = html.unescape(result.get("translatedText") or "").strip()
    # MyMemory reports quota/usage problems inside a 200 response.
    if not translated or data.get("responseStatus") not in (200, "200") or "MYMEMORY WARNING" in translated:
        return None
    source = (result.get("detectedLanguage") or "").split("-")[0]
    alternatives = []
    for match in data.get("matches") or []:
        alt = html.unescape(match.get("translation") or "").strip()
        if alt and alt != translated and alt not in alternatives:
            alternatives.append(alt)
    out = {"text": text, "translated": translated, "source": source,
           "source_name": NAMES.get(source, source.upper() or "Detected"),
           "target": target, "alternatives": alternatives[:3]}
    return _cache.put(key, out)
