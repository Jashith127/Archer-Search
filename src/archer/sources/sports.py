"""Team sports for the club and league panels: ESPN's public site API, keyless.

Wikidata says what a club *is* (crest, ground, founded, league); this says how it is *doing*: where it
sits in the table and who is around it, the last results with their scorelines, the next fixtures, the
season in totals and the squad by position. Football gets the most (zones in the table, draws, a form
guide, the squad on a pitch); basketball, American football, baseball and ice hockey get the same panel
in their own terms (a win-loss record, a conference table, the next games).

A club is matched through its league: Wikidata's league (P118) names an ESPN league in `LEAGUES`, and the
club is found in that league's team list by name. A club whose league isn't listed is looked for in the
listed leagues of its own country. ESPN refuses a User-Agent that carries a URL, so this sends its own.
Anything that fails returns {} and the panel keeps its Wikidata facts.
"""

import asyncio
import re
import unicodedata
import httpx

from archer.sources import TTLCache

SITE = "https://site.api.espn.com/apis/site/v2/sports"
STANDINGS = "https://site.api.espn.com/apis/v2/sports"
HEADERS = {"User-Agent": "Archer (self-hosted personal search)", "Accept": "application/json"}
_cache = TTLCache(ttl=600, size=256)  # ESPN responses by url; the team lists are kept a day
DAY = 86400

# Wikidata's sport (P641) -> ESPN's sport path, and what the panel calls a game in it.
SPORTS = {"Q2736": "soccer", "Q5372": "basketball", "Q41323": "football", "Q5369": "baseball", "Q41466": "hockey"}
SPORT_NAMES = {"soccer": "Football", "basketball": "Basketball", "football": "American football",
               "baseball": "Baseball", "hockey": "Ice hockey"}

# Wikidata league -> (sport, ESPN slug, short name, country QID). Country lets a club with no league on
# Wikidata be looked for in its own country's leagues.
LEAGUES = {
    "Q9448": ("soccer", "eng.1", "Premier League", "Q145"),
    "Q19510": ("soccer", "eng.2", "Championship", "Q145"),
    "Q324867": ("soccer", "esp.1", "La Liga", "Q29"),
    "Q82595": ("soccer", "ger.1", "Bundesliga", "Q183"),
    "Q15804": ("soccer", "ita.1", "Serie A", "Q38"),
    "Q13394": ("soccer", "fra.1", "Ligue 1", "Q142"),
    "Q167541": ("soccer", "ned.1", "Eredivisie", "Q55"),
    "Q182994": ("soccer", "por.1", "Primeira Liga", "Q45"),
    "Q14377162": ("soccer", "sco.1", "Scottish Premiership", "Q22"),
    "Q216022": ("soccer", "bel.1", "Belgian Pro League", "Q31"),
    "Q485568": ("soccer", "tur.1", "Süper Lig", "Q43"),
    "Q18543": ("soccer", "usa.1", "MLS", "Q30"),
    "Q1101443": ("soccer", "usa.nwsl", "NWSL", "Q30"),
    "Q764690": ("soccer", "mex.1", "Liga MX", "Q96"),
    "Q206813": ("soccer", "bra.1", "Brasileirão", "Q155"),
    "Q255633": ("soccer", "ksa.1", "Saudi Pro League", "Q851"),
    "Q16056350": ("soccer", "ind.1", "Indian Super League", "Q668"),
    "Q18756": ("soccer", "uefa.champions", "Champions League", ""),
    "Q18760": ("soccer", "uefa.europa", "Europa League", ""),
    "Q155223": ("basketball", "nba", "NBA", "Q30"),
    "Q2593221": ("basketball", "wnba", "WNBA", "Q30"),
    "Q1215884": ("football", "nfl", "NFL", "Q30"),
    "Q1163715": ("baseball", "mlb", "MLB", "Q30"),
    "Q1215892": ("hockey", "nhl", "NHL", "Q30"),
}
_BY_SLUG = {slug: (sport, short, qid) for qid, (sport, slug, short, _) in LEAGUES.items()}

# Words a club's article carries that ESPN's name doesn't: "Arsenal F.C." is Arsenal.
_CLUB_WORDS = {"fc", "afc", "cf", "sc", "ac", "as", "ssc", "sv", "vfl", "vfb", "fk", "sk", "cd", "ud", "rcd", "club",
               "de", "del", "futbol", "football", "calcio", "f", "c", "a", "s", "the", "and", "bk", "if", "ff", "tsg",
               "rc", "osc", "ogc", "us", "cfc", "sad", "1"}
# Names a club goes by that ESPN spells differently, as folded words.
_ALIASES = {"internazionale": ("inter", "milan"), "bayern munich": ("bayern", "munchen"),
            "psg": ("paris", "saint", "germain"), "wolverhampton wanderers": ("wolves",),
            "athletic club": ("athletic", "bilbao"), "rb leipzig": ("leipzig",), "bayer leverkusen": ("leverkusen",)}


def _fold(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    plain = re.sub(r"\s*\(.*?\)", "", plain).replace("&", " ")
    return re.findall(r"[a-z0-9]+", plain.casefold())


def _core(text: str) -> frozenset[str]:
    """The words that name a club, without "F.C.", years or numbers: {"bayern", "munich"}."""
    return frozenset(w for w in _fold(text) if w not in _CLUB_WORDS and not w.isdigit())


def _hex(value) -> str:
    """ESPN colours come as "e20520", "#81D6AC" or, now and then, "##B5E7CE"."""
    value = str(value or "").lstrip("#")
    return f"#{value.lower()}" if re.fullmatch(r"[0-9a-fA-F]{6}", value) else ""


def _num(value, default=0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return int(number) if number.is_integer() else round(number, 3)


def _logo(team: dict) -> str:
    logos = team.get("logos") or []
    href = (logos[0] or {}).get("href", "") if logos else team.get("logo", "")
    return href if isinstance(href, str) and href.startswith("https://") else ""


async def _get(client: httpx.AsyncClient, url: str, ttl: float = 600, **params) -> dict:
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    if (hit := _cache.get(key)) is not None:
        return hit
    resp = await client.get(url, params=params, headers=HEADERS, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    return _cache.put(key, data if isinstance(data, dict) else {}, ttl=ttl)


# ------------------------------------------------------------ finding the club

async def _teams(client: httpx.AsyncClient, sport: str, slug: str) -> list[dict]:
    data = await _get(client, f"{SITE}/{sport}/{slug}/teams", ttl=DAY)
    leagues = (data.get("sports") or [{}])[0].get("leagues") or [{}]
    return [t.get("team") or {} for t in leagues[0].get("teams") or []]


def match(name: str, teams: list[dict], aliases: tuple[str, ...] = ()) -> dict | None:
    """The team this article names. Exact names first (any of ESPN's spellings or the article's aliases);
    then the one team whose name contains, or is contained in, the article's, when only one does."""
    wanted = {_core(n) for n in (name, *aliases) if _core(n)}
    if not wanted:
        return None

    def spellings(team: dict) -> set[frozenset[str]]:
        out = {_core(team.get(k, "")) for k in ("displayName", "shortDisplayName", "name")}
        out.add(_core(f"{team.get('location', '')} {team.get('name', '')}"))
        key = " ".join(_fold(team.get("displayName", "")))
        if key in _ALIASES:
            out.add(frozenset(_ALIASES[key]))
        return {s for s in out if s}

    for team in teams:
        if wanted & spellings(team):
            return team
    loose = [t for t in teams if any(s < w or w < s for s in spellings(t) for w in wanted)]
    # "Milan" would be inside "Inter Milan": a contained name only counts when it is the one candidate.
    return loose[0] if len(loose) == 1 else None


async def find(client: httpx.AsyncClient, name: str, league: str = "", sport: str = "", country: str = "",
               aliases: tuple[str, ...] = ()) -> tuple[str, str, dict] | None:
    """(sport, slug, team) for a club, through its Wikidata league, or its country's leagues."""
    if league in LEAGUES:
        candidates = [LEAGUES[league]]
    else:
        path = SPORTS.get(sport, "")
        candidates = [v for v in LEAGUES.values() if v[0] == path and country and v[3] == country]
    for sport_path, slug, _, _ in candidates[:4]:
        try:
            team = match(name, await _teams(client, sport_path, slug), aliases)
        except (httpx.HTTPError, ValueError):
            continue
        if team:
            return sport_path, slug, team
    return None


# ------------------------------------------------------------ the table

def _stats(entry: dict) -> dict:
    return {s.get("name") or s.get("type"): s for s in entry.get("stats") or [] if isinstance(s, dict)}


def _row(entry: dict, sport: str) -> dict:
    team, stats = entry.get("team") or {}, _stats(entry)
    value = lambda key: _num((stats.get(key) or {}).get("value"))  # noqa: E731
    note = entry.get("note") or {}
    row = {"id": str(team.get("id") or ""), "name": team.get("displayName", ""), "abbr": team.get("abbreviation", ""),
           "logo": _logo(team), "played": value("gamesPlayed"), "wins": value("wins"), "losses": value("losses"),
           "draws": value("ties"), "for": value("pointsFor"), "against": value("pointsAgainst"),
           "diff": value("pointDifferential"), "points": value("points"),
           "pct": (stats.get("winPercent") or {}).get("displayValue", ""),
           "behind": (stats.get("gamesBehind") or {}).get("displayValue", ""),
           "streak": (stats.get("streak") or {}).get("displayValue", ""),
           "rank": value("rank") or value("playoffSeed"),
           "zone": {"color": _hex(note.get("color")), "label": note.get("description", "")} if note else None}
    if sport != "soccer":
        row["played"] = row["played"] or row["wins"] + row["losses"] + row["draws"]
    return row


def _groups(data: dict) -> list[dict]:
    """Every table in a standings reply: one for a league, one per conference for the NBA or NFL."""
    out = []
    for node in data.get("children") or [data]:
        entries = (node.get("standings") or {}).get("entries") or []
        if entries:
            out.append({"name": node.get("name", ""), "entries": entries})
        out += _groups(node) if node is not data and node.get("children") else []
    return out


async def table(client: httpx.AsyncClient, sport: str, slug: str) -> list[dict]:
    """The league's tables, each sorted best first, with zones (Champions League, relegation) kept."""
    data = await _get(client, f"{STANDINGS}/{sport}/{slug}/standings")
    out = []
    for group in _groups(data):
        rows = [_row(e, sport) for e in group["entries"]]
        if sport == "soccer":
            rows.sort(key=lambda r: (r["rank"] or 99, -r["points"]))
        else:
            rows.sort(key=lambda r: (-float(r["pct"] or 0) if r["pct"] else 0, -r["wins"], r["losses"]))
            for i, r in enumerate(rows, 1):
                r["rank"] = i
        out.append({"name": group["name"], "rows": rows})
    return out


def _zones(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        z = r.get("zone")
        if z and z["label"] and z["label"] not in seen:
            seen.add(z["label"])
            out.append(z)
    return out


# ------------------------------------------------------------ games

def _side(comp: dict, team_id: str) -> tuple[dict, dict]:
    sides = comp.get("competitors") or []
    us = next((c for c in sides if str((c.get("team") or {}).get("id") or c.get("id")) == team_id), None)
    them = next((c for c in sides if c is not us), None)
    return us or {}, them or {}


def _score(side: dict):
    score = side.get("score")
    if isinstance(score, dict):
        score = score.get("displayValue", score.get("value"))
    return _num(score, None) if score not in (None, "") else None


def game(event: dict, team_id: str) -> dict | None:
    """One game from the team's side: opponent, home or away, score and outcome, or kick-off if to come."""
    comp = (event.get("competitions") or [{}])[0]
    us, them = _side(comp, team_id)
    if not us or not them:
        return None
    status = (comp.get("status") or event.get("status") or {}).get("type") or {}
    state = status.get("state", "pre")
    opp = them.get("team") or {}
    league = event.get("league") if isinstance(event.get("league"), dict) else {}
    out = {"date": event.get("date", ""), "state": state, "detail": status.get("shortDetail") or status.get("detail", ""),
           "home": us.get("homeAway") == "home", "opp": opp.get("displayName", ""), "opp_short": opp.get("shortDisplayName") or opp.get("displayName", ""),
           "opp_abbr": opp.get("abbreviation", ""), "opp_logo": _logo(opp),
           "venue": ((comp.get("venue") or {}).get("fullName") or ""),
           "comp": league.get("abbreviation") or league.get("name") or ((event.get("season") or {}).get("slug") or ""),
           "tv": next((b.get("media", {}).get("shortName", "") for b in comp.get("broadcasts") or [] if isinstance(b, dict)), "")}
    if state != "pre":
        gf, ga = _score(us), _score(them)
        if gf is None or ga is None:
            return None if state == "post" else out
        out.update({"for": gf, "against": ga})
        if state == "post":
            won = us.get("winner")
            out["outcome"] = "W" if won is True or gf > ga else "L" if them.get("winner") is True or gf < ga else "D"
    return out


async def _schedule(client: httpx.AsyncClient, sport: str, slug: str, team_id: str) -> tuple[list[dict], list[dict], dict | None]:
    """(results oldest first, fixtures soonest first, a game being played now)."""
    base = f"{SITE}/{sport}/{slug}/teams/{team_id}/schedule"
    calls = [_get(client, base)]
    if sport == "soccer":  # the plain schedule is the league's games played; fixtures are asked for apart
        calls.append(_get(client, base, fixture="true"))
    replies = await asyncio.gather(*calls, return_exceptions=True)
    seen, games = set(), []
    for reply in replies:
        if isinstance(reply, dict):
            for event in reply.get("events") or []:
                if event.get("id") not in seen and (g := game(event, team_id)):
                    seen.add(event.get("id"))
                    games.append(g)
    games.sort(key=lambda g: g["date"])
    results = [g for g in games if g["state"] == "post"]
    fixtures = [g for g in games if g["state"] == "pre"]
    live = next((g for g in games if g["state"] == "in"), None)
    return results[-10:], fixtures[:4], live


# ------------------------------------------------------------ the squad

_LINES = {"soccer": [("G", "Goalkeepers"), ("D", "Defenders"), ("M", "Midfielders"), ("F", "Forwards")]}


async def _squad(client: httpx.AsyncClient, sport: str, slug: str, team_id: str) -> tuple[list[dict], str]:
    data = await _get(client, f"{SITE}/{sport}/{slug}/teams/{team_id}/roster", ttl=DAY / 4)
    athletes = data.get("athletes") or []
    if athletes and isinstance(athletes[0], dict) and "items" in athletes[0]:  # the NFL groups its roster
        athletes = [a for group in athletes for a in group.get("items") or []]
    people = []
    for a in athletes:
        if not isinstance(a, dict) or not a.get("displayName"):
            continue
        pos = a.get("position") or {}
        flag = (a.get("flag") or {}).get("href", "")
        shot = (a.get("headshot") or {}).get("href", "")
        people.append({"name": a["displayName"], "short": a.get("shortName") or a["displayName"],
                       "number": str(a.get("jersey") or ""), "pos": pos.get("abbreviation", ""),
                       "position": pos.get("displayName") or pos.get("name", ""), "age": _num(a.get("age"), None),
                       "nat": a.get("citizenship", ""), "flag": flag if flag.startswith("https://") else "",
                       "photo": shot if shot.startswith("https://") else ""})
    order = _LINES.get(sport)
    groups: list[dict] = []
    if order:
        for code, label in order:
            members = [p for p in people if p["pos"][:1] == code]
            if members:
                groups.append({"code": code, "label": label, "players": members})
    else:
        by: dict[str, list] = {}
        for p in people:
            by.setdefault(p["position"] or "Squad", []).append(p)
        groups = [{"code": k[:1], "label": k, "players": v} for k, v in by.items()]
    for g in groups:
        g["players"].sort(key=lambda p: int(p["number"]) if p["number"].isdigit() else 999)
    coach = ""
    if (coaches := data.get("coach")) and isinstance(coaches, list) and isinstance(coaches[0], dict):
        coach = f"{coaches[0].get('firstName', '')} {coaches[0].get('lastName', '')}".strip()
    return groups, coach


# ------------------------------------------------------------ the panels

def _record(row: dict | None, sport: str) -> dict:
    if not row:
        return {}
    keys = ("played", "wins", "draws", "losses", "for", "against", "diff", "points", "pct", "behind", "streak")
    return {k: row[k] for k in keys if k in row} | {"soccer": sport == "soccer"}


def _ladder(rows: list[dict], at: int, size: int = 5) -> list[dict]:
    """The leader and the rows around this one, with "gap" marking a jump."""
    if at < 0:
        return rows[:size]
    lo = max(0, min(at - size // 2, len(rows) - size))
    picked = sorted({0, *range(lo, min(len(rows), lo + size))})
    out, last = [], -1
    for i in picked:
        out.append({**rows[i], "me": i == at, "gap": last >= 0 and i != last + 1})
        last = i
    return out


async def team(client: httpx.AsyncClient, name: str, league: str = "", sport: str = "", country: str = "",
               aliases: tuple[str, ...] = ()) -> dict:
    """Everything live for a club, or {} when ESPN doesn't have it (or can't be reached)."""
    try:
        hit = await find(client, name, league, sport, country, aliases)
        if not hit:
            return {}
        sport_path, slug, found = hit
        team_id = str(found.get("id") or "")
        tables, schedule, squad, info = await asyncio.gather(
            table(client, sport_path, slug), _schedule(client, sport_path, slug, team_id),
            _squad(client, sport_path, slug, team_id), _get(client, f"{SITE}/{sport_path}/{slug}/teams/{team_id}"),
            return_exceptions=True)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return {}
    tables = tables if isinstance(tables, list) else []
    results, fixtures, live = schedule if isinstance(schedule, tuple) else ([], [], None)
    groups, coach = squad if isinstance(squad, tuple) else ([], "")
    detail = (info.get("team") or {}) if isinstance(info, dict) else {}
    group = next((g for g in tables if any(r["id"] == team_id for r in g["rows"])), None)
    rows = group["rows"] if group else []
    at = next((i for i, r in enumerate(rows) if r["id"] == team_id), -1)
    me = rows[at] if at >= 0 else None
    venue = (detail.get("franchise") or {}).get("venue") or detail.get("venue") or {}
    _, short, _ = _BY_SLUG.get(slug, (sport_path, slug, ""))
    return {
        "sport": sport_path, "sport_name": SPORT_NAMES.get(sport_path, ""),
        "league": {"slug": slug, "name": short, "group": group["name"] if group and len(tables) > 1 else ""},
        "team": {"id": team_id, "name": found.get("displayName", ""), "short": found.get("shortDisplayName", ""),
                 "abbr": found.get("abbreviation", ""), "color": _hex(found.get("color")),
                 "alt": _hex(found.get("alternateColor")), "logo": _logo(found)},
        "summary": detail.get("standingSummary", ""),
        "position": me["rank"] if me else 0, "of": len(rows),
        "record": _record(me, sport_path),
        # A football table is short enough to show whole; a conference is shown around the team.
        "table": rows if sport_path == "soccer" else _ladder(rows, at, 7),
        "zones": _zones(rows),
        "ladder": _ladder(rows, at),
        "results": results, "fixtures": fixtures, "live": live,
        "form": "".join(r.get("outcome", "") for r in results[-5:]),
        "squad": groups, "coach": coach,
        "venue": venue.get("fullName", "") if isinstance(venue, dict) else "",
    }


async def league(client: httpx.AsyncClient, qid: str) -> dict:
    """A league's tables and the current round of games, for the league panel."""
    if qid not in LEAGUES:
        return {}
    sport_path, slug, short, _ = LEAGUES[qid]
    try:
        tables, board = await asyncio.gather(table(client, sport_path, slug),
                                             _get(client, f"{SITE}/{sport_path}/{slug}/scoreboard"))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return {}
    games = []
    for event in board.get("events") or []:
        comp = (event.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        home, away = sides.get("home") or {}, sides.get("away") or {}
        status = (comp.get("status") or event.get("status") or {}).get("type") or {}
        if not home or not away:
            continue
        games.append({"date": event.get("date", ""), "state": status.get("state", "pre"),
                      "detail": status.get("shortDetail", ""),
                      "home": {"name": (home.get("team") or {}).get("displayName", ""), "abbr": (home.get("team") or {}).get("abbreviation", ""),
                               "logo": _logo(home.get("team") or {}), "score": _score(home), "winner": bool(home.get("winner")),
                               "form": home.get("form", "")},
                      "away": {"name": (away.get("team") or {}).get("displayName", ""), "abbr": (away.get("team") or {}).get("abbreviation", ""),
                               "logo": _logo(away.get("team") or {}), "score": _score(away), "winner": bool(away.get("winner")),
                               "form": away.get("form", "")},
                      "venue": (comp.get("venue") or {}).get("fullName", "")})
    games.sort(key=lambda g: g["date"])
    rows = tables[0]["rows"] if tables else []
    return {"sport": sport_path, "sport_name": SPORT_NAMES.get(sport_path, ""), "name": short, "slug": slug,
            "tables": tables, "zones": _zones(rows), "games": games,
            "day": ((board.get("day") or {}).get("date") or ""),
            "leader": rows[0] if rows else None}
