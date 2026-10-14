"""Local places from OpenStreetMap: Photon for the area, Overpass for the places.

Both are free community services. Overpass is often slow (10–15 s under load), so the
results page loads places lazily and races two Overpass servers.
"""

import asyncio
import math
import re
from difflib import SequenceMatcher

import httpx

from archer.sources import TTLCache

_area_cache = TTLCache(ttl=7 * 86400)
_places_cache = TTLCache(ttl=3600)
OVERPASS_SERVERS = ("https://overpass-api.de/api/interpreter",
                    "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
SMALL_AREAS = {"suburb", "neighbourhood", "quarter", "residential", "locality", "street", "hamlet", "village"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742000 * math.asin(math.sqrt(a))


def correct_area(area: str, texts: list[str]) -> str | None:
    """Fix a misspelled area using the search results' own wording.

    "indranagar" -> "Indiranagar" when the results (already spell-corrected by the
    upstream engines) keep saying "Indiranagar". Needs 2+ mentions to count.
    """
    n = len(area.split())
    target = _norm(area)
    counts: dict[str, int] = {}
    for text in texts:
        words = re.findall(r"[A-Za-z][A-Za-z'-]+", text)
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            norm = _norm(phrase)
            if norm != target and norm[:1] == target[:1] and SequenceMatcher(None, target, norm).ratio() >= 0.85:
                counts[phrase] = counts.get(phrase, 0) + 1
    if not counts:
        return None
    best, hits = max(counts.items(), key=lambda kv: kv[1])
    return best if hits >= 2 else None


async def area(client: httpx.AsyncClient, name: str, home: tuple[float, float] | None) -> dict | None:
    """Geocode an area; among equally named places, prefer the one nearest home."""
    key = (name.lower(), home)
    if (hit := _area_cache.get(key)) is not None:
        return hit or None
    params = {"q": name, "limit": 8, "lang": "en"}
    if home:
        params.update(lat=home[0], lon=home[1])
    resp = await client.get("https://photon.komoot.io/api/", params=params)
    resp.raise_for_status()
    features = resp.json().get("features") or []
    if not features:
        _area_cache.put(key, {})
        return None
    target = _norm(name.split(",")[0])

    def rank(f: dict) -> tuple:
        props, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
        similar = SequenceMatcher(None, target, _norm(props.get("name", ""))).ratio() >= 0.8
        far = _distance_m(home[0], home[1], lat, lon) if home else 0
        return (not similar, far)

    best = min(features, key=rank)
    props, (lon, lat) = best["properties"], best["geometry"]["coordinates"]
    where = [props.get("name"), props.get("city") or props.get("county"), props.get("state")]
    label = ", ".join(dict.fromkeys(w for w in where if w))
    # Photon's "type" (city/district/locality/street…) says how big the area is; osm_value
    # is often just "administrative".
    small = props.get("type") in ("district", "locality", "street", "house") or props.get("osm_value") in SMALL_AREAS
    out = {"name": props.get("name") or name, "label": label, "lat": lat, "lon": lon, "small": small}
    return _area_cache.put(key, out)


def _address(tags: dict) -> str:
    parts = [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:suburb") or tags.get("addr:city")]
    return ", ".join(p for p in parts if p)


async def _overpass(client: httpx.AsyncClient, query: str) -> dict:
    """Ask every Overpass server at once; the first good answer wins."""
    async def ask(url: str) -> dict:
        resp = await client.post(url, data={"data": query}, timeout=25)
        resp.raise_for_status()
        return resp.json()

    tasks = [asyncio.create_task(ask(url)) for url in OVERPASS_SERVERS]
    try:
        for done in asyncio.as_completed(tasks):
            try:
                return await done
            except (httpx.HTTPError, ValueError):
                continue
        raise httpx.ReadTimeout("all Overpass servers failed")
    finally:
        for t in tasks:
            t.cancel()


async def _photon_pois(client: httpx.AsyncClient, key: str, value: str, lat: float, lon: float,
                       radius: int) -> list[dict]:
    """Fast fallback (~2 s): names, streets and distance only, no hours or websites."""
    resp = await client.get("https://photon.komoot.io/api/", params={
        "q": value.replace("_", " "), "osm_tag": f"{key}:{value}", "lat": lat, "lon": lon,
        "limit": 20, "lang": "en", "location_bias_scale": 0.1, "zoom": 16})
    resp.raise_for_status()
    places, seen = [], set()
    for f in resp.json().get("features", []):
        props, (plon, plat) = f["properties"], f["geometry"]["coordinates"]
        dist = _distance_m(lat, lon, plat, plon)
        ident = (props.get("name"), props.get("street"))
        if not props.get("name") or dist > radius * 1.5 or ident in seen:
            continue
        seen.add(ident)
        osm_type = {"N": "node", "W": "way", "R": "relation"}.get(props.get("osm_type", ""), "node")
        places.append({
            "name": props["name"], "kind": "", "hours": "", "phone": "", "website": "",
            "address": ", ".join(p for p in (props.get("street"), props.get("district") or props.get("locality")) if p),
            "distance": f"{dist / 1000:.1f} km" if dist >= 1000 else f"{dist:.0f} m",
            "osm": f"https://www.openstreetmap.org/{osm_type}/{props.get('osm_id', '')}",
            "score": dist,
        })
    places.sort(key=lambda p: p["score"])
    return places[:8]


async def nearby(client: httpx.AsyncClient, key: str, value: str, lat: float, lon: float,
                 radius: int) -> tuple[list[dict], str]:
    """Returns (places, source). Overpass has the detail; Photon is the fallback when it's overloaded."""
    cache_key = (key, value, round(lat, 3), round(lon, 3), radius)
    if (hit := _places_cache.get(cache_key)) is not None:
        return hit
    query = f'[out:json][timeout:10];nwr["{key}"="{value}"]["name"](around:{radius},{lat},{lon});out center tags 60;'
    try:
        data = await asyncio.wait_for(_overpass(client, query), timeout=12)
    except (httpx.HTTPError, asyncio.TimeoutError):
        found = await _photon_pois(client, key, value, lat, lon, radius)
        # Cache the fallback only briefly so the detailed Overpass version replaces it soon.
        return _places_cache.put(cache_key, (found, "photon"), ttl=300)
    places = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        plat = el.get("lat") or el.get("center", {}).get("lat")
        plon = el.get("lon") or el.get("center", {}).get("lon")
        if plat is None or not tags.get("name"):
            continue
        dist = _distance_m(lat, lon, plat, plon)
        website = tags.get("website") or tags.get("contact:website") or ""
        places.append({
            "name": tags.get("name:en") or tags["name"],
            "kind": (tags.get("cuisine") or tags.get("brand") or "").replace(";", ", ").replace("_", " ").title(),
            "address": _address(tags),
            "hours": tags.get("opening_hours", ""),
            "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "website": website if website.startswith(("http://", "https://")) else "",
            "distance": f"{dist / 1000:.1f} km" if dist >= 1000 else f"{dist:.0f} m",
            "osm": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            # Richer OSM entries (hours, site, cuisine) first, then by distance.
            "score": dist - 400 * sum(bool(tags.get(t)) for t in ("opening_hours", "website", "cuisine", "phone")),
        })
    places.sort(key=lambda p: p["score"])
    return _places_cache.put(cache_key, (places[:8], "overpass"))
