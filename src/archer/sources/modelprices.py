"""API prices for AI models, from OpenRouter's public model list (keyless, refreshed daily).

One ~1 MB JSON lists every model OpenRouter serves with its per-token prices. It is cached in
DATA_DIR like the Epoch zip, so pricing a model card costs no round trip once it is on disk.
Charts use one number per model, the 3:1 input:output blend per million tokens; the model's own
card shows input, output and cached-input prices separately.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from archer.config import DATA_DIR

log = logging.getLogger("archer")

URL = "https://openrouter.ai/api/v1/models"
SOURCE_URL = "https://openrouter.ai/models"
CACHE_FILE = DATA_DIR / "openrouter_models.json"
MAX_AGE = 24 * 3600
RETRY = 600

_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
# Words a listing adds to a model's name without making it a different model.
_NOISE = {"preview", "exp", "experimental", "latest", "beta", "free", "thinking", "online", "nitro"}


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""
    input: float
    output: float
    cached: float | None = None  # reading a cached prompt, where the provider lists it

    @property
    def blended(self) -> float:
        return round((3 * self.input + self.output) / 4, 4)


_table: dict[tuple, Price] | None = None
_loaded_at = 0.0
_failed_at = 0.0
_refresh: asyncio.Task | None = None


def key(name: str) -> tuple:
    """Order-free tokens of a model name, minus listing noise and date stamps ("-0528", "2025-11-20")."""
    base = re.sub(r"\s*\([^)]*\)", "", name.split(": ", 1)[-1]).lower().replace("_", " ")
    base = re.sub(r"\b\d{4}(?:-\d{2}-\d{2})?\b", " ", base)
    return tuple(sorted(t for t in _TOKEN.findall(base) if t not in _NOISE))


def _parse(blob: bytes) -> dict[tuple, Price]:
    table: dict[tuple, Price] = {}
    for m in json.loads(blob)["data"]:
        if m.get("id", "").endswith(":free"):
            continue
        try:
            pricing = m["pricing"]
            cached = float(pricing.get("input_cache_read") or 0) * 1e6
            price = Price(round(float(pricing["prompt"]) * 1e6, 4), round(float(pricing["completion"]) * 1e6, 4),
                          round(cached, 4) or None)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if price.input <= 0 and price.output <= 0:
            continue
        # The listing is newest first, so the current price of a name wins over older snapshots.
        for k in {key(m.get("name") or ""), key(m["id"].split("/")[-1])}:
            if k:
                table.setdefault(k, price)
    return table


async def _refresh_now(client: httpx.AsyncClient) -> None:
    global _table, _loaded_at, _failed_at
    try:
        resp = await client.get(URL, timeout=20)
        resp.raise_for_status()
        table = _parse(resp.content)  # validate before replacing the cached copy
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(CACHE_FILE)
        _table, _loaded_at = table, time.time()
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("openrouter price refresh failed: %s", type(exc).__name__)
        _failed_at = time.monotonic()
        if _table is not None:
            _loaded_at = time.time() - MAX_AGE + 3600  # retry in an hour, keep the old prices


async def ensure(client: httpx.AsyncClient, wait: float = 3) -> dict[tuple, Price]:
    """Price table keyed by `key(name)`. Never raises; empty means no prices (charts fall back to dates)."""
    global _table, _loaded_at, _refresh
    if _table is None and CACHE_FILE.exists():
        try:
            _table, _loaded_at = _parse(CACHE_FILE.read_bytes()), CACHE_FILE.stat().st_mtime
        except (OSError, ValueError, KeyError, TypeError):
            _table = None
    stale = _table is None or time.time() - _loaded_at > MAX_AGE
    if stale and not (_failed_at and time.monotonic() - _failed_at < RETRY) and (_refresh is None or _refresh.done()):
        _refresh = asyncio.create_task(_refresh_now(client))
    if _table is None and _refresh and not _refresh.done():
        try:  # first run: wait a little, but let the download finish in the background regardless
            await asyncio.wait_for(asyncio.shield(_refresh), wait)
        except asyncio.TimeoutError:
            pass
    return _table or {}


def lookup(table: dict[tuple, Price], name: str) -> Price | None:
    return table.get(key(name)) if table else None


def cached_price(model: str) -> Price | None:
    """A model's price from the cached list, without network. "claude-haiku-5-5" and
    "anthropic/claude-haiku-5.5" name the same model, so dashed versions are tried dotted too."""
    global _table, _loaded_at
    if _table is None and CACHE_FILE.exists():
        try:
            _table, _loaded_at = _parse(CACHE_FILE.read_bytes()), CACHE_FILE.stat().st_mtime
        except (OSError, ValueError, KeyError, TypeError):
            return None
    name = model.split("/")[-1]
    for variant in (name, re.sub(r"(\d)-(\d)(?!\d)", r"\1.\2", name)):
        if price := lookup(_table or {}, variant):
            return price
    return None
