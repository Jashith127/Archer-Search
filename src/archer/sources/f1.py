"""Formula One data for the driver, team, Grand Prix and circuit panels: Jolpica, the keyless Ergast successor.

Wikidata says who a driver or constructor *is* (photo, nationality, teams, born); this says how they are
*doing*: the championship and the drivers around them, every result this season, and a career in totals
(starts, wins, podiums, poles, titles). It also knows the calendar — the next round, its session times,
whether it is a sprint weekend, the qualifying grid once it exists — and who has won at each circuit.

Every Jolpica row carries a Wikipedia url, so a panel is matched to the article it was already built from
rather than by guessing at a name. Current drivers and teams are found in the standings; anyone else in
the full driver and constructor lists, which are fetched only when a panel needs them and kept a week.

Circuit outlines come from circuits.json, bundled from bacinger/f1-circuits (MIT): one line per circuit,
drawn in the racing direction from the start/finish line, matched to Jolpica's circuits by position. A
circuit that has never held a Grand Prix falls back to OpenStreetMap's raceways (`track`), which is slow,
so it is its own call. Anything that fails returns {} or "" and the panel keeps its Wikidata facts.
"""

import asyncio
import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from archer.sources import TTLCache

API = "https://api.jolpi.ca/ergast/f1"
OVERPASS = "https://overpass-api.de/api/interpreter"
KINDS = {"driver": "Driver", "constructor": "Constructor"}  # endpoint stem -> the key each row nests
HOUR = 3600
_cache = TTLCache(ttl=HOUR, size=256)          # Jolpica responses by url
_track = TTLCache(ttl=30 * 86400, size=64)     # OpenStreetMap outlines; a circuit does not move
_LOOKUP: dict[str, tuple[str, str]] = {}       # lowercased article title -> (kind, id)
_NAMES: dict[str, tuple[tuple[str, ...], str]] = {}  # driverId -> (name words, family name), folded
_gates: dict[int, asyncio.Semaphore] = {}      # Jolpica allows a few requests a second, per event loop

# Team colours, as the teams and the broadcast graphics use them, by Jolpica constructorId. They tint the
# panel's accent, the bars and the car on the track map; a team that isn't listed is drawn in plain ink.
TEAM_COLORS = {
    "mercedes": "#27F4D2", "ferrari": "#E8002D", "mclaren": "#FF8000", "red_bull": "#3671C6",
    "rb": "#6692FF", "alpine": "#0093CC", "haas": "#B6BABD", "audi": "#F50537", "williams": "#64C4FF",
    "aston_martin": "#229971", "cadillac": "#C8CCD2", "sauber": "#52E252", "alphatauri": "#5E8FAA",
    "toro_rosso": "#469BFF", "racing_point": "#F596C8", "force_india": "#F596C8", "renault": "#FFF500",
    "lotus_f1": "#FFB800", "brawn": "#B8FD6E", "alfa": "#C92D4B", "toyota": "#CC0000", "jordan": "#F2D500",
    "benetton": "#00A86B", "jaguar": "#0D6E3F", "bar": "#E1E1E1", "honda": "#E1E1E1", "lotus": "#FFB800",
    "tyrrell": "#1D4ED8", "brabham": "#1F8A4C", "minardi": "#F2F2F2", "stewart": "#E5E5E5", "ligier": "#3D7EDB",
}

# World champions by season, from Jolpica's own final standings, so a titles count is one lookup instead
# of a request per season. A season that ends after this table was written is fetched once (`_titles`).
DRIVER_CHAMPIONS = {
    1950: "farina", 1951: "fangio", 1952: "ascari", 1953: "ascari", 1954: "fangio", 1955: "fangio",
    1956: "fangio", 1957: "fangio", 1958: "hawthorn", 1959: "jack_brabham", 1960: "jack_brabham",
    1961: "phil_hill", 1962: "hill", 1963: "clark", 1964: "surtees", 1965: "clark", 1966: "jack_brabham",
    1967: "hulme", 1968: "hill", 1969: "stewart", 1970: "rindt", 1971: "stewart", 1972: "emerson_fittipaldi",
    1973: "stewart", 1974: "emerson_fittipaldi", 1975: "lauda", 1976: "hunt", 1977: "lauda",
    1978: "mario_andretti", 1979: "scheckter", 1980: "jones", 1981: "piquet", 1982: "keke_rosberg",
    1983: "piquet", 1984: "lauda", 1985: "prost", 1986: "prost", 1987: "piquet", 1988: "senna", 1989: "prost",
    1990: "senna", 1991: "senna", 1992: "mansell", 1993: "prost", 1994: "michael_schumacher",
    1995: "michael_schumacher", 1996: "damon_hill", 1997: "villeneuve", 1998: "hakkinen", 1999: "hakkinen",
    2000: "michael_schumacher", 2001: "michael_schumacher", 2002: "michael_schumacher",
    2003: "michael_schumacher", 2004: "michael_schumacher", 2005: "alonso", 2006: "alonso", 2007: "raikkonen",
    2008: "hamilton", 2009: "button", 2010: "vettel", 2011: "vettel", 2012: "vettel", 2013: "vettel",
    2014: "hamilton", 2015: "hamilton", 2016: "rosberg", 2017: "hamilton", 2018: "hamilton", 2019: "hamilton",
    2020: "hamilton", 2021: "max_verstappen", 2022: "max_verstappen", 2023: "max_verstappen",
    2024: "max_verstappen", 2025: "norris",
}
CONSTRUCTOR_CHAMPIONS = {
    1958: "vanwall", 1959: "cooper-climax", 1960: "cooper-climax", 1961: "ferrari", 1962: "brm", 1963: "lotus-climax",
    1964: "ferrari", 1965: "lotus-climax", 1966: "brabham-repco", 1967: "brabham-repco", 1968: "lotus-ford",
    1969: "matra-ford", 1970: "team_lotus", 1971: "tyrrell", 1972: "team_lotus", 1973: "team_lotus",
    1974: "mclaren", 1975: "ferrari", 1976: "ferrari", 1977: "ferrari", 1978: "team_lotus", 1979: "ferrari",
    1980: "williams", 1981: "williams", 1982: "ferrari", 1983: "ferrari", 1984: "mclaren", 1985: "mclaren",
    1986: "williams", 1987: "williams", 1988: "mclaren", 1989: "mclaren", 1990: "mclaren", 1991: "mclaren",
    1992: "williams", 1993: "williams", 1994: "williams", 1995: "benetton", 1996: "williams", 1997: "williams",
    1998: "mclaren", 1999: "ferrari", 2000: "ferrari", 2001: "ferrari", 2002: "ferrari", 2003: "ferrari",
    2004: "ferrari", 2005: "renault", 2006: "renault", 2007: "ferrari", 2008: "ferrari", 2009: "brawn",
    2010: "red_bull", 2011: "red_bull", 2012: "red_bull", 2013: "red_bull", 2014: "mercedes", 2015: "mercedes",
    2016: "mercedes", 2017: "mercedes", 2018: "mercedes", 2019: "mercedes", 2020: "mercedes", 2021: "mercedes",
    2022: "red_bull", 2023: "red_bull", 2024: "mclaren", 2025: "mclaren",
}
_CHAMPIONS = {"driver": DRIVER_CHAMPIONS, "constructor": CONSTRUCTOR_CHAMPIONS}

# Jolpica names countries and nationalities as words; a flag needs ISO 3166-1 alpha-2.
_ISO = {
    "argentina": "ar", "australia": "au", "austria": "at", "azerbaijan": "az", "bahrain": "bh", "belgium": "be",
    "bolivia": "bo", "brazil": "br", "british": "gb", "bahraini": "bh", "canada": "ca", "chile": "cl",
    "china": "cn", "colombia": "co", "costa rica": "cr", "czech": "cz", "czechia": "cz", "denmark": "dk",
    "ecuador": "ec", "emirates": "ae", "england": "gb", "finland": "fi", "france": "fr", "germany": "de",
    "german": "de", "great britain": "gb", "hungary": "hu", "india": "in", "indian": "in", "indonesia": "id",
    "ireland": "ie", "israel": "il", "italy": "it", "italian": "it", "japan": "jp", "japanese": "jp",
    "kuwait": "kw", "latvia": "lv", "lebanon": "lb", "malaysia": "my", "mexico": "mx", "monaco": "mc",
    "netherlands": "nl", "dutch": "nl", "new zealand": "nz", "nigeria": "ng", "norway": "no", "oman": "om",
    "pakistan": "pk", "panama": "pa", "paraguay": "py", "peru": "pe", "poland": "pl", "portugal": "pt",
    "qatar": "qa", "romania": "ro", "russia": "ru", "saudi arabia": "sa", "saudi": "sa", "serbia": "rs",
    "singapore": "sg", "slovakia": "sk", "south africa": "za", "south korea": "kr", "korean": "kr",
    "korea": "kr", "spain": "es", "spanish": "es", "sweden": "se", "swiss": "ch", "switzerland": "ch",
    "thailand": "th", "turkey": "tr", "türkiye": "tr", "turkish": "tr", "ukraine": "ua", "usa": "us",
    "united states": "us", "american": "us", "uruguay": "uy", "vietnam": "vn", "brazilian": "br",
    "french": "fr", "belgian": "be", "monegasque": "mc", "danish": "dk", "finnish": "fi", "greek": "gr",
    "hungarian": "hu", "peruvian": "pe", "russian": "ru", "spaniard": "es", "swede": "se", "swedish": "se",
    "austrian": "at", "australian": "au", "canadian": "ca", "mexican": "mx", "argentine": "ar",
    "argentinian": "ar", "chinese": "cn", "thai": "th", "new zealander": "nz", "polish": "pl",
    "portuguese": "pt", "south african": "za", "venezuelan": "ve", "colombian": "co", "chilean": "cl",
    "irish": "ie", "indonesian": "id", "malaysian": "my", "uae": "ae", "abu dhabi": "ae",
    "united arab emirates": "ae", "uk": "gb", "united kingdom": "gb", "las vegas": "us", "miami": "us",
    "liechtensteiner": "li", "rhodesian": "zw", "east german": "de", "american-italian": "us",
    "argentine-italian": "ar",
}


def flag(country: str) -> str:
    """A country flag URL from a country or nationality name, or "" when it isn't one."""
    iso = _ISO.get((country or "").strip().casefold(), "")
    return f"https://flagcdn.com/{iso}.svg" if iso else ""


def color(constructor_id: str) -> str:
    return TEAM_COLORS.get(constructor_id or "", "")


def article(url: str) -> str:
    """The Wikipedia article title in an API url: "Max_Verstappen" -> "Max Verstappen"."""
    try:
        return unquote(urlsplit(url or "").path.rsplit("/", 1)[-1]).replace("_", " ").strip()
    except ValueError:
        return ""


def _number(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _points(value) -> float | int:
    """Points as a number that prints the way the table does: 25, 12.5."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else f


def _name(entry: dict) -> str:
    return f"{entry.get('givenName', '')} {entry.get('familyName', '')}".strip()


def _gate() -> asyncio.Semaphore:
    loop = id(asyncio.get_running_loop())
    if loop not in _gates:
        _gates[loop] = asyncio.Semaphore(3)
    return _gates[loop]


async def _get(client: httpx.AsyncClient, path: str, ttl: float = HOUR, **params) -> dict:
    """One Jolpica response, cached by url. Errors propagate; each caller decides what it can live without."""
    key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    if (hit := _cache.get(key)) is not None:
        return hit
    async with _gate():
        resp = await client.get(f"{API}/{path}.json", params=params, timeout=8)
    resp.raise_for_status()
    data = resp.json().get("MRData") or {}
    return _cache.put(key, data, ttl=ttl)


def _list(node, key: str) -> list[dict]:
    """One key out of a nested object, always as a list whether Jolpica sent one item or many."""
    value = node.get(key) if isinstance(node, dict) else None
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _table(data: dict, outer: str, key: str) -> list[dict]:
    """Jolpica nests each table under season/round objects and wraps a lone list in a list."""
    node = data.get(outer, {})
    while isinstance(node, list) and node:
        node = node[0]
    return _list(node, key) if isinstance(node, dict) else []


# ------------------------------------------------------------ standings and lookup

def _fold(text: str) -> list[str]:
    """Lowercase words without accents: "Nico Hülkenberg" -> ["nico", "hulkenberg"]."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", plain.casefold())


def _known(entry: dict) -> None:
    """Keep a driver's name, for an article whose title isn't the one Jolpica links to."""
    if ident := str(entry.get("driverId") or ""):
        family = _fold(entry.get("familyName", ""))
        _NAMES[ident] = (tuple(_fold(entry.get("givenName", "")) + family), family[-1] if family else "")


def _by_name(title: str) -> str:
    """The driver an article title names when Jolpica links to a redirect instead: "Kimi Antonelli" is
    Andrea Kimi Antonelli, "Alex Albon" is Alexander Albon. The family name has to match, and every other
    word of the title has to begin one of the driver's names; one match only, or none."""
    words = _fold(re.sub(r"\s*\(.*?\)", "", title))     # "George Russell (racing driver)" -> george russell
    while words and words[-1] in ("jr", "sr", "ii", "iii"):   # "Carlos Sainz Jr." -> carlos sainz
        words.pop()
    if len(words) < 2:
        return ""
    found = [ident for ident, (names, family) in _NAMES.items()
             if family == words[-1] and all(any(n.startswith(w) for n in names) for w in words[:-1])]
    return found[0] if len(found) == 1 else ""


def _remember(title: str, kind: str, ident: str) -> None:
    if not title or not ident:
        return
    if len(_LOOKUP) >= 2048 and title not in _LOOKUP:
        _LOOKUP.pop(next(iter(_LOOKUP)))
    _LOOKUP[title] = (kind, ident)


async def _standings(client: httpx.AsyncClient, kind: str) -> list[dict]:
    """One row per entry in the current championship's standings, best first."""
    data = await _get(client, f"current/{kind}standings", limit=40)
    lists = data.get("StandingsTable", {}).get("StandingsLists") or []
    table = lists[0] if isinstance(lists, list) and lists else lists
    rows = (table.get(f"{KINDS[kind]}Standings") or []) if isinstance(table, dict) else []
    for row in rows:
        entry = row.get(KINDS[kind]) or {}
        _remember(article(entry.get("url", "")).casefold(), kind, str(entry.get(f"{kind}Id") or ""))
        if kind == "driver":
            _known(entry)
    return rows


async def _everyone(client: httpx.AsyncClient, kind: str) -> None:
    """Every driver or constructor Jolpica knows, so a retired driver or a team long gone is found too.

    Jolpica pages at 100, so the driver list is nine requests; it runs only when a panel isn't in the
    current standings and is then kept a week.
    """
    if _cache.get(f"everyone:{kind}"):
        return
    first = await _get(client, f"{kind}s", ttl=7 * 86400, limit=100, offset=0)
    pages = [first] + list(await asyncio.gather(*(
        _get(client, f"{kind}s", ttl=7 * 86400, limit=100, offset=offset)
        for offset in range(100, _number(first.get("total")), 100))))
    for page in pages:
        for entry in _table(page, f"{KINDS[kind]}Table", f"{KINDS[kind]}s"):
            _remember(article(entry.get("url", "")).casefold(), kind, str(entry.get(f"{kind}Id") or ""))
            if kind == "driver":
                _known(entry)
    _cache.put(f"everyone:{kind}", True, ttl=7 * 86400)


async def _find(client: httpx.AsyncClient, title: str) -> tuple[str, str] | None:
    wanted = (title or "").strip().casefold()
    if not wanted:
        return None
    if (hit := _LOOKUP.get(wanted)) is None:
        await asyncio.gather(_standings(client, "driver"), _standings(client, "constructor"),
                             return_exceptions=True)
    if (hit := _LOOKUP.get(wanted)) is None and (ident := _by_name(title)):
        hit = ("driver", ident)
    if hit is None:
        await asyncio.gather(_everyone(client, "driver"), _everyone(client, "constructor"))
        hit = _LOOKUP.get(wanted) or (("driver", ident) if (ident := _by_name(title)) else None)
    return hit


# A query is about Formula One when it says so; "mercedes" alone is the car maker, "mercedes f1 team" isn't.
_CUES = {"f1", "formula", "team", "racing", "scuderia", "gp", "constructor", "driver"}
# Names a team goes by that its Jolpica name doesn't contain.
_TEAM_ALIASES = {"rb": [["racing", "bulls"], ["vcarb"], ["visa", "cash", "app"]], "sauber": [["kick", "sauber"]],
                 "red_bull": [["red", "bull"]], "aston_martin": [["aston", "martin"]]}


async def title_for_query(client: httpx.AsyncClient, text: str) -> str:
    """The article for a current team or driver a search names in its own words: "mercedes petronas amg
    f1 team" is Mercedes-Benz in Formula One, "hamilton f1" is Lewis Hamilton. Needs an F1 cue word, and
    a team name (all of its words) or one driver's family name; uses the cached standings only."""
    words = _fold(text)
    if not (set(words) & _CUES) or len(words) < 2:
        return ""
    try:
        drivers, teams = await asyncio.gather(_standings(client, "driver"), _standings(client, "constructor"))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return ""
    best, size = "", 0
    for row in teams:
        entry = row.get("Constructor") or {}
        name = [w for w in _fold(entry.get("name", "")) if w not in ("f1", "team")]
        for spelling in [name] + _TEAM_ALIASES.get(str(entry.get("constructorId")), []):
            if spelling and all(w in words for w in spelling) and len(spelling) > size:
                best, size = article(entry.get("url", "")), len(spelling)
    if best:
        return best
    found = {article((r.get("Driver") or {}).get("url", "")) for r in drivers
             if _fold((r.get("Driver") or {}).get("familyName", ""))[-1:] and
             _fold((r.get("Driver") or {}).get("familyName", ""))[-1] in words}
    return found.pop() if len(found) == 1 else ""


async def find(client: httpx.AsyncClient, title: str) -> tuple[str, str] | None:
    """("driver" | "constructor", id) for someone in this season's standings, else None. One cached table,
    so the wiki panel can ask it about any racing driver without reaching for the full lists."""
    wanted = (title or "").strip().casefold()
    if wanted and wanted not in _LOOKUP:
        await asyncio.gather(_standings(client, "driver"), _standings(client, "constructor"), return_exceptions=True)
    return _LOOKUP.get(wanted) or (("driver", ident) if (ident := _by_name(title)) else None)


async def lookup(client: httpx.AsyncClient, title: str) -> dict:
    """Everything live for the driver or constructor whose Wikipedia article is `title`, or {}."""
    try:
        hit = await _find(client, title)
        if not hit:
            return {}
        kind, ident = hit
        return await (_driver(client, ident) if kind == "driver" else _team(client, ident))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return {}  # the panel keeps its Wikidata facts


def _ladder(rows: list[dict], at: int, kind: str) -> list[dict]:
    """The leader and the entries either side of this one, for the mini standings table."""
    keep = sorted({0, *range(max(0, at - 2), min(len(rows), at + 3))})
    out = []
    for i in keep:
        row = rows[i]
        entry = row.get(KINDS[kind]) or {}
        team = (row.get("Constructors") or [{}])[-1] if kind == "driver" else entry
        out.append({"position": _number(row.get("position")), "points": _points(row.get("points")),
                    "name": _name(entry) if kind == "driver" else entry.get("name", ""),
                    "code": entry.get("code", "") if kind == "driver" else "",
                    "team": team.get("name", ""), "color": color(team.get("constructorId", "")),
                    "me": i == at, "gap": i > 0 and i == keep[1] and keep[1] - keep[0] > 1})
    return out


def _standing(rows: list[dict], kind: str, ident: str) -> dict:
    at = next((i for i, r in enumerate(rows) if str((r.get(KINDS[kind]) or {}).get(f"{kind}Id")) == ident), -1)
    if at < 0:
        return {}
    row, leader = rows[at], rows[0]
    points = _points(row.get("points"))
    above = rows[at - 1] if at else None
    return {"position": _number(row.get("position")), "points": points, "wins": _number(row.get("wins")),
            "of": len(rows), "to_leader": _points(float(leader.get("points") or 0) - float(points)),
            "to_next": _points(float(above.get("points") or 0) - float(points)) if above else 0,
            "ladder": _ladder(rows, at, kind)}


# ------------------------------------------------------------ season

def _when(entry: dict | None) -> str:
    """A session or race as the ISO instant it starts, or "" when it isn't scheduled."""
    if not isinstance(entry, dict):
        return ""
    date = str(entry.get("date") or "")
    clock = str(entry.get("time") or "")[:5] or "12:00"
    return f"{date}T{clock}:00Z" if len(date) == 10 else ""


SESSIONS = ("FirstPractice", "SecondPractice", "ThirdPractice", "SprintQualifying", "SprintShootout",
            "Sprint", "Qualifying")


async def _schedule(client: httpx.AsyncClient) -> list[dict]:
    """This season's calendar, one entry per round."""
    data = await _get(client, "current", ttl=6 * HOUR, limit=40)
    out = []
    for race in _table(data, "RaceTable", "Races"):
        circuit = race.get("Circuit") or {}
        where = circuit.get("Location") or {}
        country = str(where.get("country") or "")
        sessions = {name: _when(race.get(name)) for name in SESSIONS}
        out.append({
            "round": _number(race.get("round")), "season": str(race.get("season") or ""),
            "name": str(race.get("raceName") or ""), "url": str(race.get("url") or ""),
            "date": str(race.get("date") or ""), "start": _when(race),
            "circuit": str(circuit.get("circuitName") or ""), "circuit_id": str(circuit.get("circuitId") or ""),
            "circuit_url": str(circuit.get("url") or ""), "locality": str(where.get("locality") or ""),
            "country": country, "flag": flag(country),
            "lat": str(where.get("lat") or ""), "lon": str(where.get("long") or ""),
            "sprint": bool(race.get("Sprint")),
            "schedule": {**{k: v for k, v in sessions.items() if v}, "Race": _when(race)},
        })
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _done(race: dict) -> bool:
    """A race counts as run two and a half hours after its start."""
    try:
        start = datetime.fromisoformat(race["start"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return _now() > start + timedelta(hours=2.5)


def _calendar(rounds: list[dict]) -> list[dict]:
    """The season as a strip of rounds: name, country, date, and whether it has been run."""
    return [{"round": r["round"], "name": r["name"], "country": r["country"], "flag": r["flag"],
             "date": r["date"], "done": _done(r), "sprint": r["sprint"]} for r in rounds]


def _result(race: dict, row: dict) -> dict:
    status = str(row.get("status") or "")
    return {"round": _number(race.get("round")), "name": str(race.get("raceName") or ""),
            "position": _number(row.get("position")), "text": str(row.get("positionText") or ""),
            "grid": _number(row.get("grid")), "points": _points(row.get("points")), "status": status,
            "classified": str(row.get("positionText") or "").isdigit(),
            "fastest": str((row.get("FastestLap") or {}).get("rank") or "") == "1",
            "code": (row.get("Driver") or {}).get("code", ""),
            "driver": _name(row.get("Driver") or {}),
            "team": (row.get("Constructor") or {}).get("constructorId", "")}


def _form(results: list[dict]) -> dict:
    """What a season of results says, beyond the points: best finish, podiums, retirements, average."""
    classified = [r for r in results if r["classified"]]
    best = min(classified, key=lambda r: r["position"], default=None)
    return {"races": len(results), "podiums": sum(1 for r in classified if r["position"] <= 3),
            "points_finishes": sum(1 for r in results if r["points"]),
            "dnf": sum(1 for r in results if not r["classified"]),
            "fastest": sum(1 for r in results if r["fastest"]),
            "average": round(sum(r["position"] for r in classified) / len(classified), 1) if classified else 0,
            "best": {"position": best["position"], "name": best["name"]} if best else {},
            "gained": sum(r["grid"] - r["position"] for r in classified if r["grid"])}


# ------------------------------------------------------------ career

async def _total(client: httpx.AsyncClient, path: str) -> int:
    return _number((await _get(client, path, ttl=12 * HOUR, limit=1)).get("total"))


async def _titles(client: httpx.AsyncClient, kind: str, ident: str) -> list[int]:
    """Championship seasons. Seasons finished since the table above was written are asked for once."""
    table = _CHAMPIONS[kind]
    year = _now().year
    for season in range(max(table) + 1, year):
        if season in table:
            continue
        try:
            data = await _get(client, f"{season}/{kind}standings/1", ttl=30 * 86400)
            rows = _table(data, "StandingsTable", "StandingsLists")
            row = (rows[0].get(f"{KINDS[kind]}Standings") or [{}])[0] if rows else {}
            if ident_ := (row.get(KINDS[kind]) or {}).get(f"{kind}Id"):
                table[season] = str(ident_)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            break
    return sorted(y for y, who in table.items() if who == ident)


async def _career(client: httpx.AsyncClient, kind: str, ident: str) -> dict:
    """Career totals. A constructor's results count every car rather than every race, so it gets wins,
    poles and titles only; each count stands alone, so one failed request leaves a gap, not nothing."""
    base = f"{kind}s/{ident}"
    seasons = await _get(client, f"{base}/seasons", ttl=12 * HOUR, limit=100)
    years = sorted(_number(s.get("season")) for s in _table(seasons, "SeasonTable", "Seasons"))
    if not years:
        return {}
    counts = {"wins": f"{base}/results/1", "poles": f"{base}/qualifying/1"}
    if kind == "driver":
        counts |= {"starts": f"{base}/results", "second": f"{base}/results/2", "third": f"{base}/results/3"}
    totals = dict(zip(counts, await asyncio.gather(*(_soft(_total(client, path), None) for path in counts.values()))))
    out = {"seasons": len(years), "first": years[0], "last": years[-1], "years": years,
           "wins": totals["wins"], "poles": totals["poles"],
           "titles": await _soft(_titles(client, kind, ident), [])}
    if kind == "driver":
        out["starts"] = totals["starts"]
        parts = (totals["wins"], totals["second"], totals["third"])
        out["podiums"] = sum(parts) if None not in parts else None
    return out


async def _soft(coro, default):
    try:
        return await coro
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return default


async def team_logo(client: httpx.AsyncClient, url: str) -> str:
    """The team's logo: the infobox image of the constructor's Wikipedia article (`url`).

    Most teams' logos are non-free files held on English Wikipedia rather than Commons, and Wikidata has
    a logo (P154) for only a few, so the page image is asked for with any licence. It goes through Archer's
    shared Wikimedia helper, so a rate limit pauses this with everything else, and is kept a week.
    """
    from archer.sources import wiki          # here, not at the top: wiki imports nothing of ours, keep it so
    title = article(url)
    if not title:
        return ""
    key = f"logo:{title}"
    if (hit := _cache.get(key)) is not None:
        return hit
    try:
        data = await wiki._get(client, wiki.WP_API, action="query", titles=title,
                               prop="pageimages", piprop="original", pilicense="any", redirects=1,
                               format="json", formatversion=2)
        page = next(iter(data.get("query", {}).get("pages") or []), {})
        source = (page.get("original") or {}).get("source", "")
    except Exception:  # noqa: BLE001 - no logo is a fine answer; the panel just goes without
        return ""
    logo = source.split("?", 1)[0] if source.startswith("https://upload.wikimedia.org/") else ""
    return _cache.put(key, logo, ttl=7 * 86400 if logo else HOUR)


async def _driver(client: httpx.AsyncClient, ident: str) -> dict:
    rows, schedule, season, career = await asyncio.gather(
        _soft(_standings(client, "driver"), []), _soft(_schedule(client), []),
        _soft(_get(client, f"current/drivers/{ident}/results", limit=40), {}),
        _soft(_career(client, "driver", ident), {}))
    results = [_result(race, row) for race in _table(season, "RaceTable", "Races")
               for row in _list(race, "Results")[:1]]
    row = next((r for r in rows if str((r.get("Driver") or {}).get("driverId")) == ident), {})
    entry = row.get("Driver") or {}
    if not (row or results or career):
        return {}                      # Jolpica answered nothing about them: the Wikidata half stands alone
    team = (row.get("Constructors") or [{}])[-1]
    team_id = team.get("constructorId") or (results[-1]["team"] if results else "")
    return {"role": "driver", "id": ident, "name": _name(entry), "code": entry.get("code", ""),
            "number": entry.get("permanentNumber", ""), "nationality": entry.get("nationality", ""),
            "team": team.get("name", ""), "team_id": team_id, "color": color(team_id),
            "logo": await team_logo(client, team.get("url", "")) if row else "",
            "season": schedule[0]["season"] if schedule else "",
            "standing": _standing(rows, "driver", ident), "results": results, "form": _form(results),
            "rounds": len(schedule), "calendar": _calendar(schedule), "career": career,
            "active": bool(row)}


async def _team(client: httpx.AsyncClient, ident: str) -> dict:
    rows, drivers, schedule, season, career = await asyncio.gather(
        _soft(_standings(client, "constructor"), []), _soft(_standings(client, "driver"), []),
        _soft(_schedule(client), []),
        _soft(_get(client, f"current/constructors/{ident}/results", limit=100), {}),
        _soft(_career(client, "constructor", ident), {}))
    row = next((r for r in rows if str((r.get("Constructor") or {}).get("constructorId")) == ident), {})
    entry = row.get("Constructor") or {}
    lineup = [{"name": _name(d.get("Driver") or {}), "code": (d.get("Driver") or {}).get("code", ""),
               "number": (d.get("Driver") or {}).get("permanentNumber", ""),
               "url": (d.get("Driver") or {}).get("url", ""),
               "position": _number(d.get("position")), "points": _points(d.get("points")),
               "wins": _number(d.get("wins"))}
              for d in drivers if str(((d.get("Constructors") or [{}])[-1]).get("constructorId")) == ident]
    rounds = []
    for race in _table(season, "RaceTable", "Races"):
        cars = [_result(race, r) for r in _list(race, "Results")]
        rounds.append({"round": _number(race.get("round")), "name": str(race.get("raceName") or ""),
                       "points": _points(sum(float(c["points"]) for c in cars)), "cars": cars})
    return {"role": "team", "id": ident, "name": entry.get("name", ""),
            "logo": await team_logo(client, entry.get("url", "")) if entry else "",
            "nationality": entry.get("nationality", ""), "color": color(ident),
            "season": schedule[0]["season"] if schedule else "",
            "standing": _standing(rows, "constructor", ident), "lineup": lineup, "rounds": rounds,
            "total_rounds": len(schedule), "calendar": _calendar(schedule), "career": career,
            "active": bool(row)}


# ------------------------------------------------------------ circuits

_CIRCUITS: list[dict] | None = None


def _bundled() -> list[dict]:
    global _CIRCUITS
    if _CIRCUITS is None:
        try:
            _CIRCUITS = json.loads((Path(__file__).with_name("circuits.json")).read_text("utf-8"))["circuits"]
        except (OSError, ValueError, KeyError):
            _CIRCUITS = []
    return _CIRCUITS


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def circuit_map(lat, lon, within: float = 4.0) -> dict:
    """The bundled outline nearest a circuit's coordinates, or {} when no Grand Prix circuit is that close."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return {}
    best = min(_bundled(), key=lambda c: _km(la, lo, c["lat"], c["lon"]), default=None)
    if not best or _km(la, lo, best["lat"], best["lon"]) > within:
        return {}
    return {k: best[k] for k in ("id", "name", "length", "opened", "first_gp", "w", "h", "points")}


async def _winners(client: httpx.AsyncClient, circuit_id: str, count: int = 8) -> tuple[int, list[dict]]:
    """How many Grands Prix a circuit has held, and its most recent winners, newest first."""
    path = f"circuits/{circuit_id}/results/1"
    total = await _total(client, path)
    if not total:
        return 0, []
    data = await _get(client, path, ttl=12 * HOUR, limit=count, offset=max(0, total - count))
    out = []
    for race in reversed(_table(data, "RaceTable", "Races")):
        row = (_list(race, "Results") or [{}])[0]
        team = row.get("Constructor") or {}
        out.append({"season": str(race.get("season") or ""), "name": str(race.get("raceName") or ""),
                    "driver": _name(row.get("Driver") or {}), "team": team.get("name", ""),
                    "color": color(team.get("constructorId", "")), "time": (row.get("Time") or {}).get("time", "")})
    return total, out


async def _podium(client: httpx.AsyncClient, round_: int) -> list[dict]:
    """The top three of a round with what the card needs to tell the race: time or gap, grid slot, laps."""
    data = await _get(client, f"current/{round_}/results", limit=3)
    races = _table(data, "RaceTable", "Races")
    out = []
    for r in (_list(races[0], "Results") if races else []):
        lap = r.get("FastestLap") or {}
        team = r.get("Constructor") or {}
        out.append({"position": _number(r.get("position")), "driver": _name(r.get("Driver") or {}),
                    "code": (r.get("Driver") or {}).get("code", ""), "number": (r.get("Driver") or {}).get("permanentNumber", ""),
                    "team": team.get("name", ""), "color": color(team.get("constructorId", "")),
                    "grid": _number(r.get("grid")), "laps": _number(r.get("laps")), "points": _points(r.get("points")),
                    "time": str((r.get("Time") or {}).get("time") or ""),
                    "millis": _number((r.get("Time") or {}).get("millis")),
                    "fastest": str(lap.get("rank") or "") == "1",
                    "fastest_time": str((lap.get("Time") or {}).get("time") or ""), "fastest_lap": _number(lap.get("lap"))})
    return out


def _report(podium: list[dict], lap_m) -> dict:
    """The race in four numbers: laps, distance, the winner's average speed and the fastest lap (if it was a podium car's)."""
    win = next((p for p in podium if p["position"] == 1), None)
    if not win or not win["laps"]:
        return {}
    km = round(win["laps"] * float(lap_m) / 1000, 3) if lap_m else 0
    hours = win["millis"] / 3.6e6 if win["millis"] else 0
    quick = next((p for p in podium if p["fastest"]), None)
    return {"laps": win["laps"], "distance": km, "avg_kph": round(km / hours, 1) if km and hours else 0,
            "fastest": {"driver": quick["driver"], "color": quick["color"], "time": quick["fastest_time"],
                        "lap": quick["fastest_lap"]} if quick and quick["fastest_time"] else {}}


def _race_card(race: dict) -> dict:
    """A round as the weekend card draws it, with the circuit's outline when it is bundled."""
    return {**race, "done": _done(race), "map": circuit_map(race["lat"], race["lon"])}


async def next_race(client: httpx.AsyncClient) -> dict:
    """The next round (or the one under way): where, when, every session, the circuit, the season strip."""
    rounds = await _schedule(client)
    upcoming = next((r for r in rounds if not _done(r)), None)
    if not upcoming:
        return {"calendar": _calendar(rounds), "season": rounds[0]["season"] if rounds else ""} if rounds else {}
    return {**_race_card(upcoming), "calendar": _calendar(rounds), "rounds": len(rounds)}


def _same_race(title: str, name: str) -> bool:
    """ "Monaco Grand Prix" matches itself, and so does "2026 Monaco Grand Prix" (the season's article)."""
    t = title.strip().casefold()
    if t[:4].isdigit():
        t = t[4:].strip()
    return t == name.casefold() or t.replace("formula one ", "") == name.casefold()


async def grand_prix(client: httpx.AsyncClient, title: str) -> dict:
    """A Grand Prix panel's live half: this season's round, the circuit's outline and its recent winners."""
    try:
        rounds = await _schedule(client)
        race = next((r for r in rounds if _same_race(title, r["name"])), None)
        if not race:
            return {}
        card = _race_card(race)
        held, winners = await _soft(_winners(client, race["circuit_id"]), (0, []))
        podium = await _soft(_podium(client, race["round"]), []) if card["done"] else []
        report = _report(podium, (card.get("map") or {}).get("length")) if podium else {}
        return {**card, "held": held, "winners": winners, "podium": podium, "report": report, "rounds": len(rounds)}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return {}


async def circuit(client: httpx.AsyncClient, title: str, lat: str = "", lon: str = "") -> dict:
    """A circuit panel's live half, matched by Wikipedia article or, failing that, by position."""
    try:
        data = await _get(client, "circuits", ttl=7 * 86400, limit=100)
        circuits = _table(data, "CircuitTable", "Circuits")
        wanted = (title or "").strip().casefold()
        found = next((c for c in circuits if article(c.get("url", "")).casefold() == wanted), None)
        if not found and lat and lon:
            def distance(c):
                where = c.get("Location") or {}
                try:
                    return _km(float(lat), float(lon), float(where.get("lat")), float(where.get("long")))
                except (TypeError, ValueError):
                    return 1e9
            near = min(circuits, key=distance, default=None)
            found = near if near and distance(near) < 3 else None
        if not found:
            # Not a Grand Prix circuit: the panel can still draw OpenStreetMap's outline from its position.
            return {"map": circuit_map(lat, lon, within=1.5)} if lat and lon else {}
        where = found.get("Location") or {}
        ident = str(found.get("circuitId") or "")
        rounds = await _soft(_schedule(client), [])
        race = next((r for r in rounds if r["circuit_id"] == ident), None)
        held, winners = await _soft(_winners(client, ident), (0, []))
        return {"id": ident, "name": str(found.get("circuitName") or ""), "url": str(found.get("url") or ""),
                "locality": str(where.get("locality") or ""), "country": str(where.get("country") or ""),
                "flag": flag(str(where.get("country") or "")),
                "lat": str(where.get("lat") or ""), "lon": str(where.get("long") or ""),
                "map": circuit_map(where.get("lat"), where.get("long")),
                "race": _race_card(race) if race else {}, "held": held, "winners": winners}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return {}


async def qualifying(client: httpx.AsyncClient) -> dict:
    """The qualifying grid of the most recent round, with all three session times.

    Returned whole, but nothing is drawn until the reader asks: the panel shows the session time and a
    button, so nobody is shown a result they had not decided to see.
    """
    data = await _get(client, "current/last/qualifying", ttl=900, limit=30)
    races = _table(data, "RaceTable", "Races")
    if not races:
        return {}
    race = races[0]
    grid = []
    for row in _list(race, "QualifyingResults"):
        driver, team = row.get("Driver") or {}, (row.get("Constructor") or {})
        times = {s: str(row.get(s) or "") for s in ("Q1", "Q2", "Q3")}
        best = next((row[s] for s in ("Q3", "Q2", "Q1") if row.get(s)), "")
        grid.append({"position": _number(row.get("position")), "name": _name(driver), "team": team.get("name", ""),
                     "color": color(team.get("constructorId", "")), "code": driver.get("code", ""),
                     "number": driver.get("permanentNumber", ""), "url": driver.get("url", ""),
                     "times": {k: v for k, v in times.items() if v}, "best": str(best)})
    return {"round": _number(race.get("round")), "season": str(race.get("season") or ""),
            "name": str(race.get("raceName") or ""), "date": str(race.get("date") or ""),
            "time": str(race.get("time") or ""), "grid": grid,
            "session": _when(race.get("Qualifying")) or _when(race)}


# ------------------------------------------------------------ OpenStreetMap fallback

def _outline(ways: list[list[tuple[float, float]]], budget: int = 240) -> str:
    """Raceway geometry as one SVG path in a 100x100 box, thinned to about `budget` points.

    The ways need no ordering: each is drawn where it lies, and because they share their end nodes the
    track reads as one closed outline. Longitude is scaled by cos(latitude) so the shape isn't stretched
    east-west the way a raw lat/lon plot would be.
    """
    points = [p for way in ways for p in way]
    if len(points) < 4:
        return ""
    lats = [p[0] for p in points]
    mid_lat = math.radians(sum(lats) / len(lats))
    kx = math.cos(mid_lat) or 1.0
    xs = [p[1] * kx for p in points]
    ys = [p[0] for p in points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if width <= 0 or height <= 0:
        return ""
    scale = 92 / max(width, height)                      # a little margin inside the 100 box
    pad_x, pad_y = (100 - width * scale) / 2, (100 - height * scale) / 2
    drop = max(1, round(len(points) / budget))           # thin the straights, keep every corner's worth
    parts = []
    for way in ways:
        kept = way[::drop]
        if way[-1] not in kept:
            kept.append(way[-1])
        parts.append("M" + " ".join(
            f"{round(pad_x + (lon * kx - min(xs)) * scale, 1)} {round(pad_y + (max(ys) - lat) * scale, 1)}"
            for lat, lon in kept))
    return "".join(parts)


async def track(client: httpx.AsyncClient, lat: str, lon: str, key: str = "") -> str:
    """A circuit's outline from OpenStreetMap's raceways, as SVG path data, for circuits not bundled.

    Cached for a month because a circuit does not move. Overpass is frequently slow or busy, so this
    is only ever called after the page is up and a failure simply means no outline rather than a hole.
    """
    if not lat or not lon:
        return ""
    cache_key = key or f"{lat},{lon}"
    if (hit := _track.get(cache_key)) is not None:
        return hit
    try:
        query = f"[out:json][timeout:25];way[highway=raceway](around:1500,{lat},{lon});out geom;"
        resp = await client.post(OVERPASS, data={"data": query},
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=45)
        resp.raise_for_status()
        ways = [[(p["lat"], p["lon"]) for p in (el.get("geometry") or []) if "lat" in p]
                for el in resp.json().get("elements") or []
                # Karting tracks and drag strips share the tag; only the circuit itself is wanted.
                if (el.get("tags") or {}).get("motorsport_type", "") not in ("karting", "drag_racing")]
        path = _outline([w for w in ways if len(w) > 1])
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        path = ""
    return _track.put(cache_key, path, ttl=None if path else 600)

