"""GitHub repository pages as evidence: the API's repo facts plus the README, with no model call.

A repo page is mostly JavaScript chrome, so reading its HTML yields little; the public API (60
unauthenticated requests an hour, one cache entry per repo) gives the description, language, topics
and the README text itself.
"""
import re
from urllib.parse import urlsplit

import httpx

RESERVED = {"settings", "marketplace", "topics", "orgs", "features", "pricing", "sponsors", "about", "login", "explore", "search"}
_cache: dict[str, dict] = {}


def repo_of(url: str) -> tuple[str, str] | None:
    """(owner, repo) for github.com/owner/repo and its sub-pages, else None."""
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    if (p.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    parts = [s for s in p.path.split("/") if s]
    if len(parts) < 2 or parts[0].lower() in RESERVED:
        return None
    owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
    return (owner, repo) if re.fullmatch(r"[\w.-]{1,100}", owner) and re.fullmatch(r"[\w.-]{1,100}", repo) else None


async def repo(client: httpx.AsyncClient, owner: str, name: str) -> dict:
    key = f"{owner}/{name}".lower()
    if key in _cache:
        return _cache[key]
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    base = f"https://api.github.com/repos/{owner}/{name}"
    meta = await client.get(base, headers=headers)
    meta.raise_for_status()
    info = meta.json()
    readme = ""
    try:
        resp = await client.get(base + "/readme", headers={**headers, "Accept": "application/vnd.github.raw+json"})
        if resp.status_code == 200:
            readme = resp.text[:24000]
    except httpx.HTTPError:
        pass  # the repo facts alone still answer "what is this"
    data = {
        "name": info.get("full_name"), "description": info.get("description"), "language": info.get("language"),
        "topics": info.get("topics") or [], "stars": info.get("stargazers_count"), "forks": info.get("forks_count"),
        "license": (info.get("license") or {}).get("spdx_id"), "homepage": info.get("homepage"),
        "updated": info.get("pushed_at"), "archived": info.get("archived"), "url": info.get("html_url"),
        "readme": readme,
    }
    if len(_cache) >= 64:
        _cache.pop(next(iter(_cache)))
    _cache[key] = data
    return data
