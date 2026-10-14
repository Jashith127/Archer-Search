"""Crypto prices from CoinGecko's public API (free, no key, rate-limited per IP)."""

import httpx

from archer.sources import TTLCache

_cache = TTLCache(ttl=120)
SYMBOLS = {"inr": "₹", "usd": "$", "eur": "€", "gbp": "£", "jpy": "¥", "aud": "A$", "cad": "C$",
           "sgd": "S$", "aed": "AED ", "chf": "CHF ", "cny": "¥"}


def _money(value: float | None, sym: str) -> str:
    if value is None:
        return "—"
    tiers = ((1e12, "L Cr"), (1e7, "Cr")) if sym == "₹" else ((1e12, "T"), (1e9, "B"), (1e6, "M"))
    for size, word in tiers:
        if abs(value) >= size:
            return f"{sym}{value / size:,.2f} {word}"
    decimals = 2 if abs(value) >= 1 else 6
    if sym == "₹":
        return f"{sym}{_indian_grouping(value, decimals)}"
    return f"{sym}{value:,.{decimals}f}"


def _indian_grouping(value: float, decimals: int) -> str:
    """79,07,309.00: last three digits, then pairs (lakh/crore grouping)."""
    whole, _, frac = f"{abs(value):.{decimals}f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    grouped = ",".join(([head] if head else []) + pairs + [tail])
    return ("-" if value < 0 else "") + grouped + (f".{frac}" if frac else "")


def _sparkline(prices: list[float], w: int = 640, h: int = 160) -> dict:
    if len(prices) < 2:
        return {"path": "", "area": ""}
    lo, hi = min(prices), max(prices)
    span = hi - lo or 1
    pts = [(i / (len(prices) - 1) * w, h - (p - lo) / span * (h - 12) - 6) for i, p in enumerate(prices)]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return {"path": path, "area": f"{path} L{w},{h} L0,{h} Z", "w": w, "h": h}


async def price(client: httpx.AsyncClient, coin: str, fiat: str) -> dict | None:
    key = (coin, fiat)
    if hit := _cache.get(key):
        return hit
    resp = await client.get("https://api.coingecko.com/api/v3/coins/markets", params={
        "vs_currency": fiat, "ids": coin, "sparkline": "true", "price_change_percentage": "24h,7d"})
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    c = rows[0]
    sym = SYMBOLS.get(fiat, fiat.upper() + " ")
    change = c.get("price_change_percentage_24h") or 0.0
    week = c.get("price_change_percentage_7d_in_currency")
    prices = (c.get("sparkline_in_7d") or {}).get("price") or []
    out = {
        "name": c["name"], "symbol": c["symbol"].upper(), "image": c.get("image", ""), "fiat": fiat.upper(),
        "sym": sym, "price": c["current_price"], "price_text": _money(c["current_price"], sym),
        "change": round(change, 2), "week": round(week, 2) if week is not None else None,
        "rank": c.get("market_cap_rank"),
        "stats": [
            ("Market cap", _money(c.get("market_cap"), sym)),
            ("24h volume", _money(c.get("total_volume"), sym)),
            ("24h high", _money(c.get("high_24h"), sym)),
            ("24h low", _money(c.get("low_24h"), sym)),
            ("All-time high", _money(c.get("ath"), sym) + (f" · {c['ath_date'][:10]}" if c.get("ath_date") else "")),
            ("Circulating supply", f"{c['circulating_supply']:,.0f} {c['symbol'].upper()}" if c.get("circulating_supply") else "—"),
        ],
        "spark": _sparkline(prices),
        "updated": (c.get("last_updated") or "")[11:16],
    }
    return _cache.put(key, out)
