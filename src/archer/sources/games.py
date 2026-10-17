"""Live details for a video game panel: Steam's store data and CheapShark's PC deals (both keyless).

Steam (store API, cached six hours per app and country): the price in the home country's currency with
any discount, user reviews, players right now, Metacritic, features, screenshots and PC requirements.
CheapShark: the best current price across PC stores and the lowest price ever, in US dollars (its stores
are US storefronts), shown with a home-currency estimate. Console stores have no keyless price API, so
the panel links to them instead (wiki.py builds those links from Wikidata's store IDs).
"""

import asyncio
import html
import re
import unicodedata
from datetime import datetime, timezone

import httpx

from archer.sources import TTLCache, fx

STEAM_STORE = "https://store.steampowered.com"
STEAM_PLAYERS = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
CHEAPSHARK = "https://www.cheapshark.com/api/1.0"

_cache = TTLCache(ttl=6 * 3600, size=256)
_stores = TTLCache(ttl=86400, size=1)
_appids = TTLCache(ttl=86400, size=512)

# Steam's feature categories worth showing, by its id, in this order.
FEATURES = {2: "Single-player", 1: "Multiplayer", 9: "Co-op", 38: "Online co-op", 39: "Local co-op",
            24: "Split screen", 49: "PvP", 28: "Full controller support", 18: "Partial controller support",
            23: "Steam Cloud", 62: "Family Sharing", 22: "Achievements", 30: "Workshop", 20: "MMO", 41: "Remote Play on TV"}
REQUIREMENT_LABELS = ("OS", "Processor", "Memory", "Graphics", "Storage", "DirectX")


def _plain(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def requirements(block: str) -> list[list[str]]:
    """Steam's requirement HTML ("<li><strong>OS:</strong> Windows 10<br></li>…") as [label, value] rows."""
    rows = []
    for label, value in re.findall(r"<strong>\s*([^<:]{2,24}?)\s*:\s*</strong>(.*?)(?=<li>|</ul>|$)", block or "", re.S | re.I):
        label = "OS" if label.strip().upper().startswith("OS") else label.strip().title().replace("Directx", "DirectX")
        value = _plain(value)
        if label in REQUIREMENT_LABELS and value and label not in {r[0] for r in rows}:
            rows.append([label, value[:160]])
    return rows


def _norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", name.replace("&", "and")).strip()


async def find_appid(client: httpx.AsyncClient, title: str, country: str) -> str:
    """Steam's app id for a game Wikidata has no Steam ID for, when its store search has that exact name."""
    key = (_norm(title), country)
    if (hit := _appids.get(key)) is not None:
        return hit
    resp = await client.get(f"{STEAM_STORE}/api/storesearch/", params={"term": title, "cc": country, "l": "english"}, timeout=5)
    resp.raise_for_status()
    items = resp.json().get("items") or []
    found = next((str(i["id"]) for i in items if isinstance(i, dict) and _norm(str(i.get("name", ""))) == key[0]), "")
    return _appids.put(key, found)


def _money(amount: float, symbol: str) -> str:
    return f"{symbol}{amount:,.0f}" if amount >= 100 else f"{symbol}{amount:,.2f}"


async def _steam(client: httpx.AsyncClient, appid: str, country: str) -> dict:
    resp = await client.get(f"{STEAM_STORE}/api/appdetails", params={"appids": appid, "cc": country, "l": "english"}, timeout=6)
    resp.raise_for_status()
    entry = (resp.json() or {}).get(appid) or {}
    return (entry.get("data") or {}) if entry.get("success") else {}


async def _reviews(client: httpx.AsyncClient, appid: str) -> dict:
    resp = await client.get(f"{STEAM_STORE}/appreviews/{appid}", params={"json": 1, "language": "all", "purchase_type": "all",
                                                                       "num_per_page": 0}, timeout=6)
    resp.raise_for_status()
    summary = resp.json().get("query_summary") or {}
    total = summary.get("total_reviews") or 0
    if not total or not summary.get("review_score_desc"):
        return {}
    return {"label": summary["review_score_desc"], "total": total,
            "percent": round(100 * (summary.get("total_positive") or 0) / total)}


async def _players(client: httpx.AsyncClient, appid: str) -> int | None:
    resp = await client.get(STEAM_PLAYERS, params={"appid": appid}, timeout=5)
    resp.raise_for_status()
    count = resp.json().get("response", {}).get("player_count")
    return count if isinstance(count, int) else None


async def _store_names(client: httpx.AsyncClient) -> dict[str, str]:
    if (hit := _stores.get("all")) is not None:
        return hit
    resp = await client.get(f"{CHEAPSHARK}/stores", timeout=5)
    resp.raise_for_status()
    return _stores.put("all", {str(s["storeID"]): s["storeName"] for s in resp.json() if isinstance(s, dict) and s.get("isActive")})


async def _deals(client: httpx.AsyncClient, appid: str, rate: "fx.Rate | None") -> dict:
    resp = await client.get(f"{CHEAPSHARK}/games", params={"steamAppID": appid}, timeout=6)
    resp.raise_for_status()
    found = resp.json()
    if not found:
        return {}
    detail, names = await asyncio.gather(client.get(f"{CHEAPSHARK}/games", params={"id": found[0]["gameID"]}, timeout=6),
                                         _store_names(client))
    detail.raise_for_status()
    data = detail.json()
    home = (lambda usd: _money(usd * rate.per_usd, rate.symbol)) if rate and rate.code != "usd" else (lambda usd: "")
    deals = []
    for d in sorted(data.get("deals") or [], key=lambda d: float(d["price"])):
        price, retail = float(d["price"]), float(d.get("retailPrice") or d["price"])
        deals.append({"store": names.get(str(d["storeID"]), "PC store"), "usd": f"${price:,.2f}", "home": home(price),
                      "retail": f"${retail:,.2f}" if retail > price else "", "cut": round(float(d.get("savings") or 0)),
                      "url": "https://www.cheapshark.com/redirect?dealID=" + d["dealID"]})
    ever = data.get("cheapestPriceEver") or {}
    lowest = None
    if ever.get("price"):
        when = datetime.fromtimestamp(int(ever.get("date") or 0), timezone.utc)
        lowest = {"usd": f"${float(ever['price']):,.2f}", "home": home(float(ever["price"])),
                  "date": f"{when.day} {when:%b %Y}" if ever.get("date") else ""}
    return {"deals": deals[:5], "lowest": lowest, "stores": len(deals)}


async def _quiet(task, default):
    try:
        return await task
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return default


async def details(client: httpx.AsyncClient, appid: str, title: str, country: str, currency: str) -> dict:
    """Everything the game panel loads after the page is up. Each source fails on its own."""
    country = (country or "us").lower()
    if not appid and title:
        appid = await _quiet(find_appid(client, title, country), "")
    if not appid:
        return {}
    key = (appid, country, currency)
    if (hit := _cache.get(key)) is not None:
        return hit
    rate = await _quiet(fx.usd_rate(client, currency), None)
    steam, reviews, players, deals = await asyncio.gather(
        _quiet(_steam(client, appid, country), {}), _quiet(_reviews(client, appid), {}),
        _quiet(_players(client, appid), None), _quiet(_deals(client, appid, rate), {}))
    price = None
    if steam.get("is_free"):
        price = {"free": True, "text": "Free to play"}
    elif (p := steam.get("price_overview")) and p.get("final_formatted"):
        price = {"free": False, "text": p["final_formatted"].replace("₹ ", "₹"),
                 "original": p.get("initial_formatted", "").replace("₹ ", "₹") if p.get("discount_percent") else "",
                 "discount": p.get("discount_percent") or 0}
    release = steam.get("release_date") or {}
    meta = steam.get("metacritic") or {}
    shots = [{"thumb": s["path_thumbnail"], "full": s["path_full"]} for s in steam.get("screenshots") or []
             if isinstance(s, dict) and str(s.get("path_full", "")).startswith("https://")][:10]
    ids = {c.get("id") for c in steam.get("categories") or [] if isinstance(c, dict)}
    out = {
        "appid": appid,
        "url": f"{STEAM_STORE}/app/{appid}/",
        "name": steam.get("name", ""),
        "price": price,
        "coming_soon": bool(release.get("coming_soon")),
        "release": release.get("date", ""),
        "reviews": reviews,
        "players": players,
        "metacritic": {"score": meta["score"], "url": meta.get("url", "")} if isinstance(meta.get("score"), int) else None,
        "features": [label for fid, label in FEATURES.items() if fid in ids][:8],
        "genres": [g["description"] for g in steam.get("genres") or [] if isinstance(g, dict)][:4],
        "systems": [name for key, name in (("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux"))
                    if (steam.get("platforms") or {}).get(key)],
        "header": steam.get("header_image", "") if str(steam.get("header_image", "")).startswith("https://") else "",
        "screenshots": shots,
        "requirements": {"minimum": requirements((steam.get("pc_requirements") or {}).get("minimum", "")),
                         "recommended": requirements((steam.get("pc_requirements") or {}).get("recommended", ""))}
                        if isinstance(steam.get("pc_requirements"), dict) else {},
        "dlc": len(steam.get("dlc") or []),
        "achievements": (steam.get("achievements") or {}).get("total") or 0,
        "deals": deals,
    }
    # A failed Steam call is retried sooner than a full answer.
    return _cache.put(key, out, ttl=None if steam else 600)
