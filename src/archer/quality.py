"""Cheap result-quality signals for reranking: no network, no model, microseconds per page.

`signals(q, results)` returns one adjustment per result, added to the personalization score.
  * engine agreement: a result several engines returned independently is likelier to be good
  * duplicates: the same page under another URL (www, trailing slash, AMP, tracking query) is pushed down
  * freshness: only when the query asks for recent things ("latest", "news", "today", this year)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

# Scale: the provider's rank is worth 0.6/(1+0.25·i), so neighbouring places differ by 0.12, 0.08, 0.06, …
# SearXNG already orders by how many engines agreed, so agreement is only a nudge (it would double count).
# Freshness can lift a new page over its neighbour but never over a raised site (±0.35) or a strong match.
W_AGREE = 0.015      # per extra engine, up to AGREE_MAX
AGREE_MAX = 0.03
DUP_PENALTY = -0.10  # about two places down from where the duplicate sat
W_FRESH = 0.10

# Words that mean "recent" on their own; "new", "live", "results" and "update" were left out because
# they appear in names and how-tos ("new york", "live stream", "update windows").
_FRESH_WORDS = re.compile(r"\b(latest|news|today|tonight|yesterday|this (?:week|month|year)|breaking|"
                          r"release date|score|standings|price)\b", re.I)
_AMP = re.compile(r"(/amp/?$|^amp\.|\.amp\.html$)")


def wants_fresh(q: str) -> bool:
    return bool(_FRESH_WORDS.search(q)) or str(datetime.now().year) in q


def canonical(url: str) -> str:
    """A page's identity with the noise removed: host without www/amp, path without a trailing slash or amp suffix."""
    p = urlsplit(url)
    host = _AMP.sub("", p.netloc.lower()).removeprefix("www.")
    path = _AMP.sub("", p.path.rstrip("/").lower())
    return f"{host}{path}"


def _age_days(date: str, now: datetime) -> float | None:
    if not date:
        return None
    try:
        d = datetime.fromisoformat(date.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return max(0.0, (now - d).total_seconds() / 86400)


def signals(q: str, results: list, *, now: datetime | None = None) -> list[float]:
    now = now or datetime.now(timezone.utc)
    fresh = wants_fresh(q)
    seen: set[str] = set()
    out = []
    for r in results:
        adj = min(AGREE_MAX, W_AGREE * max(0, len(r.engines) - 1))
        key = canonical(r.url)
        if key in seen:
            adj += DUP_PENALTY
        seen.add(key)
        if fresh:
            age = _age_days(r.date, now)
            if age is not None:
                adj += W_FRESH * (1 / (1 + age / 30))  # a month old earns half, a year old almost nothing
        out.append(adj)
    return out
