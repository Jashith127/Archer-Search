"""US dollar exchange rates for showing API prices in the home currency (keyless, cached a few hours).

Frankfurter serves the European Central Bank's daily reference rates; CoinGecko's exchange-rate
table (already used for crypto) is the fallback when it doesn't answer.
"""

import logging
import time
from dataclasses import dataclass
from datetime import date

import httpx

log = logging.getLogger("archer")

FRANKFURTER = "https://api.frankfurter.app/latest"
COINGECKO = "https://api.coingecko.com/api/v3/exchange_rates"
TTL = 6 * 3600
RETRY = 600
SYMBOLS = {"inr": "₹", "eur": "€", "gbp": "£", "jpy": "¥", "aud": "A$", "cad": "C$", "chf": "CHF ", "sgd": "S$"}


@dataclass
class Rate:
    code: str      # "inr"
    symbol: str    # "₹"
    per_usd: float
    as_of: str     # "13 Oct 2026" or "" when the source gives no date
    source: str


_cache: dict[str, tuple[float, Rate | None]] = {}


async def _frankfurter(client: httpx.AsyncClient, code: str) -> Rate:
    resp = await client.get(FRANKFURTER, params={"from": "USD", "to": code.upper()}, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    day = date.fromisoformat(data["date"])
    return Rate(code, SYMBOLS[code], float(data["rates"][code.upper()]), f"{day.day} {day:%b %Y}", "European Central Bank")


async def _coingecko(client: httpx.AsyncClient, code: str) -> Rate:
    resp = await client.get(COINGECKO, timeout=5)
    resp.raise_for_status()
    rates = resp.json()["rates"]
    return Rate(code, SYMBOLS[code], float(rates[code]["value"]) / float(rates["usd"]["value"]), "", "CoinGecko")


async def usd_rate(client: httpx.AsyncClient, code: str) -> Rate | None:
    """Units of `code` per US dollar, or None (no supported currency, or both sources down)."""
    code = code.lower()
    if code not in SYMBOLS:
        return None
    fetched, rate = _cache.get(code, (0.0, None))
    if time.monotonic() - fetched < (TTL if rate else RETRY):
        return rate
    fresh = None
    for source in (_frankfurter, _coingecko):
        try:
            fresh = await source(client, code)
            if fresh.per_usd > 0:
                break
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            log.warning("fx rate from %s failed: %s", source.__name__.strip("_"), type(exc).__name__)
            fresh = None
    if fresh is None and rate is not None:
        _cache[code] = (time.monotonic() - TTL + RETRY, rate)  # keep the last good rate, retry soon
        return rate
    _cache[code] = (time.monotonic(), fresh)
    return fresh
