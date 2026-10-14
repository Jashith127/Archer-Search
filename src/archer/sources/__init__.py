"""Keyless public data sources used to build hero modules."""

import time
from typing import Any

import httpx

from archer import __version__
from archer.config import settings

USER_AGENT = f"Archer/{__version__} ({settings.contact}; self-hosted personal search) httpx/{httpx.__version__}"


class TTLCache:
    def __init__(self, ttl: float, size: int = 512):
        self.ttl, self.size = ttl, size
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key):
        hit = self._data.get(key)
        if hit and time.monotonic() < hit[0]:
            return hit[1]
        return None

    def put(self, key, value, ttl: float | None = None):
        """Store value; `ttl` overrides the cache default (e.g. shorter for fallback data)."""
        if len(self._data) >= self.size:
            self._data.pop(next(iter(self._data)))
        self._data[key] = (time.monotonic() + (self.ttl if ttl is None else ttl), value)
        return value
