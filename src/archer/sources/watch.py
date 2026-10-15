"""Where to watch: legal streaming availability for films and shows.

Country-specific data comes from TMDB's watch-provider endpoint (data by JustWatch; free API
key). Without a key, Archer falls back to the streaming-service IDs Wikidata publishes,
which name the service but not whether the title is available in your country.
"""

from urllib.parse import quote_plus

import httpx

from archer.sources import TTLCache

_cache = TTLCache(ttl=12 * 3600)
LOGO_BASE = "https://image.tmdb.org/t/p/w154"

# TMDB category -> (label shown, order). Free options lead.
CATEGORIES = [("free", "Free"), ("ads", "Free with ads"), ("flatrate", "Stream"), ("rent", "Rent"), ("buy", "Buy")]

COUNTRY_NAMES = {
    "IN": "India", "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia",
    "DE": "Germany", "FR": "France", "ES": "Spain", "IT": "Italy", "JP": "Japan", "KR": "South Korea",
    "BR": "Brazil", "MX": "Mexico", "SG": "Singapore", "AE": "United Arab Emirates", "NL": "Netherlands",
    "SE": "Sweden", "NZ": "New Zealand", "IE": "Ireland", "ZA": "South Africa",
}

# Wikidata streaming IDs -> (service, URL template, kinds it applies to).
WIKIDATA_SERVICES = {
    "P1874": ("Netflix", "https://www.netflix.com/title/{}", ("film", "tv")),
    "P8055": ("Prime Video", "https://www.primevideo.com/detail/{}", ("film", "tv")),
    "P7595": ("Disney+", "https://www.disneyplus.com/movies/-/{}", ("film",)),
    "P7596": ("Disney+", "https://www.disneyplus.com/series/-/{}", ("tv",)),
    "P6466": ("Hulu", "https://www.hulu.com/movie/{}", ("film",)),
    "P9586": ("Apple TV", "https://tv.apple.com/movie/{}", ("film",)),
    "P9751": ("Apple TV", "https://tv.apple.com/show/{}", ("tv",)),
}

# How the no-key fallback draws each service: a Simple Icons mark where the brand allows one
# (templates/brand_icons.html), otherwise its initial on the brand colour. Prime Video, Disney+
# and Hulu asked Simple Icons to remove their logos, so Archer doesn't ship them either.
SERVICE_BRANDS = {
    "Netflix": ("netflix", "#e50914"), "Apple TV": ("appletv", "#2b2b2b"),
    "Prime Video": ("", "#1a98ff"), "Disney+": ("", "#113ccf"), "Hulu": ("", "#1ce783"),
}


def country_name(code: str) -> str:
    return COUNTRY_NAMES.get(code.upper(), code.upper())


def justwatch_search(country: str, title: str) -> str:
    return f"https://www.justwatch.com/{country.lower()}/search?q={quote_plus(title)}"


async def tmdb_providers(client: httpx.AsyncClient, key: str, media: str, tmdb_id: str, country: str, *, fresh: bool = False) -> dict:
    """Providers for one title in one country, grouped by how you can watch it."""
    cache_key = (media, tmdb_id, country)
    if not fresh and (hit := _cache.get(cache_key)) is not None:
        return hit
    url = f"https://api.themoviedb.org/3/{'tv' if media == 'tv' else 'movie'}/{tmdb_id}/watch/providers"
    # v4 read tokens are JWTs; v3 keys go in the query string.
    headers, params = ({"Authorization": f"Bearer {key}"}, {}) if key.startswith("eyJ") else ({}, {"api_key": key})
    resp = await client.get(url, params=params, headers=headers, timeout=8)
    resp.raise_for_status()
    region = resp.json().get("results", {}).get(country.upper(), {})
    groups = []
    for cat, label in CATEGORIES:
        providers = [{"name": p["provider_name"],
                      "logo": LOGO_BASE + p["logo_path"] if p.get("logo_path") else ""}
                     for p in sorted(region.get(cat, []), key=lambda p: p.get("display_priority", 99))]
        if providers:
            groups.append({"category": cat, "label": label, "free": cat in ("free", "ads"), "providers": providers})
    out = {"groups": groups, "link": region.get("link", "")}
    return _cache.put(cache_key, out)
