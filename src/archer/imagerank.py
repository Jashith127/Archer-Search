"""Relevance re-ranking for image results.

SearXNG orders images by how many engines returned them and where, which says nothing
about whether the picture matches the query. We score each result by how many of the
query's terms its title and URLs contain (model numbers like "255F" count most), demote
non-matches, and drop duplicates, tiny images and icon files.
"""

import re

from archer.searxng import Result

_STOP = {"the", "a", "an", "of", "and", "for", "in", "on", "with", "to", "photo", "photos", "image",
         "images", "picture", "pictures", "pic", "pics", "inch", "inches"}
_ICONISH = re.compile(r"\b(?:logos?|icons?|svg|vectors?|clipart)\b")
_SIZE = re.compile(r"(\d+)\D+(\d+)")
_TOKEN = re.compile(r"[a-z0-9]+")
_COMPACT = re.compile(r"[^a-z0-9]")
MIN_SIDE = 200


def _tokens(text: str) -> list[str]:
    seen: list[str] = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOP or (len(tok) < 2 and not tok.isalpha()) or tok in seen:
            continue
        seen.append(tok)
    return seen


def _weight(tok: str) -> int:
    """Model numbers (letters+digits, 3+ chars) pin down the exact product; plain words matter less."""
    return 3 if len(tok) >= 3 and any(c.isdigit() for c in tok) else 1


def _too_small(res: str) -> bool:
    m = _SIZE.search(res or "")
    return bool(m) and min(int(m[1]), int(m[2])) < MIN_SIDE


def score(tokens: list[str], r: Result) -> float:
    """0..1: weighted share of query tokens found in the title (full credit) or URLs (partial)."""
    if not tokens:
        return 0.0
    title = r.title.lower()
    title_toks = set(_TOKEN.findall(title))
    title_compact = _COMPACT.sub("", title)
    url_compact = _COMPACT.sub("", f"{r.img_src} {r.url}".lower())
    got = total = 0.0
    for tok in tokens:
        w = _weight(tok)
        total += w
        if tok in title_toks or (len(tok) >= 3 and tok in title_compact):
            got += w
        elif len(tok) >= 3 and tok in url_compact:
            got += w * 0.6
    return got / total


def rank_images(q: str, results: list[Result]) -> list[Result]:
    tokens = _tokens(q)
    wants_icons = bool(_ICONISH.search(q.lower()))
    kept: list[tuple[float, Result]] = []
    seen: set[str] = set()
    for i, r in enumerate(results):
        src = r.img_src or r.thumbnail
        if not src or src in seen or _too_small(r.resolution):
            continue
        if not wants_icons and src.lower().split("?")[0].endswith(".svg"):
            continue
        seen.add(src)
        bonus = min(len(r.engines) - 1, 2) * 0.5  # several engines agreeing is a mild signal
        kept.append((score(tokens, r) * 10 + bonus - i * 0.04, r))
    kept.sort(key=lambda pair: pair[0], reverse=True)
    return [r for _, r in kept]


_SPLIT = re.compile(r"\s+[|\-–—]\s+")


def similar_query(title: str, fallback: str) -> str:
    """Search terms for 'more like this': the image's own title minus site boilerplate."""
    parts = [p for p in _SPLIT.split(title.strip()) if p]
    head = parts[0] if parts and len(parts[0].split()) >= 3 else title
    words = re.sub(r"[^\w\s.\-]", " ", head).split()[:9]
    cleaned = " ".join(words)
    return cleaned if len(_tokens(cleaned)) >= 2 else fallback
