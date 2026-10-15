"""Cast lists and filmographies with pictures, for film/TV and actor panels.

Wikidata names the people and roles (billing order from the item's P161 statements, the
character from its P453/P4633 qualifiers) and English Wikipedia supplies photos and posters,
one batched pageimages request per 50 titles. With TMDB_API_KEY set and the item's TMDB id on
Wikidata, TMDB's credits are used instead: complete casts with profile photos, and
filmographies with posters. Every person and title links to the exact Wikipedia article when
one is known, so the next panel opens on the right "Chris Evans".
"""

import re
from datetime import date
from urllib.parse import unquote

import httpx

from archer.sources import TTLCache, wiki

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/"

_cast_cache = TTLCache(ttl=86400)
_films_cache = TTLCache(ttl=86400)

MOVIE_TYPES = {"Q11424", "Q24869", "Q202866", "Q29168811", "Q18011172", "Q506240", "Q93204"}
TV_TYPES = {"Q5398426", "Q581714", "Q1259759", "Q15416", "Q526877", "Q63952888", "Q117467246"}

# Crew worth naming on a title, in the order shown: Wikidata property -> job.
CREW_PROPS = [("P57", "Director"), ("P170", "Creator"), ("P58", "Writer"), ("P86", "Music"),
              ("P344", "Cinematography"), ("P1040", "Editor"), ("P162", "Producer")]
CREW_ORDER = [job for _, job in CREW_PROPS]
TMDB_JOBS = {"Director": "Director", "Screenplay": "Writer", "Writer": "Writer", "Original Music Composer": "Music",
             "Director of Photography": "Cinematography", "Editor": "Editor", "Producer": "Producer"}
# TMDB genres that are appearances, not roles: talk shows, news, reality.
_NOT_ROLES = {10767, 10763, 10764}
_SELF = re.compile(r"^\s*(self|himself|herself|themselves)\b", re.I)


def _key_params(key: str) -> tuple[dict, dict]:
    # v4 read tokens are JWTs; v3 keys go in the query string.
    return ({"Authorization": f"Bearer {key}"}, {}) if key.startswith("eyJ") else ({}, {"api_key": key})


async def _tmdb(client: httpx.AsyncClient, key: str, path: str, **params) -> dict:
    headers, auth = _key_params(key)
    resp = await client.get(TMDB_API + path, params={**auth, **params}, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _tmdb_img(path: str | None, size: str) -> str:
    return f"{TMDB_IMG}{size}{path}" if isinstance(path, str) and path.startswith("/") else ""


def _article_title(url: str) -> str:
    return unquote(url.rsplit("/wiki/", 1)[-1]).replace("_", " ") if "/wiki/" in url else ""


def _year(text: str) -> str:
    return text[:4] if re.match(r"\d{4}", text or "") else ""


def _upcoming(text: str) -> bool:
    try:
        return date.fromisoformat(text[:10]) > date.today()
    except ValueError:
        return False


async def _sparql(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(wiki.WDQS, params={"query": query, "format": "json"}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("results", {}).get("bindings", [])


async def _wiki_titles(client: httpx.AsyncClient, groups: dict[str, list]) -> dict[tuple[str, str], str]:
    """Wikipedia article titles for TMDB ids, {(property, id): title}, in one SPARQL query.

    Best effort: if Wikidata is slow the caller falls back to plain-name searches.
    """
    parts = []
    for prop, ids in groups.items():
        ids = list(dict.fromkeys(str(i) for i in ids if re.fullmatch(r"\d{1,9}", str(i))))[:250]
        if ids and re.fullmatch(r"P\d+", prop):
            values = " ".join(f'"{i}"' for i in ids)
            parts.append(f'{{ VALUES ?id {{ {values} }} ?item wdt:{prop} ?id . BIND("{prop}" AS ?prop) }}')
    if not parts:
        return {}
    query = (f"SELECT ?prop ?id ?article WHERE {{ {' UNION '.join(parts)} "
             "?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }")
    try:
        rows = await _sparql(client, query)
    except (httpx.HTTPError, ValueError):
        return {}
    return {(r["prop"]["value"], r["id"]["value"]): _article_title(r["article"]["value"]) for r in rows}


async def _entities(client: httpx.AsyncClient, ids: list[str]) -> dict:
    """Labels and English Wikipedia sitelinks for Wikidata items, 50 per request."""
    out: dict = {}
    for i in range(0, len(ids), 50):
        data = await wiki._get(client, wiki.WD_API, action="wbgetentities", ids="|".join(ids[i:i + 50]),
                               props="labels|sitelinks", sitefilter="enwiki", languages="en|mul", format="json")
        out.update(data.get("entities", {}))
    return out


def _value_id(snak: dict) -> str:
    if snak.get("snaktype") != "value":
        return ""
    value = snak.get("datavalue", {}).get("value")
    return value.get("id", "") if isinstance(value, dict) else ""


# ------------------------------------------------------------------ cast

async def cast(client: httpx.AsyncClient, qid: str, media: str, tmdb_id: str, key: str) -> dict:
    """{"cast": [{name, role, photo, q, episodes}], "crew": [{name, job, photo, q}], "source"}."""
    cache_key = (qid, media, tmdb_id if key else "")
    if (hit := _cast_cache.get(cache_key)) is not None:
        return hit
    out = None
    if key and tmdb_id:
        try:
            out = await _tmdb_cast(client, key, media, tmdb_id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            out = None  # a bad key or a TMDB outage still leaves Wikidata's list
    if not out or not out["cast"]:
        out = await _wikidata_cast(client, qid)
    return _cast_cache.put(cache_key, out)


async def _tmdb_cast(client: httpx.AsyncClient, key: str, media: str, tmdb_id: str) -> dict:
    if media == "tv":
        data = await _tmdb(client, key, f"/tv/{tmdb_id}", append_to_response="aggregate_credits")
        credits = data.get("aggregate_credits", {})
    else:
        data = await _tmdb(client, key, f"/movie/{tmdb_id}", append_to_response="credits")
        credits = data.get("credits", {})

    people = []
    for p in sorted(credits.get("cast", []), key=lambda p: p.get("order", 999)):
        if media == "tv":
            roles = sorted(p.get("roles", []), key=lambda r: -(r.get("episode_count") or 0))
            role = " / ".join(dict.fromkeys(r["character"] for r in roles if r.get("character")))
            episodes = p.get("total_episode_count") or 0
        else:
            role, episodes = p.get("character") or "", 0
        people.append({"id": p.get("id"), "name": p.get("name", ""), "role": role[:80], "episodes": episodes,
                       "photo": _tmdb_img(p.get("profile_path"), "w185")})
    people = [p for p in people if p["name"]][:150]

    crew: list[dict] = []
    for c in data.get("created_by", []) if media == "tv" else []:
        crew.append({"id": c.get("id"), "name": c.get("name", ""), "job": "Creator", "photo": _tmdb_img(c.get("profile_path"), "w185")})
    for c in credits.get("crew", []):
        jobs = [j.get("job") for j in c.get("jobs", [])] if media == "tv" else [c.get("job")]
        for job in jobs:
            # A series' directors and producers run to dozens; keep its music and writers.
            if job in TMDB_JOBS and not (media == "tv" and TMDB_JOBS[job] in ("Director", "Producer")):
                crew.append({"id": c.get("id"), "name": c.get("name", ""), "job": TMDB_JOBS[job],
                             "photo": _tmdb_img(c.get("profile_path"), "w185")})
    crew = _tidy_crew(crew)

    titles = await _wiki_titles(client, {"P4985": [p["id"] for p in people + crew]})
    for p in people + crew:
        p["q"] = titles.get(("P4985", str(p.pop("id"))), "") or p["name"]
    return {"cast": people, "crew": crew, "source": "tmdb"}


def _tidy_crew(crew: list[dict]) -> list[dict]:
    """One entry per person (jobs joined: "Director, Writer"), at most two names per job, in CREW_ORDER."""
    by_person: dict[str, dict] = {}
    per_job: dict[str, int] = {}
    for c in sorted(crew, key=lambda c: CREW_ORDER.index(c["job"])):
        if not c["name"] or per_job.get(c["job"], 0) >= 2:
            continue
        if (seen := by_person.get(c["name"])):
            if c["job"] not in seen["job"]:
                seen["job"] += ", " + c["job"]
            continue
        per_job[c["job"]] = per_job.get(c["job"], 0) + 1
        by_person[c["name"]] = dict(c)
    return list(by_person.values())[:8]


async def _wikidata_cast(client: httpx.AsyncClient, qid: str) -> dict:
    claims = (await wiki._get(client, wiki.WD_API, action="wbgetclaims", entity=qid, format="json")).get("claims", {})
    order: list[str] = []
    roles: dict[str, list] = {}
    for st in claims.get("P161", []):
        if st.get("rank") == "deprecated" or not (pid := _value_id(st.get("mainsnak", {}))):
            continue
        if pid not in roles:
            order.append(pid)
            roles[pid] = []
        quals = st.get("qualifiers", {})
        roles[pid] += [_value_id(q) for q in quals.get("P453", []) if _value_id(q)]
        roles[pid] += [("text", q["datavalue"]["value"]) for q in quals.get("P4633", [])
                       if q.get("snaktype") == "value" and isinstance(q.get("datavalue", {}).get("value"), str)]
    order = order[:150]
    crew_ids = []
    for prop, job in CREW_PROPS:
        for st in claims.get(prop, [])[:3]:
            if st.get("rank") != "deprecated" and (pid := _value_id(st.get("mainsnak", {}))):
                crew_ids.append((pid, job))
    char_ids = [r for pid in order for r in roles[pid] if isinstance(r, str)]
    ents = await _entities(client, list(dict.fromkeys(order + char_ids + [p for p, _ in crew_ids])))

    def label(pid: str) -> str:
        return wiki._label(ents.get(pid, {}), "")

    def article(pid: str) -> str:
        return ents.get(pid, {}).get("sitelinks", {}).get("enwiki", {}).get("title", "")

    articles = [article(pid) for pid in order + [p for p, _ in crew_ids]]
    photos = await wiki.thumbs(client, [a for a in articles if a], 240)

    def person(pid: str) -> dict:
        page = article(pid)
        return {"name": label(pid) or page, "photo": photos.get(page, ""), "q": page or label(pid)}

    people = []
    for pid in order:
        names = [r[1] if isinstance(r, tuple) else label(r) for r in roles[pid]]
        entry = person(pid)
        if entry["name"]:
            people.append({**entry, "role": " / ".join(dict.fromkeys(n for n in names if n))[:80], "episodes": 0})
    crew = _tidy_crew([{**person(pid), "job": job} for pid, job in crew_ids])
    return {"cast": people, "crew": crew, "source": "wikidata"}


# ----------------------------------------------------------- filmography

async def filmography(client: httpx.AsyncClient, qid: str, tmdb_id: str, key: str) -> dict:
    """{"items": [{title, year, date, media, role, episodes, poster, q, pop, upcoming}], "source"}."""
    cache_key = (qid, tmdb_id if key else "")
    if (hit := _films_cache.get(cache_key)) is not None:
        return hit
    out = None
    if key and tmdb_id:
        try:
            out = await _tmdb_filmography(client, key, tmdb_id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            out = None
    if not out or not out["items"]:
        out = await _wikidata_filmography(client, qid)
    return _films_cache.put(cache_key, out)


async def _tmdb_filmography(client: httpx.AsyncClient, key: str, tmdb_id: str) -> dict:
    data = await _tmdb(client, key, f"/person/{tmdb_id}/combined_credits")
    merged: dict[tuple, dict] = {}
    for c in data.get("cast", []):
        media = c.get("media_type")
        character = (c.get("character") or "").strip()
        if media not in ("movie", "tv") or set(c.get("genre_ids") or []) & _NOT_ROLES or _SELF.match(character):
            continue
        when = c.get("release_date") if media == "movie" else c.get("first_air_date")
        key_ = (media, c.get("id"))
        if (seen := merged.get(key_)):
            if character and character not in seen["role"]:
                seen["role"] = (seen["role"] + " / " + character).strip(" /")
            seen["episodes"] += c.get("episode_count") or 0
            continue
        merged[key_] = {"id": c.get("id"), "title": c.get("title") or c.get("name") or "", "date": when or "",
                        "year": _year(when or ""), "media": media, "role": character[:80],
                        "episodes": c.get("episode_count") or 0, "poster": _tmdb_img(c.get("poster_path"), "w342"),
                        "pop": c.get("vote_count") or 0, "upcoming": _upcoming(when or "")}
    items = [i for i in merged.values() if i["title"]]
    # Drop one-episode guest spots on long-running shows once there's plenty else to show.
    if len(items) > 30:
        items = [i for i in items if not (i["media"] == "tv" and i["episodes"] == 1 and i["pop"] < 50)]
    items = sorted(items, key=lambda i: i["date"] or "9999", reverse=True)[:200]
    titles = await _wiki_titles(client, {"P4947": [i["id"] for i in items if i["media"] == "movie"],
                                         "P4983": [i["id"] for i in items if i["media"] == "tv"]})
    for i in items:
        prop = "P4947" if i["media"] == "movie" else "P4983"
        i["q"] = titles.get((prop, str(i.pop("id"))), "") or i["title"]
    return {"items": items, "source": "tmdb"}


async def _wikidata_filmography(client: httpx.AsyncClient, qid: str) -> dict:
    types = " ".join(f"wd:{t}" for t in sorted(MOVIE_TYPES | TV_TYPES))
    query = f"""SELECT ?work (SAMPLE(?label) AS ?title) (SAMPLE(?type) AS ?kind) (MIN(?d) AS ?date)
      (SAMPLE(?charLabel) AS ?role) (SAMPLE(?charName) AS ?roleText) (SAMPLE(?article) AS ?page) (MAX(?links) AS ?pop) WHERE {{
      ?work p:P161 ?st . ?st ps:P161 wd:{qid} .
      VALUES ?type {{ {types} }} ?work wdt:P31 ?type .
      OPTIONAL {{ ?work rdfs:label ?label . FILTER(LANG(?label) = "en") }}
      OPTIONAL {{ ?st pq:P453 ?char . ?char rdfs:label ?charLabel . FILTER(LANG(?charLabel) = "en") }}
      OPTIONAL {{ ?st pq:P4633 ?charName . }}
      OPTIONAL {{ ?work wdt:P577 ?d . }}
      OPTIONAL {{ ?work wdt:P580 ?d . }}
      OPTIONAL {{ ?work wikibase:sitelinks ?links . }}
      OPTIONAL {{ ?article schema:about ?work ; schema:isPartOf <https://en.wikipedia.org/> . }}
    }} GROUP BY ?work LIMIT 250"""
    items = []
    for row in await _sparql(client, query):
        page = _article_title(row.get("page", {}).get("value", ""))
        title = row.get("title", {}).get("value") or re.sub(r"\s*\([^)]*\)$", "", page)
        if not title:
            continue
        when = row.get("date", {}).get("value", "")
        kind = row.get("kind", {}).get("value", "").rsplit("/", 1)[-1]
        role = row.get("role", {}).get("value") or row.get("roleText", {}).get("value") or ""
        pop = row.get("pop", {}).get("value", "0")
        items.append({"title": title, "date": when[:10], "year": _year(when), "media": "tv" if kind in TV_TYPES else "movie",
                      "role": role[:80], "episodes": 0, "page": page, "pop": int(pop) if pop.isdigit() else 0,
                      "upcoming": _upcoming(when)})
    items = sorted(items, key=lambda i: i["date"] or "9999", reverse=True)[:200]
    posters = await wiki.thumbs(client, [i["page"] for i in items if i["page"]], 342, nonfree=True)
    for i in items:
        page = i.pop("page")
        i["poster"], i["q"] = posters.get(page, ""), page or i["title"]
    return {"items": items, "source": "wikidata"}
