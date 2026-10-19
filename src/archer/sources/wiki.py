"""Entity panels from Wikipedia (match, description, image) and Wikidata (typed facts)."""

import asyncio
import re
import time
from datetime import date
from urllib.parse import quote, unquote, urlsplit

import httpx

from archer.config import settings
from archer.sources import TTLCache, watch

WP_API = "https://en.wikipedia.org/w/api.php"
WD_API = "https://www.wikidata.org/w/api.php"
WDQS = "https://query.wikidata.org/sparql"

_entity_cache = TTLCache(ttl=86400)
_works_cache = TTLCache(ttl=86400)
_pending: dict[str, asyncio.Task] = {}  # lookups in flight, shared by everyone asking for the same name
_country_iso: dict[str, str] = {}  # country QID -> ISO code, for the flag on "Country" tiles

# P31 (instance of) -> panel kind
KINDS = {
    # An F1 driver is almost always typed "human"; only the occupation says which series they race in.
    # This catches the rare item that carries the series as its P31 instead (see _kind for the rest).
    "f1driver": {"Q10841764"},
    "character": {"Q15632617", "Q95074", "Q15773347", "Q15773317", "Q3658341", "Q1114461", "Q80447738"},
    "person": {"Q5"},
    # Video games (and their DLC, remakes, compilations, demos, series) before "software", which also types them.
    "game": {"Q7889", "Q209163", "Q1066707", "Q865493", "Q21125433", "Q4393107", "Q16070115", "Q64170203",
             "Q1755420", "Q7058673"},
    # Motorsport before "product" (a car model is also a product model) and before "org" (a racing team is
    # also a business). Races and series mostly arrive through the sport fallback in _kind.
    "f1team": {"Q10497835", "Q109136128"},
    # Esports teams before clubs (an esports team is also a sports team), clubs and leagues before "org"
    # (a football club is often also a business). Players are found by occupation in _kind.
    "esteam": {"Q61974339"},
    "club": {"Q476028", "Q13393265", "Q17156793", "Q13027888", "Q4498974", "Q20639856", "Q12973014", "Q847017"},
    "league": {"Q15991303", "Q15991290", "Q623109"},
    "car": {"Q3231690", "Q59773381"},
    # A Grand Prix is a "Formula One event"; most other races only arrive through the sport fallback.
    "race": {"Q60147807", "Q87292877", "Q108886259"},
    # Circuits before "place", which every item with coordinates falls back to. A street circuit counts.
    "circuit": {"Q2338524", "Q926439"},
    "film": {"Q11424", "Q24869", "Q202866", "Q29168811", "Q18011172", "Q506240", "Q93204"},
    "tv": {"Q5398426", "Q581714", "Q1259759", "Q15416", "Q526877", "Q63952888", "Q117467246"},
    "book": {"Q7725634", "Q571", "Q47461344", "Q8261", "Q1667921", "Q277759", "Q14406742"},
    "music": {"Q215380", "Q5741069", "Q2088357", "Q482994", "Q7366", "Q134556", "Q105543609", "Q9212979"},
    "country": {"Q6256", "Q3624078", "Q7275", "Q1763527", "Q15634554"},
    "org": {"Q4830453", "Q6881511", "Q891723", "Q783794", "Q43229", "Q163740", "Q1058914",
            "Q167037", "Q3918", "Q875538", "Q210167", "Q18388277", "Q484652"},
    "species": {"Q16521"},
    "chemical": {"Q11344", "Q11173", "Q79529", "Q113145171", "Q59199015", "Q12140"},
    "disease": {"Q12136", "Q18123741", "Q929833", "Q112193867", "Q169872", "Q42303753", "Q18965518"},
    "software": {"Q7397", "Q9143", "Q341", "Q166142", "Q271680", "Q188860", "Q620615", "Q9135", "Q6368",
                 "Q21129801", "Q1330336", "Q131093"},
    "holiday": {"Q1445650", "Q1197685", "Q132241", "Q15275719", "Q2017179"},
    "landmark": {"Q570116", "Q33506", "Q16560", "Q23413", "Q4989906", "Q12518", "Q44539", "Q16970", "Q32815",
                 "Q839954", "Q9259", "Q12280", "Q2977", "Q1081138", "Q46169", "Q8502", "Q4022", "Q23397",
                 "Q35657", "Q10864048", "Q1107656", "Q41176", "Q483110", "Q207694", "Q811979"},
    # Product models (smartphone, computer, console); anything with a manufacturer or brand also counts.
    "product": {"Q19723451", "Q10929058", "Q55990535"},
    "place": {"Q515", "Q1549591", "Q5119", "Q1637706", "Q200250", "Q7930989", "Q123705", "Q486972", "Q3957"},
}
ACTOR = {"Q33999", "Q10800557", "Q10798782", "Q2405480", "Q2259451", "Q948329"}
WRITER = {"Q36180", "Q6625963", "Q482980", "Q49757", "Q1930187", "Q4853732", "Q28389"}
MUSICIAN = {"Q177220", "Q639669", "Q753110", "Q488205", "Q130857", "Q183945", "Q855091", "Q2252262"}
ATHLETE = {"Q2066131", "Q937857", "Q12299841", "Q3665646", "Q10833314", "Q10843402", "Q11513337",
           "Q10871364", "Q19204627", "Q13141064", "Q11338576", "Q10349745", "Q4009406", "Q13382576", "Q14089670"}
POLITICIAN = {"Q82955", "Q193391", "Q372436", "Q30461"}
EXECUTIVE = {"Q43845", "Q484876", "Q131524", "Q2961975", "Q1979607"}

# Motorsport. A driver's occupation names the series they race in; a race or team is typed by its sport
# (P641) rather than by P31, because Le Mans is a "recurring sporting event" and Ferrari is a business.
F1_DRIVER = {"Q10841764"}  # occupation "Formula One driver"
RACING_DRIVER = {"Q10349745", "Q378622"}  # "racing automobile driver", "racing driver"
# Sports that mean "a series of motor races" when a P31 hasn't already said so.
MOTORSPORT = {"Q5367", "Q5386", "Q1968", "Q192420", "Q506749", "Q1089579", "Q1805420", "Q7856", "Q60147807",
              "Q965550", "Q17163326", "Q328716", "Q850292"}

# Esports. A player is a "professional gamer" by occupation; a team or league plays the sport "esports".
ESPORTS_PLAYER = {"Q4379701"}
ESPORTS = "Q300920"
TEAM_SPORTS = {"Q2736", "Q5372", "Q41323", "Q5369", "Q41466"}

CURRENCY_UNITS = {"Q4917": "$", "Q80524": "₹", "Q4916": "€", "Q25224": "£", "Q8146": "¥"}
UNIT_SUFFIX = {"Q11573": "m", "Q174728": "cm", "Q11570": "kg", "Q712226": "km²", "Q828224": "km",
               "Q7727": "min", "Q11574": "s", "Q35852": "ha", "Q232291": "mi²", "Q11579": "K",
               "Q25267": "°C", "Q42289": "°F", "Q483261": "u", "Q13147228": "g/cm³", "Q844211": "kg/m³",
               "Q41803": "g", "Q3241121": "g/mol", "Q174789": "mm",
               # Speeds (P2052) arrive in km/h or mph, and a car's top speed is the point of the tile.
               "Q180154": "km/h", "Q211256": "mph"}
_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

# Badge shown above every entity panel: kind -> (icon, label). Icons live in templates/ui_icons.html.
KIND_META = {
    "person": ("user", "Person"), "film": ("film", "Film"), "tv": ("tv", "TV series"), "book": ("book", "Book"),
    "music": ("music", "Music"), "country": ("flag", "Country"), "org": ("building", "Organisation"),
    "species": ("leaf", "Species"), "chemical": ("flask", "Chemical"), "disease": ("virus", "Health condition"),
    "software": ("code", "Software"), "game": ("gamepad", "Video game"), "holiday": ("sparkle", "Holiday"), "landmark": ("landmark", "Landmark"),
    "place": ("pin", "Place"), "product": ("package", "Product"), "thing": ("info", "Topic"), "character": ("user", "Fictional character"),
    "car": ("car", "Car"), "race": ("trophy", "Racing series"), "circuit": ("map", "Circuit"),
    "f1driver": ("helmet", "Formula One driver"), "f1team": ("users", "Formula One team"),
    "club": ("shield", "Sports club"), "league": ("trophy", "League"),
    "esteam": ("gamepad", "Esports team"), "esplayer": ("gamepad", "Esports player"),
}
SUBKIND_LABEL = {"athlete": "Athlete", "politician": "Politician", "executive": "Business leader", "writer": "Writer"}
TILE_ICONS = {
    "Age": "calendar", "Died": "calendar", "Born in": "pin", "Parents": "users", "Spouse": "heart",
    "Height": "ruler", "Occupation": "briefcase", "Team": "shield", "Position": "target", "Number": "hash",
    "Office": "landmark", "Party": "flag", "Organisation": "building", "Net worth": "coins",
    "Notable work": "book", "Released": "calendar", "Publisher": "building", "Platforms": "gamepad",
    "Game modes": "users", "Series": "layers", "Engine": "cube", "Units sold": "chart", "Composer": "music", "Runtime": "clock", "Director": "film",
    "Box office": "coins", "Genre": "tag", "Aired": "tv", "Seasons": "layers", "Episodes": "list",
    "Network": "tv", "Author": "pen", "Published": "calendar", "Pages": "book", "Formed": "calendar",
    "Label": "disc", "Members": "users", "Artist": "music", "Founded": "calendar", "CEO": "user",
    "Headquarters": "building", "Employees": "users", "Revenue": "chart", "Country": "globe",
    "Population": "users", "Area": "map", "Elevation": "mountain", "Leader": "user",
    "Scientific name": "leaf", "Conservation": "shield", "Parent taxon": "leaf", "Capital": "star",
    "Currency": "coins", "Head of government": "landmark", "Symbol": "flask", "Formula": "flask",
    "Molar mass": "scale", "Atomic mass": "scale", "Melting point": "thermometer",
    "Boiling point": "flame", "Density": "cube", "Symptoms": "heartbeat", "Specialty": "stethoscope",
    "Spread by": "virus", "Treatment": "pill", "Developer": "code", "First released": "calendar",
    "Latest version": "tag", "License": "book", "Date": "calendar", "Religion": "star",
    "Observed in": "globe", "Type": "tag", "Location": "pin", "Built": "hammer", "Architect": "pen",
    "Heritage": "award", "Visitors / year": "users", "Since": "calendar",
    "Appears in": "tv", "Created by": "pen", "Played by": "user",
    "Brand": "tag", "Operating system": "code", "Processor": "cube", "Weight": "scale", "Launch price": "coins",
    # Motorsport.
    "Body style": "car", "Manufacturer": "building", "Production": "calendar", "Units built": "chart",
    "Top speed": "gauge", "Layout": "layers", "Engine": "cube", "Transmission": "cube", "Platform": "cube",
    "Class": "tag", "Predecessor": "swap", "Successor": "swap", "Discipline": "trophy", "Sport": "globe",
    "First held": "calendar", "Last held": "calendar", "Rounds": "chart", "Circuit": "map", "Champion": "user",
    "Record holder": "trophy", "Next race": "calendar",
    "Record": "trophy", "Number": "hash", "Constructor": "users", "Debut": "calendar", "Races": "chart",
    "Wins": "award", "Podiums": "award", "Points": "chart", "Car": "car", "Entry": "car", "Base": "pin",
    "Chassis": "cube", "Engine maker": "building", "Tyres": "target", "Colours": "palette", "Assembled": "building",
    # Clubs, leagues and esports.
    "League": "trophy", "Ground": "landmark", "Head coach": "user", "Teams": "users", "Game": "gamepad",
    "Games": "gamepad", "Based in": "pin", "Nationality": "globe",
}


def flag_url(iso2: str) -> str:
    """Country flag by ISO 3166-1 alpha-2 code (flagcdn serves Wikimedia-derived SVGs)."""
    return f"https://flagcdn.com/{iso2.lower()}.svg" if re.fullmatch(r"[A-Za-z]{2}", iso2 or "") else ""
SOCIAL = {
    "P2003": ("Instagram", "https://www.instagram.com/{}"),
    "P2002": ("X", "https://x.com/{}"),
    "P7085": ("TikTok", "https://www.tiktok.com/@{}"),
    "P2397": ("YouTube", "https://www.youtube.com/channel/{}"),
    "P345": ("IMDb", "https://www.imdb.com/{}"),
    "P1258": ("Rotten Tomatoes", "https://www.rottentomatoes.com/{}"),
    "P1651": ("YouTube", "https://www.youtube.com/watch?v={}"),
    "P2850": ("Apple Music", "https://music.apple.com/artist/{}"),
    "P1902": ("Spotify", "https://open.spotify.com/artist/{}"),
    "P2037": ("GitHub", "https://github.com/{}"),
}
_STOP = {"the", "a", "an", "of", "and", "vs", "v", "in", "on", "for", "to", "is", "who", "what"}


def _norm_tokens(text: str) -> list[str]:
    return [t for t in re.sub(r"[^\w]+", " ", text.lower()).split() if t]


def _matches(query: str, title: str) -> bool:
    q = [t for t in _norm_tokens(query) if t not in _STOP]
    t = _norm_tokens(title)
    if not q:
        return False
    return set(q) <= set(t) and len([x for x in t if x not in _STOP]) <= len(q) + 3


# -------------------------------------------------------------- Wikipedia

_WP_PROPS = {
    "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
    "prop": "pageprops|pageimages|description|extracts|info", "inprop": "url",
    "exintro": 1, "explaintext": 1, "exchars": 1600, "exlimit": 5,
    # pilicense=any: film posters and book covers are non-free and hidden by default.
    "piprop": "original|thumbnail", "pithumbsize": 800, "pilicense": "any",
    "ppprop": "wikibase_item|disambiguation",
}


def _usable(page: dict) -> bool:
    return not page.get("missing") and "disambiguation" not in page.get("pageprops", {}) \
        and page.get("pageprops", {}).get("wikibase_item")


class RateLimited(httpx.HTTPError):
    pass


_cooldown_until = 0.0


async def _get(client: httpx.AsyncClient, url: str, **params) -> dict:
    """GET JSON from a Wikimedia API, backing off for Retry-After when rate-limited."""
    global _cooldown_until
    if time.monotonic() < _cooldown_until:
        raise RateLimited("Wikimedia cooldown active")
    try:
        resp = await client.get(url, params=params)
    except httpx.TimeoutException:
        resp = await client.get(url, params=params)  # one retry: Wikimedia timeouts are usually one-offs
    if resp.status_code == 429:
        retry = resp.headers.get("retry-after", "")
        _cooldown_until = time.monotonic() + (int(retry) if retry.isdigit() else 120)
        raise RateLimited(f"Wikimedia rate limit; pausing lookups for {retry or 120}s")
    resp.raise_for_status()
    return resp.json()


async def _find_page(client: httpx.AsyncClient, candidate: str, allow_suggestion: bool = True) -> tuple[dict | None, bool]:
    """Returns (page, corrected). corrected=True when Wikipedia's spelling suggestion was used."""
    # Exact title first (one call resolves case and redirects: "bangalore" -> Bengaluru);
    # search only when that misses, to keep within Wikimedia's rate limits.
    # Redirects are case-sensitive past the first letter, so "andrea kimi antonelli" misses the redirect
    # "Andrea Kimi Antonelli" → Kimi Antonelli; the title-cased spelling is tried before searching.
    pages = (await _get(client, WP_API, **_WP_PROPS, titles=candidate)).get("query", {}).get("pages", [])
    if pages and _usable(pages[0]):
        return pages[0], False
    titled = " ".join(w[:1].upper() + w[1:] for w in candidate.split())
    if titled != candidate and titled[1:] != candidate[1:]:   # only when it differs past the first letter
        pages = (await _get(client, WP_API, **_WP_PROPS, titles=titled)).get("query", {}).get("pages", [])
        if pages and _usable(pages[0]):
            return pages[0], False
    found = await _get(client, WP_API, **_WP_PROPS, generator="search", gsrsearch=candidate, gsrlimit=3,
                       gsrinfo="suggestion")
    for page in sorted(found.get("query", {}).get("pages", []), key=lambda p: p.get("index", 99)):
        if _usable(page) and _matches(candidate, page["title"]):
            return page, False
    # Typos ("zendeya") return no pages but a suggestion ("zendaya"); follow it once.
    suggestion = found.get("query", {}).get("searchinfo", {}).get("suggestion")
    if allow_suggestion and suggestion and suggestion.lower() != candidate.lower():
        page, _ = await _find_page(client, suggestion, allow_suggestion=False)
        return page, page is not None
    return None, False


matches = _matches  # public: views uses it to read which meaning the web results are about

_NAMESPACES = ("Special:", "Category:", "File:", "Help:", "Wikipedia:", "Talk:", "Template:", "Portal:", "User:", "Draft:")


def article_title(url: str) -> str:
    """English Wikipedia article title from a result URL ("…/wiki/Dune_(2021_film)" -> "Dune (2021 film)"), else ""."""
    parts = urlsplit(url)
    if parts.hostname not in ("en.wikipedia.org", "en.m.wikipedia.org") or not parts.path.startswith("/wiki/"):
        return ""
    title = unquote(parts.path[len("/wiki/"):]).replace("_", " ").strip()
    return "" if not title or title.startswith(_NAMESPACES) or "(disambiguation)" in title else title


async def title_for_site(client: httpx.AsyncClient, url: str, name: str) -> str:
    """The Wikipedia article whose Wikidata item lists this site as its official website (P856).

    "apple" ranking apple.com first means Apple Inc., not the fruit. Only titles that match the
    query count, so a product page on the same domain can't take over.
    """
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    if not host or "." not in host:
        return ""
    sites = [f"https://{h}{tail}" for h in (f"www.{host}", host) for tail in ("/", "")]
    found = await _get(client, WD_API, action="query", list="search", format="json", srlimit=5,
                       srsearch='haswbstatement:"' + "|".join(f"P856={site}" for site in sites) + '"')
    ids = [hit["title"] for hit in found.get("query", {}).get("search", []) if re.fullmatch(r"Q\d+", hit.get("title", ""))]
    if not ids:
        return ""
    ents = (await _get(client, WD_API, action="wbgetentities", ids="|".join(ids), props="sitelinks",
                       sitefilter="enwiki", format="json")).get("entities", {})
    for qid in ids:
        title = ents.get(qid, {}).get("sitelinks", {}).get("enwiki", {}).get("title", "")
        if title and _matches(name, title):
            return title
    return ""


# --------------------------------------------------------------- Wikidata

def _claims(entity: dict, prop: str) -> list[dict]:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        if claim.get("rank") == "deprecated":
            continue
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            out.append(snak["datavalue"])
    # Wikidata marks the current value (e.g. current CEO) as preferred.
    preferred = [c["mainsnak"]["datavalue"] for c in entity.get("claims", {}).get(prop, [])
                 if c.get("rank") == "preferred" and c.get("mainsnak", {}).get("snaktype") == "value"]
    return preferred or out


def _ids(entity: dict, prop: str, limit: int = 8) -> list[str]:
    return [v["value"]["id"] for v in _claims(entity, prop) if v.get("type") == "wikibase-entityid"][:limit]


def _first(entity: dict, prop: str):
    values = _claims(entity, prop)
    return values[0]["value"] if values else None


def _current_ids(entity: dict, prop: str, limit: int = 2) -> list[str]:
    """Values still in effect (no end-time qualifier), e.g. current team or office.

    Old roles often lack an end date (Satya Nadella's Sun Microsystems job), so the
    most recently *started* open role wins.
    """
    current = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        qualifiers = claim.get("qualifiers", {})
        if snak.get("snaktype") != "value" or claim.get("rank") == "deprecated" or "P582" in qualifiers:
            continue
        start = next((q["datavalue"]["value"]["time"] for q in qualifiers.get("P580", [])
                      if q.get("snaktype") == "value"), "")
        current.append((claim.get("rank") == "preferred", start, snak["datavalue"]["value"]["id"]))
    current.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return [qid for _, _, qid in current[:limit]] or _ids(entity, prop, limit)


def _fmt_time(value: dict | None) -> tuple[str, int | None, date | None]:
    """Wikidata time -> (display text, year, date or None)."""
    if not value:
        return "", None, None
    m = re.match(r"([+-])(\d+)-(\d\d)-(\d\d)", value["time"])
    if not m:
        return "", None, None
    sign, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    precision = value.get("precision", 11)
    if sign == "-":
        return f"{y} BC", -y, None
    if precision >= 11 and mo and d:
        dt = date(y, mo, d)
        return f"{d} {dt.strftime('%B')} {y}", y, dt
    if precision == 10 and mo:
        return f"{date(y, mo, 1).strftime('%B')} {y}", y, None
    return str(y), y, None


def _fmt_amount(value: dict | None, labels: dict[str, str]) -> str:
    if not value:
        return ""
    amount = float(value["amount"])
    unit = value.get("unit", "1").rsplit("/", 1)[-1]
    if unit in CURRENCY_UNITS:
        sym = CURRENCY_UNITS[unit]
        for size, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
            if abs(amount) >= size:
                return f"{sym}{amount / size:.1f} {word}"
        return f"{sym}{amount:,.0f}"
    if unit == "Q11573" and amount < 3:  # height in metres
        return f"{amount:.2f} m"
    if unit == "Q7727":  # minutes -> 2h 46m
        h, m = divmod(int(amount), 60)
        return f"{h}h {m}m" if h else f"{m} min"
    number = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.3f}".rstrip("0").rstrip(".")
    suffix = UNIT_SUFFIX.get(unit) or (labels.get(unit, "") if unit != "1" else "")
    return f"{number}{suffix}" if suffix.startswith("°") else f"{number} {suffix}".strip()


# Songs, singles, compositions, albums and EPs: things you can actually press play on.
PLAYABLE = {"Q7366", "Q134556", "Q105543609", "Q482994", "Q208569", "Q169930", "Q2031291", "Q209939"}


def _music_player(entity: dict, kind: str) -> dict | None:
    """Embeds for a song or album. YouTube only when Wikidata lists its (music) video."""
    if kind != "music" or not set(_ids(entity, "P31", 20)) & PLAYABLE:
        return None
    cc = settings.home_country.lower()

    def first(prop: str) -> str:
        value = _first(entity, prop)
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.\-]{5,64}", value) else ""

    sources = []
    if video := first("P1651"):
        sources.append({"kind": "youtube", "label": "Music video", "id": video,
                        "embed": f"https://www.youtube-nocookie.com/embed/{video}?autoplay=1&rel=0",
                        "thumb": f"https://i.ytimg.com/vi/{video}/hqdefault.jpg",
                        "url": f"https://www.youtube.com/watch?v={video}"})
    if track := first("P2207"):
        sources.append({"kind": "spotify", "label": "Spotify", "embed": f"https://open.spotify.com/embed/track/{track}",
                        "url": f"https://open.spotify.com/track/{track}"})
    elif album := first("P2205"):
        sources.append({"kind": "spotify", "label": "Spotify", "embed": f"https://open.spotify.com/embed/album/{album}",
                        "url": f"https://open.spotify.com/album/{album}"})
    if song := first("P10110"):
        sources.append({"kind": "applemusic", "label": "Apple Music", "embed": f"https://embed.music.apple.com/{cc}/song/{song}",
                        "url": f"https://music.apple.com/{cc}/song/{song}"})
    elif album := first("P2281"):
        sources.append({"kind": "applemusic", "label": "Apple Music", "embed": f"https://embed.music.apple.com/{cc}/album/{album}",
                        "url": f"https://music.apple.com/{cc}/album/{album}"})
    return {"sources": sources} if sources else None


def _watch_data(entity: dict, kind: str, title: str) -> dict:
    """TMDB id for live country-specific lookup, plus Wikidata's streaming IDs as a fallback."""

    services, seen = [], set()
    for prop, (name, template, kinds) in watch.WIKIDATA_SERVICES.items():
        value = _first(entity, prop)
        if kind in kinds and isinstance(value, str) and name not in seen and re.fullmatch(r"[A-Za-z0-9_.\-]{2,80}", value):
            seen.add(name)
            mark, color = watch.SERVICE_BRANDS.get(name, ("", ""))
            services.append({"name": name, "url": template.format(value), "mark": mark, "color": color})
    tmdb = _first(entity, "P4983" if kind == "tv" else "P4947")
    country = settings.home_country.upper()
    return {"media": "tv" if kind == "tv" else "movie", "tmdb": tmdb if isinstance(tmdb, str) else "",
            "services": services, "country": country, "country_name": watch.country_name(country),
            "flag": flag_url(country), "justwatch": watch.justwatch_search(country, title)}


def _unique_facts(facts: list[tuple[str, str]], skip: set[str]) -> list[tuple[str, str]]:
    """First occurrence of each label wins; labels already shown as tiles are dropped."""
    seen, out = set(skip), []
    for label, value in facts:
        if label not in seen:
            seen.add(label)
            out.append((label, value))
    return out


def _compact(n: float) -> str:
    for size, word in ((1e9, "billion"), (1e6, "million")):
        if n >= size:
            return f"{n / size:.1f} {word}"
    return f"{n:,.0f}"


def _age(born: date | None, died: date | None) -> int | None:
    if not born:
        return None
    end = died or date.today()
    return end.year - born.year - ((end.month, end.day) < (born.month, born.day))


async def _wbget(client: httpx.AsyncClient, ids: list[str], props: str) -> dict:
    if not ids:
        return {}
    out: dict = {}
    # 50 ids per request (the API's limit); a film's cast and crew can need several, asked together.
    batches = await asyncio.gather(*(_get(client, WD_API, action="wbgetentities", ids="|".join(ids[i:i + 50]),
                                          props=props, languages="en|mul", format="json")
                                     for i in range(0, len(ids), 50)))
    for data in batches:
        for key, ent in data.get("entities", {}).items():
            out[key] = ent
            if redirect := ent.get("redirects"):  # merged items come back under their new id
                out[redirect["from"]] = ent
    return out


def _label(entity: dict, fallback: str) -> str:
    labels = entity.get("labels", {})
    # Many items now carry only a language-neutral "mul" label (e.g. people's names).
    return (labels.get("en") or labels.get("mul") or {}).get("value", fallback)


def _kind(entity: dict) -> str:
    types = set(_ids(entity, "P31", 20))
    claims = entity.get("claims", {})
    # A driver in a specific series is typed "human", so the generic person/athlete panel would answer
    # first. The occupation names the series, so it is read before any P31.
    if set(_ids(entity, "P106", 40)) & F1_DRIVER:
        return "f1driver"
    if set(_ids(entity, "P106", 40)) & ESPORTS_PLAYER:
        return "esplayer"
    # A league ESPN covers is a league, whatever newer class Wikidata has moved it to.
    from archer.sources.sports import LEAGUES
    if entity.get("id") in LEAGUES:
        return "league"
    for kind, qids in KINDS.items():
        if types & qids:
            return kind
    # A works team's article is often "Mercedes-Benz in Formula One", which Wikidata types only as an
    # article covering several topics; the title is the one reliable sign.
    if _label(entity, "").endswith(" in Formula One"):
        return "f1team"
    # A race or series is usually a "recurring sporting event" rather than a racing series, so it is found
    # by its sport. Circuits are typed as places above, so only events reach this.
    if set(_ids(entity, "P641", 6)) & MOTORSPORT:
        return "race"
    # A team typed by a class we don't list still plays in a league; esports organisations name their sport.
    sports_played = set(_ids(entity, "P641", 6))
    if "P118" in claims and sports_played & TEAM_SPORTS and "Q5" not in types:
        return "club"
    if ESPORTS in sports_played and "Q5" not in types and types & {"Q4438121", "Q4830453", "Q43229"}:
        return "esteam"
    # Many items are typed by a subclass we don't list (Python is a "multi-paradigm
    # programming language", not "programming language"), so fall back to tell-tale properties.
    for kind, props in (("game", ("P404", "P1733", "P5794", "P8084", "P5944")), ("software", ("P348", "P277", "P1324")), ("chemical", ("P274", "P246")),
                        ("disease", ("P780", "P1995", "P494")), ("country", ("P36", "P38")),
                        ("film", ("P57", "P2047")), ("book", ("P50",)), ("species", ("P225",))):
        if sum(p in claims for p in props) >= (2 if kind in ("country", "film") else 1):
            return kind
    if "P176" in claims or "P1716" in claims:
        return "product"
    if _first(entity, "P625"):
        return "place"
    return "thing"


# ------------------------------------------------------------ video games

def _digits(value: str) -> str:
    return value if re.fullmatch(r"\d{1,10}", value or "") else ""


def _units(entity: dict) -> str:
    """Units sold: the largest figure (later claims are usually later totals), as "12 million"."""
    counts = [float(v["amount"]) for v in (c["value"] for c in _claims(entity, "P2664")) if isinstance(v, dict) and v.get("amount")]
    if not counts:
        return ""
    top = max(counts)
    return f"{top / 1e6:.3g} million" if top >= 1e6 else f"{int(top):,}"


def _review_scores(entity: dict) -> list[dict]:
    """Review scores with their outlet (P444 qualified by P447), one per outlet, e.g. Metacritic 95/100."""
    out, seen = [], set()
    for claim in entity.get("claims", {}).get("P444", []):
        snak = claim.get("mainsnak", {})
        score = snak.get("datavalue", {}).get("value")
        by = next((q["datavalue"]["value"]["id"] for q in claim.get("qualifiers", {}).get("P447", [])
                   if q.get("snaktype") == "value"), "")
        if isinstance(score, str) and by and by not in seen and re.fullmatch(r"[\d.]{1,4}(/\d{1,3})?%?", score.strip()):
            seen.add(by)
            out.append({"by": by, "score": score.strip()})
    return out


# Store pages by Wikidata ID; formats from each property's formatter URL. A platform without an ID gets its
# store's search page, so every console the game is on has somewhere to buy it.
_STORE_IDS = (("P1733", "Steam", "https://store.steampowered.com/app/{}/", "steam"),
              ("P6278", "Epic Games Store", "https://store.epicgames.com/p/{}", "epic"),
              ("P2725", "GOG", "https://www.gog.com/{}", "gog"),
              ("P5885", "Microsoft Store", "https://www.microsoft.com/store/productId/{}", "xbox"),
              ("P5971", "PlayStation Store", "https://store.playstation.com/en-in/product/{}", "playstation"),
              ("P5944", "PlayStation Store", "https://store.playstation.com/en-us/product/{}", "playstation"))
_STORE_SEARCH = (("PlayStation", "PlayStation Store", "https://store.playstation.com/en-in/search/{}", "playstation"),
                 ("Xbox", "Microsoft Store", "https://www.xbox.com/en-IN/search/results/games?q={}", "xbox"),
                 ("Nintendo", "Nintendo eShop", "https://www.nintendo.com/search/#q={}", "nintendo"))


_PLATFORM_SHORT = {"Microsoft Windows": "PC", "Xbox Series X and Series S": "Xbox Series X|S", "macOS": "Mac",
                   "Nintendo Switch": "Switch", "Nintendo Switch 2": "Switch 2"}


def _game_stores(entity: dict, platforms: list[str], title: str) -> list[dict]:
    stores, seen = [], set()
    for prop, label, template, key in _STORE_IDS:
        value = _first(entity, prop)
        if isinstance(value, str) and key not in seen and re.fullmatch(r"[A-Za-z0-9_./-]{1,90}", value):
            seen.add(key)
            url = template.format(quote(value, safe="/"))
            stores.append({"label": label, "url": url, "kind": key, "host": urlsplit(url).hostname})
    for word, label, template, key in _STORE_SEARCH:
        if key not in seen and any(word.lower() in p.lower() for p in platforms):
            seen.add(key)
            url = template.format(quote(title))
            stores.append({"label": label, "url": url, "kind": key, "host": urlsplit(url).hostname, "search": True})
    return stores


# ------------------------------------------------------------ motorsport

# P279 walks up the class tree, so a car's or a series' generic ancestors ("car model", "motor vehicle")
# say nothing; the specific ones ("sports car", "supercar") are the labels worth showing.
_GENERIC_CLASS = {"car model", "automobile model", "product model", "motor vehicle", "road vehicle",
                  "vehicle", "automobile", "car", "auto racing", "sport", "racing", "racing event",
                  "recurring sporting event", "sport event", "team sport"}
# A discipline tag names the branch of motorsport, not the series or the sport. Wikidata's own names are
# long and overlapping, so they are folded here: "endurance racing", "sports car racing" and "automobile
# endurance racing" are all the same tag, and a race typed by several of them must still show one.
_DISCIPLINE_NAME = {
    "rallying": "Rally", "rally": "Rally", "rally raid": "Rally raid", "rallycross": "Rallycross",
    "endurance racing": "Endurance", "automobile endurance racing": "Endurance", "endurance race": "Endurance",
    "sports car racing": "Endurance", "sports prototype racing": "Endurance", "gt racing": "Endurance",
    "le mans prototype racing": "Endurance", "sports car": "Endurance", "gt car": "Endurance",
    "motorsport": "Motorsport", "auto racing": "Motorsport", "motor racing": "Motorsport",
    "motorcycle racing": "Motorcycle", "motorcycle sport": "Motorcycle", "motorcycle speedway": "Motorcycle",
    "kart racing": "Karting", "kartsport": "Karting", "grand prix motor racing": "Grand Prix", "formula racing": "Open wheel",
    "single-seater racing": "Open wheel", "open-wheel racing": "Open wheel", "formula one": "Formula One",
    "grand prix motorcycle racing": "Motorcycle", "motorcycle road racing": "Road racing",
    "stock car racing": "Stock car", "circle track racing": "Stock car", "nascar": "Stock car",
    "drag racing": "Drag racing", "motocross": "Motocross", "off-road racing": "Off-road",
    "automobile racing series": "Racing series", "one-make racing": "One-make", "hill climb": "Hill climb",
    "time trial": "Time trial",
}


def _disciplines(names: list[str]) -> list[str]:
    """Distinct discipline tags for a race or series ("Endurance", "Rally"), each shown once."""
    out: list[str] = []
    for raw in names:
        key = raw.casefold()
        name = _DISCIPLINE_NAME.get(key)
        if name is None and key.endswith(" race"):
            name = _DISCIPLINE_NAME.get(key[:-5].strip())  # "rally raid race" -> "Rally raid"
        # An unmapped label is kept only if it names something concrete; "… racing", "… series" and
        # "… championship" are containers, and the reader already knows the series from the title.
        if name is None:
            name = raw if key not in _GENERIC_CLASS and not key.endswith((" racing", " series", " championship")) else ""
        low = name.casefold()
        if not name or low in {n.casefold() for n in out} or any(low in n.casefold() for n in out):
            continue  # already covered, or a longer label we accepted says the same thing
        out.append(name)
    if len(out) > 1 and "Motorsport" in out:
        out.remove("Motorsport")              # the branch says it; the umbrella adds nothing
    return out[:3]


def _units_built(entity: dict) -> str:
    """P1092 as a plain count ("12,000"), never Wikidata's trailing "unit"."""
    value = _first(entity, "P1092")
    return _compact(float(value["amount"])) if isinstance(value, dict) and value.get("amount") else ""


# Manufacturer marks, bundled in templates/motorsport_icons.html by the name Simple Icons publishes them
# under. Only an exact match earns a badge: showing the wrong badge is worse than showing none.
CAR_LOGO = {"ferrari": "ferrari", "porsche": "porsche", "mclaren": "mclaren", "aston martin": "astonmartin",
            "bmw": "bmw", "audi": "audi", "volkswagen": "volkswagen",
            "fiat": "fiat", "renault": "renault", "peugeot": "peugeot", "citroën": "citroen", "citroen": "citroen",
            "seat": "seat", "škoda": "skoda", "skoda": "skoda", "dacia": "dacia", "lamborghini": "lamborghini",
            "bugatti": "bugatti", "bentley": "bentley", "rolls-royce": "rollsroyce", "rolls royce": "rollsroyce",
            "toyota": "toyota", "honda": "honda", "nissan": "nissan", "mazda": "mazda", "subaru": "subaru",
            "suzuki": "suzuki", "hyundai": "hyundai", "kia": "kia", "ford": "ford", "chevrolet": "chevrolet",
            "cadillac": "cadillac", "tesla": "tesla", "volvo": "volvo", "koenigsegg": "koenigsegg",
            "red bull racing": "redbull", "ducati": "ducati"}


def _drivetrain(entity: dict, labels: dict[str, str]) -> str:
    """How the wheels are driven, from the drivetrain class Wikidata hangs off the model (P670)."""
    for qid in _ids(entity, "P670", 4):
        name = labels.get(qid, "")
        if name.casefold() in _DRIVETRAIN:
            return name
    return ""


_DRIVETRAIN = {"front-wheel drive", "rear-wheel drive", "all-wheel drive", "four-wheel drive",
              "front-engine, front-wheel-drive", "front-engine, rear-wheel-drive",
              "rear mid-engine, rear-wheel drive"}


def _iso_for(name: str) -> str:
    """The ISO 3166-1 alpha-2 code for a country name, for a tile's flag."""
    return {"usa": "us", "united states": "us", "united states of america": "us",
            "united kingdom": "gb", "uk": "gb", "great britain": "gb",
            "england": "gb", "scotland": "gb", "wales": "gb", "northern ireland": "gb",
            "kingdom of the netherlands": "nl", "netherlands": "nl", "holland": "nl",
            "czech republic": "cz", "czechia": "cz", "south korea": "kr", "korea": "kr",
            "republic of korea": "kr", "bosnia and herzegovina": "ba", "czech": "cz",
            "ivory coast": "ci", "côte d'ivoire": "ci", "new zealand": "nz",
            "united arab emirates": "ae", "uae": "ae", "saudi arabia": "sa", "türkiye": "tr", "turkey": "tr",
            "italy": "it", "germany": "de", "france": "fr",
            "japan": "jp", "australia": "au", "brazil": "br", "canada": "ca", "mexico": "mx",
            "belgium": "be", "austria": "at", "switzerland": "ch", "spain": "es", "sweden": "se",
            "india": "in", "china": "cn", "russia": "ru", "portugal": "pt", "poland": "pl",
            "hungary": "hu", "argentina": "ar", "chile": "cl", "singapore": "sg", "bahrain": "bh",
            "qatar": "qa", "kuwait": "kw", "oman": "om", "monaco": "mc", "belarus": "by"}.get(
        (name or "").strip().casefold(), "")


def _iso_flag(name: str, country_ids: list[str], iso: str) -> str:
    """A tile's flag, from the panel's own ISO lookup when it has one and the name table otherwise."""
    return flag_url(iso) if iso else flag_url(_iso_for(name))


# ------------------------------------------------------------ panel build

async def _entity_by_title(client: httpx.AsyncClient, title: str) -> dict:
    """The Wikidata item of the English article with this exact title, asked alongside the Wikipedia
    lookup so a direct hit costs one round trip instead of two. Only a guess: lookup() checks it is
    the same item the page names, and asks by id when it isn't."""
    try:
        data = await _get(client, WD_API, action="wbgetentities", sites="enwiki", titles=title, normalize=1,
                          props="claims|descriptions|labels", languages="en|mul", format="json")
        return next((ent for ent in data.get("entities", {}).values() if isinstance(ent, dict) and "id" in ent), {})
    except Exception:  # noqa: BLE001 - the lookup by id below covers any failure here
        return {}


async def _iso_code(client: httpx.AsyncClient, qid: str) -> str:
    if qid not in _country_iso:
        try:
            claims = await _get(client, WD_API, action="wbgetclaims", entity=qid, property="P297", format="json")
        except (httpx.HTTPError, ValueError):
            return ""  # just no flag on the tile
        _country_iso[qid] = next((c["mainsnak"]["datavalue"]["value"] for c in claims.get("claims", {}).get("P297", [])
                                  if c.get("mainsnak", {}).get("snaktype") == "value"), "")
    return _country_iso[qid]


async def _nothing() -> str:
    return ""


async def lookup(client: httpx.AsyncClient, candidate: str, *, fresh: bool = False) -> dict | None:
    key = candidate.lower()
    if not fresh and (hit := _entity_cache.get(key)) is not None:
        return hit or None
    task = _pending.get(key)
    if fresh or task is None or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.ensure_future(_lookup(client, candidate, key))
        _pending[key] = task
        task.add_done_callback(lambda done: _pending.pop(key, None) if _pending.get(key) is done else None)
    # Shielded: a page that stops waiting must not cancel a lookup another search shares.
    return await asyncio.shield(task)


async def _lookup(client: httpx.AsyncClient, candidate: str, key: str) -> dict | None:
    (page, corrected), guess = await asyncio.gather(_find_page(client, candidate), _entity_by_title(client, candidate))
    if not page:
        _entity_cache.put(key, {})
        return None
    qid = page["pageprops"]["wikibase_item"]
    entity = guess if guess.get("id") == qid else (await _wbget(client, [qid], "claims|descriptions|labels")).get(qid, {})
    kind = _kind(entity)
    # A rookie's Wikidata often says "racing driver" months before it says "Formula One driver"; if they
    # are in this season's F1 standings (one Jolpica table, cached an hour), they get the F1 panel anyway.
    if kind == "person" and RACING_DRIVER & set(_ids(entity, "P106", 40)):
        from archer.sources import f1
        try:
            if (hit := await f1.find(client, page["title"])) and hit[0] == "driver":
                kind = "f1driver"
        except Exception:  # noqa: BLE001 - Jolpica down just means the athlete panel
            pass

    ref_props = ["P19", "P20", "P106", "P26", "P22", "P25", "P57", "P161", "P136", "P449", "P50", "P123",
                 "P495", "P740", "P264", "P527", "P112", "P169", "P159", "P17", "P6", "P141", "P171",
                 "P27", "P175", "P31", "P54", "P102", "P39", "P413", "P69", "P641", "P800", "P108", "P36",
                 "P38", "P37", "P35", "P1995", "P780", "P2176", "P1060", "P828", "P84", "P131", "P1435",
                 "P178", "P275", "P277", "P400", "P306", "P3966", "P837", "P140", "P1269", "P30", "P1441", "P170",
                 "P176", "P1716", "P155", "P156", "P1366", "P462", "P880", "P186",
                 "P404", "P408", "P179", "P852", "P908", "P86",
                 # Motorsport: top speed, units built, class, champion, the series itself, team management
                 # and a driver's first race, plus who designed a car, where it is built, how it drives,
                 # how big its engine is, who owns a team and where a race is actually held.
                 "P2052", "P1092", "P279", "P1346", "P1344", "P1037", "P2089", "P2670", "P287", "P1071",
                 "P276", "P670", "P2109", "P127", "P186",
                 # Clubs and leagues: league, home ground, head coach, chairperson, organiser.
                 "P118", "P115", "P286", "P488", "P664"]
    refs = {i for p in ref_props for i in _ids(entity, p)}
    refs |= {i for p in ("P54", "P39", "P108", "P6", "P35") for i in _current_ids(entity, p)}
    if kind == "game":
        refs |= {score["by"] for score in _review_scores(entity)}
    for p in ("P2048", "P2047", "P2142", "P2130", "P2046", "P2044", "P1082", "P1128", "P2139", "P2218",
              "P2067", "P2101", "P2102", "P2054", "P1174", "P2049", "P2610", "P2284", "P2928",
              "P2052", "P1092", "P2053", "P1114", "P2109", "P2043"):
        if (v := _first(entity, p)) and isinstance(v, dict) and "unit" in v:
            refs.add(v["unit"].rsplit("/", 1)[-1])
    # The country's ISO code (for a flag on its tile) comes alongside the labels, not after them.
    country_ids = _ids(entity, "P17", 1) or _ids(entity, "P495", 1)
    label_ents, iso = await asyncio.gather(_wbget(client, sorted(r for r in refs if r.startswith("Q")), "labels"),
                                           _iso_code(client, country_ids[0]) if country_ids else _nothing())
    labels = {k: _label(v, k) for k, v in label_ents.items()}

    def names(prop: str, limit: int = 3) -> str:
        return ", ".join(labels.get(i, i) for i in _ids(entity, prop, limit))

    def name_list(prop: str, limit: int = 8) -> list[str]:
        return [labels.get(i, i) for i in _ids(entity, prop, limit)]

    def current(prop: str, limit: int = 1) -> str:
        return ", ".join(labels.get(i, i) for i in _current_ids(entity, prop, limit))

    def amount(prop: str) -> str:
        return _fmt_amount(_first(entity, prop), labels)

    def string(prop: str) -> str:
        value = _first(entity, prop)
        return value if isinstance(value, str) else ""

    tiles: list[dict] = []
    tags: list[str] = []
    facts: list[tuple[str, str]] = []
    specs: list[dict] = []  # the motorsport bento, which is not capped at five the way tiles are

    def tile(label: str, value: str, sub: str = "") -> None:
        if value:
            tiles.append({"label": label, "value": value, "sub": sub, "icon": TILE_ICONS.get(label, "info"), "flag": ""})

    def spec(label: str, value: str, icon: str = "info", sub: str = "", flag: str = "", logo: str = "",
             live: str = "") -> None:
        """One cell of the motorsport bento. A cell with no value is dropped unless it is a live one,
        which arrives later and is rendered as a skeleton so the grid never jumps."""
        if value or live:
            specs.append({"label": label, "value": value, "sub": sub, "icon": icon,
                          "flag": flag, "logo": logo, "live": live})

    def fact(label: str, value: str) -> None:
        if value:
            facts.append((label, value))

    occupations = set(_ids(entity, "P106", 20))
    works_role = None

    subkind = ""
    product = None
    game = None
    car = None
    race = None
    f1 = None
    circuit = None
    club = None
    league = None
    esports = None
    if kind == "person":
        born_txt, _, born = _fmt_time(_first(entity, "P569"))
        died_txt, _, died = _fmt_time(_first(entity, "P570"))
        age = _age(born, died)
        if died_txt:
            tile("Died", died_txt, f"aged {age}" if age is not None else "")
        elif age is not None:
            tile("Age", f"{age} years", born_txt)
        # Subtype tiles first (Google varies the panel by what the person is known for).
        if occupations & ATHLETE or _first(entity, "P54"):
            subkind = "athlete"
            tile("Team", current("P54"))
            tile("Position", names("P413", 1) or names("P641", 1))
            number = _first(entity, "P1618")
            tile("Number", number if isinstance(number, str) else "")
        elif occupations & POLITICIAN or (_first(entity, "P39") and _first(entity, "P102")):
            subkind = "politician"
            # Prefer the office the description names ("Prime Minister of India since 2014")
            # over whichever open term started last (a newer Lok Sabha term).
            offices = [labels.get(i, i) for i in _current_ids(entity, "P39", 6)]
            blurb = (page.get("description") or "").lower()
            tile("Office", next((o for o in offices if o.lower() in blurb), offices[0] if offices else ""))
            tile("Party", current("P102") or names("P102", 1))
        elif occupations & EXECUTIVE:
            subkind = "executive"
            tile("Organisation", current("P108") or names("P108", 1))
            tile("Net worth", amount("P2218"))
        elif occupations & WRITER and not occupations & ACTOR:
            subkind = "writer"
            tile("Notable work", names("P800", 2))
        tile("Born in", names("P19", 1))
        parents = ", ".join(x for x in (names("P25", 1), names("P22", 1)) if x)
        tile("Parents", parents)
        tile("Spouse", names("P26", 2))
        tile("Height", amount("P2048"))
        tile("Occupation", names("P106", 2))
        fact("Born", born_txt + (f", {names('P19', 1)}" if names("P19", 1) else ""))
        fact("Died", died_txt)
        fact("Occupation", names("P106", 5))
        fact("Citizenship", names("P27", 2))
        fact("Team", names("P54", 1))
        fact("Position", names("P413", 2))
        fact("Party", names("P102", 1))
        fact("Position held", names("P39", 2))
        fact("Education", names("P69", 2))
        if occupations & ACTOR:
            works_role = "cast"
        elif occupations & MUSICIAN:
            works_role = "performer"
        elif occupations & WRITER:
            works_role = "author"
    elif kind == "film":
        rel_txt, rel_year, _ = _fmt_time(_first(entity, "P577"))
        tile("Released", str(rel_year or ""), rel_txt)
        tile("Runtime", _fmt_amount(_first(entity, "P2047"), labels))
        tile("Director", names("P57", 2))
        fact("Box office", _fmt_amount(_first(entity, "P2142"), labels))
        tile("Genre", names("P136", 2))
        fact("Budget", _fmt_amount(_first(entity, "P2130"), labels))
        fact("Country", names("P495", 2))
    elif kind == "tv":
        start_txt, start_y, _ = _fmt_time(_first(entity, "P580"))
        _, end_y, _ = _fmt_time(_first(entity, "P582"))
        tile("Aired", f"{start_y}–{end_y or 'present'}" if start_y else "", start_txt)
        tile("Seasons", _fmt_amount(_first(entity, "P2437"), labels))
        tile("Episodes", _fmt_amount(_first(entity, "P1113"), labels))
        fact("Network", names("P449", 2))
        tile("Genre", names("P136", 2))
    elif kind == "book":
        pub_txt, pub_y, _ = _fmt_time(_first(entity, "P577"))
        tile("Author", names("P50", 2))
        tile("Published", str(pub_y or ""), pub_txt)
        tile("Pages", _fmt_amount(_first(entity, "P1104"), labels))
        tile("Genre", names("P136", 2))
        fact("Publisher", names("P123", 2))
    elif kind == "music":
        if set(_ids(entity, "P31", 20)) & PLAYABLE:
            _, rel_y, _ = _fmt_time(_first(entity, "P577"))
            tile("Released", str(rel_y or ""))
            tile("Artist", names("P175", 2))
            tile("Genre", names("P136", 2))
            tile("Label", names("P264", 2))
        else:
            formed_txt, formed_y, _ = _fmt_time(_first(entity, "P571"))
            tile("Formed", str(formed_y or ""), names("P740", 1) or names("P495", 1))
            tile("Genre", names("P136", 2))
            tile("Label", names("P264", 2))
            tile("Members", names("P527", 4))
            works_role = "performer"
    elif kind == "org":
        found_txt, found_y, _ = _fmt_time(_first(entity, "P571"))
        tile("Founded", str(found_y or ""), found_txt)
        tile("CEO", names("P169", 1))
        tile("Headquarters", names("P159", 1))
        employees = _first(entity, "P1128")
        tile("Employees", _compact(float(employees["amount"])) if employees else "")
        tile("Revenue", _fmt_amount(_first(entity, "P2139"), labels))
        fact("Founders", names("P112", 5))
        fact("Country", names("P17", 1))
    elif kind == "place":
        tile("Country", names("P17", 1))
        pop = _first(entity, "P1082")
        tile("Population", _compact(float(pop["amount"])) if pop else "")
        tile("Area", _fmt_amount(_first(entity, "P2046"), labels))
        tile("Elevation", _fmt_amount(_first(entity, "P2044"), labels))
        tile("Leader", names("P6", 1))
        found_txt, _, _ = _fmt_time(_first(entity, "P571"))
        fact("Founded", found_txt)
    elif kind == "species":
        tile("Scientific name", str(_first(entity, "P225") or ""))
        tile("Conservation", names("P141", 1))
        tile("Parent taxon", names("P171", 1))
    elif kind == "country":
        tile("Capital", names("P36", 1))
        pop = _first(entity, "P1082")
        tile("Population", _compact(float(pop["amount"])) if pop else "")
        tile("Currency", names("P38", 1))
        tile("Head of government", current("P6") or current("P35"))
        fact("Head of state", current("P35"))
        fact("Official languages", names("P37", 4))
        fact("Area", amount("P2046"))
        fact("Calling code", string("P474"))
        fact("Continent", names("P30", 1))
    elif kind == "chemical":
        symbol, number = string("P246"), _first(entity, "P1086")
        if symbol:
            tile("Symbol", symbol, f"Atomic number {float(number['amount']):.0f}" if number else "")
        tile("Formula", string("P274").translate(_SUBSCRIPT))
        tile("Molar mass" if not symbol else "Atomic mass", amount("P2067"))
        tile("Melting point", amount("P2101"))
        tile("Boiling point", amount("P2102"))
        tile("Density", amount("P2054"))
    elif kind == "disease":
        tile("Symptoms", names("P780", 4))
        tile("Specialty", names("P1995", 2))
        tile("Spread by", names("P1060", 2))
        tile("Treatment", names("P2176", 3))
        fact("Cause", names("P828", 3))
        fact("ICD-10", string("P494"))
    elif kind == "software":
        first_txt, first_y, _ = _fmt_time(_first(entity, "P571") or _first(entity, "P577"))
        tile("Developer", names("P178", 2))
        tile("First released", str(first_y or ""), first_txt)
        tile("Latest version", string("P348"))
        tile("License", names("P275", 2))
        fact("Written in", names("P277", 3))
        fact("Platform", names("P400", 3) or names("P306", 3))
        fact("Paradigm", names("P3966", 3))
    elif kind == "game":
        released = sorted((v for v in (c["value"] for c in _claims(entity, "P577")) if isinstance(v, dict) and v.get("time")),
                          key=lambda v: v["time"])  # the first release, not a later port
        rel_txt, rel_y, _ = _fmt_time(released[0] if released else None)
        tile("Released", str(rel_y or ""), rel_txt)
        tile("Developer", names("P178", 2))
        tile("Publisher", names("P123", 2))
        tile("Genre", names("P136", 2))
        tile("Game modes", ", ".join(m.replace(" video game", "") for m in name_list("P404", 3)))
        if names("P179", 1).casefold() != page["title"].casefold():
            tile("Series", names("P179", 1))
        tile("Units sold", _units(entity))
        fact("Director", names("P57", 2))
        fact("Composer", names("P86", 2))
        fact("Engine", names("P408", 1))
        platforms = list(dict.fromkeys(_PLATFORM_SHORT.get(n, n) for n in name_list("P400", 12)))
        fact("Platforms", ", ".join(platforms))
        ratings = [labels.get(i, "") for i in _ids(entity, "P908", 1)] +                   ["ESRB " + labels[i] for i in _ids(entity, "P852", 1) if labels.get(i)]
        game = {
            "steam": _digits(string("P1733")),
            "trailer": next((v for v in (c["value"] for c in _claims(entity, "P1651"))
                             if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", v)), ""),
            "platforms": platforms,
            "stores": _game_stores(entity, name_list("P400", 12), page["title"]),
            # Aggregators first: they summarise everyone else.
            "scores": sorted(({"outlet": labels[s["by"]], "score": s["score"]} for s in _review_scores(entity) if labels.get(s["by"])),
                             key=lambda s: s["outlet"] not in ("Metacritic", "OpenCritic"))[:4],
            "ratings": [r for r in ratings if r],
            "previous": names("P155", 1), "next": names("P156", 1),
            "released": rel_txt, "year": rel_y,
        }
    elif kind == "character":
        tile("Appears in", names("P1441", 2))
        tile("Played by", names("P175", 2))
        tile("Created by", names("P170", 2))
        tile("Occupation", names("P106", 2))
        tags = name_list("P31", 3)
    elif kind == "holiday":
        tile("Date", names("P837", 1))
        tile("Religion", names("P140", 1))
        tile("Observed in", names("P17", 2) or names("P1269", 2))
        tags = name_list("P31", 3)  # "fictional human, television character" reads as tags, not a fact
    elif kind == "landmark":
        built_txt, built_y, _ = _fmt_time(_first(entity, "P571") or _first(entity, "P1619"))
        tile("Location", names("P131", 1) or names("P17", 1))
        tile("Built", str(built_y or ""), built_txt)
        tile("Architect", names("P84", 2))
        tile("Height", amount("P2048") or amount("P2044"))
        tile("Heritage", names("P1435", 1))
        tile("Visitors / year", amount("P1174"))
        fact("Country", names("P17", 1))
    elif kind == "product":
        rel_txt, rel_y, _ = _fmt_time(_first(entity, "P577") or _first(entity, "P571"))
        brand = names("P1716", 1) or names("P176", 1)
        tile("Brand", brand)
        tile("Released", str(rel_y or ""), rel_txt)
        tile("Operating system", names("P306", 1))
        tile("Processor", names("P880", 1))
        tile("Weight", amount("P2067"))
        tile("Launch price", amount("P2284"))
        fact("Manufacturer", names("P176", 2))
        fact("Memory", amount("P2928"))
        fact("Material", names("P186", 3))
        dims = [amount(p) for p in ("P2048", "P2049", "P2610")]
        if all(dims):
            fact("Dimensions", " × ".join(dims))
        # The product panel (sources/products.py) draws these itself.
        product = {"brand": brand, "manufacturer": names("P176", 1), "released": rel_txt, "year": rel_y,
                   "previous": names("P155", 1), "next": names("P156", 1) or names("P1366", 1),
                   "colors": name_list("P462", 10), "category": names("P31", 1)}
    elif kind == "car":
        # A car model is usually named "X <model>", so the range reads better than a single year.
        start_txt, start_y, _ = _fmt_time(_first(entity, "P571"))
        end_txt, end_y, _ = _fmt_time(_first(entity, "P576"))
        # A run reads as years: "1963–1973", or "From 1963" while Wikidata has no end.
        span = f"{start_y}–{end_y}" if start_y and end_y else f"From {start_y}" if start_y else (f"Until {end_y}" if end_y else "")
        brand = names("P1716", 1) or names("P176", 1)
        maker = names("P176", 1)
        units = _units_built(entity)
        speed = amount("P2052")
        # P279 walks up the class tree ("sports car", "supercar"); the generic ancestors say nothing.
        styles = [s for s in name_list("P279", 8) if s.casefold() not in _GENERIC_CLASS][:3]
        home = names("P17", 1) or names("P495", 1)
        tile("Body style", ", ".join(styles))
        tile("Brand", brand)
        tile("Production", span)
        tile("Units built", units)
        tile("Top speed", speed)
        fact("Manufacturer", maker)
        fact("Class", ", ".join(styles[1:]) or (styles[0] if styles else ""))
        fact("Predecessor", names("P155", 1))
        fact("Successor", names("P156", 1))
        fact("Series", names("P361", 2))
        fact("Country", home)
        fact("Designed by", names("P287", 2))
        fact("Assembled", names("P1071", 1) or maker)
        # The bento carries the whole panel, so it gets every figure worth having rather than the five
        # the generic entity cap allows. Live values are filled from the API once the page is up.
        spec("Body style", ", ".join(styles), "car")
        spec("Brand", brand, "tag", logo=CAR_LOGO.get(brand.casefold(), ""))
        spec("Production", span, "calendar")
        spec("Units built", units, "chart")
        spec("Top speed", speed, "gauge")
        spec("Layout", _drivetrain(entity, labels), "layers")
        spec("Power", amount("P2109"), "bolt")
        spec("Class", ", ".join(styles[1:]), "tag")
        spec("Series", names("P361", 1), "layers")
        spec("Predecessor", names("P155", 1), "swap")
        spec("Successor", names("P156", 1), "swap")
        spec("Country", home, "globe", flag=_iso_flag(home, country_ids, iso))
        spec("Designed by", names("P287", 1), "pen")
        spec("Assembled", names("P1071", 1), "pin")
        spec("Weight", amount("P2067"), "scale")
        spec("Length", amount("P2049"), "ruler")
        spec("Width", amount("P2053"), "ruler")
        spec("Height", amount("P2048"), "ruler")
        spec("Wheelbase", amount("P1114"), "ruler")
        spec("Material", names("P186", 2), "package")
        car = {"brand": brand, "manufacturer": maker, "styles": styles, "production": span,
               "year": start_y, "units": units, "top_speed": speed, "logo": CAR_LOGO.get(brand.casefold(), ""),
               "previous": names("P155", 1), "next": names("P156", 1), "series": names("P361", 1)}
        tags = list(styles)
    elif kind == "race":
        held_txt, held_y, _ = _fmt_time(_first(entity, "P571"))
        end_txt, end_y, _ = _fmt_time(_first(entity, "P576"))
        grand_prix = "Q108886259" in set(_ids(entity, "P31", 20)) or " Grand Prix" in page["title"]
        disciplines = _disciplines((["formula one"] if grand_prix else []) + name_list("P279", 4) + name_list("P641", 6))
        sport = names("P641", 2)
        # "Endurance racing" next to the Endurance tag says the same thing twice.
        sport = "" if set(_disciplines([sport])) <= set(disciplines) else sport
        winners = name_list("P1346", 2)
        home = names("P17", 1)
        tile("Sport", sport)
        tile("First held", str(held_y or ""), held_txt)
        tile("Last held", str(end_y or ""), end_txt)
        tile("Country", home)
        tile("Champion", winners[0] if winners else "")
        fact("Discipline", ", ".join(disciplines))
        fact("Part of", names("P361", 2))
        fact("Circuit", names("P276", 1) or names("P131", 1))
        fact("Record holder", ", ".join(winners[1:]))
        fact("Next race", names("P156", 1))
        # P527 on a series is usually its sub-series; only the parts named after a year are editions.
        parts = [labels.get(i, "") for i in _ids(entity, "P527", 20)][-6:]
        spec("Discipline", ", ".join(disciplines), "trophy")
        spec("Sport", sport, "globe")
        spec("First held", str(held_y or ""), "calendar", sub=held_txt)
        spec("Last held", str(end_y or ""), "calendar", sub=end_txt)
        spec("Country", home, "globe", flag=_iso_flag(home, country_ids, iso))
        spec("Champion", winners[0] if winners else "", "award")
        spec("Circuit", names("P276", 1) or names("P131", 1), "map")
        spec("Part of", names("P361", 1), "layers")
        spec("Record holder", ", ".join(winners[1:]), "trophy")
        race = {"disciplines": disciplines, "sport": sport, "champion": winners[0] if winners else "",
                "grand_prix": grand_prix,
                "country": home, "first_held": held_txt, "year": held_y,
                "part_of": names("P361", 1),
                "editions": [p for p in parts if re.search(r"\b(19|20)\d{2}\b", p)]}
        tags = list(disciplines)
    elif kind in ("f1driver", "f1team"):
        # Wikidata holds who this is; the season, the standings and the career totals come from Jolpica
        # once the page is up (/api/f1), drawn into cards that are already the right size.
        driver = kind == "f1driver"
        born_txt, _, born = _fmt_time(_first(entity, "P569"))
        founded_txt, founded_y, _ = _fmt_time(_first(entity, "P571"))
        # Only a seat with no end date is a current one; a retired driver has none.
        seated = any("P582" not in c.get("qualifiers", {}) for c in entity.get("claims", {}).get("P54", []))
        seat = current("P54", 1) if seated else ""
        home = names("P27", 1) if driver else names("P17", 1)
        if driver:
            teams = list(reversed(name_list("P54", 10)))
            tile("Team", seat)
            tile("Born", born_txt, f"aged {_age(born, None)}" if born else "")
            tile("Nationality", home)
            tile("Series", "Formula One")
            fact("Constructor", seat)
            fact("Born", born_txt)
            fact("Nationality", names("P27", 2))
            fact("Teams", ", ".join(teams))
            spec("Born", born_txt, "calendar", sub=f"aged {_age(born, None)}" if born else "")
            spec("Birthplace", names("P19", 1), "pin")
            spec("Nationality", home, "globe", flag=flag_url(_iso_for(home)))
            spec("Height", amount("P2048"), "ruler")
        else:
            teams = []
            tile("Constructor", "Formula One")
            tile("Country", home)
            tile("Founded", founded_txt)
            fact("Base", names("P159", 1))
            fact("Team principal", names("P1037", 2) or names("P169", 2))
            fact("Owner", names("P127", 1))
            spec("Base", names("P159", 1), "pin")
            spec("Country", home, "globe", flag=_iso_flag(home, country_ids, iso))
            spec("Founded", str(founded_y or ""), "calendar", sub=founded_txt)
            spec("Team principal", names("P1037", 1) or names("P169", 1), "user")
            spec("Owner", names("P127", 1), "building")
        f1 = {"role": "driver" if driver else "team", "team": seat if driver else "", "teams": teams,
              "nationality": home, "born": born_txt if driver else "", "founded": founded_txt if not driver else ""}
        tags = ["Formula One"]
    elif kind == "circuit":
        # The outline, the races held and the recent winners come from /api/f1/circuit after load; this is
        # what the circuit is: where, how long, since when.
        opened_txt, opened_y, _ = _fmt_time(_first(entity, "P1619") or _first(entity, "P571"))
        home = names("P17", 1)
        point = _first(entity, "P625")
        tile("Country", home)
        tile("Location", names("P131", 1))
        tile("Length", amount("P2043"))
        tile("Opened", str(opened_y or ""), opened_txt)
        fact("Owner", names("P127", 1))
        fact("Designed by", names("P84", 2))
        spec("Location", names("P131", 1), "pin")
        spec("Country", home, "globe", flag=_iso_flag(home, country_ids, iso))
        spec("Length", amount("P2043"), "ruler")
        spec("Opened", str(opened_y or ""), "calendar", sub=opened_txt)
        spec("Capacity", amount("P1083"), "users")
        spec("Owner", names("P127", 1), "building")
        spec("Designed by", names("P84", 1), "pen")
        circuit = {"lat": str(point["latitude"]) if isinstance(point, dict) else "",
                   "lon": str(point["longitude"]) if isinstance(point, dict) else "",
                   "street": "Q926439" in set(_ids(entity, "P31", 20))}
        tags = ["Street circuit"] if circuit["street"] else []
    elif kind == "club":
        # Who the club is; the table, results, fixtures and squad come from /api/sport/club after load.
        from archer.sources import sports
        founded_txt, founded_y, _ = _fmt_time(_first(entity, "P571"))
        sport_q = next(iter(_ids(entity, "P641", 1)), "")
        league_q = next(iter(_current_ids(entity, "P118", 1)), "")
        home = names("P17", 1)
        ground = names("P115", 1)
        coach = current("P286") or names("P286", 1)
        nickname = next((c["mainsnak"]["datavalue"]["value"].get("text", "") for c in entity.get("claims", {}).get("P1449", [])
                         if c.get("mainsnak", {}).get("snaktype") == "value"
                         and c["mainsnak"]["datavalue"]["value"].get("language") == "en"), "")
        tile("League", labels.get(league_q, ""))
        tile("Founded", str(founded_y or ""), founded_txt)
        tile("Ground", ground)
        tile("Country", home)
        tile("Head coach", coach)
        fact("Nickname", nickname)
        fact("Owner", names("P127", 2))
        fact("Chairman", names("P488", 1))
        spec("Ground", ground, "landmark")
        spec("Founded", str(founded_y or ""), "calendar", sub=founded_txt)
        spec("Country", home, "globe", flag=_iso_flag(home, country_ids, iso))
        spec("Head coach", coach, "user")
        spec("Nickname", nickname, "tag")
        spec("Owner", names("P127", 1), "building")
        club = {"sport": sports.SPORTS.get(sport_q, ""), "sport_qid": sport_q, "league": league_q,
                "league_name": labels.get(league_q, ""), "country": country_ids[0] if country_ids else "",
                "nickname": nickname, "ground": ground,
                "live": league_q in sports.LEAGUES or bool(sports.SPORTS.get(sport_q))}
        tags = [x for x in (sports.SPORT_NAMES.get(club["sport"]) or names("P641", 1).capitalize(), labels.get(league_q, "")) if x]
    elif kind == "league":
        from archer.sources import sports
        founded_txt, founded_y, _ = _fmt_time(_first(entity, "P571"))
        sport_q = next(iter(_ids(entity, "P641", 1)), "")
        champions = name_list("P1346", 40)
        teams = amount("P1132")
        tile("Founded", str(founded_y or ""), founded_txt)
        tile("Country", names("P17", 1))
        tile("Teams", teams)
        tile("Champion", champions[-1] if champions else "")
        fact("Organiser", names("P664", 1))
        spec("Founded", str(founded_y or ""), "calendar", sub=founded_txt)
        spec("Country", names("P17", 1), "globe", flag=_iso_flag(names("P17", 1), country_ids, iso))
        spec("Teams", teams, "users")
        spec("Champion", champions[-1] if champions else "", "trophy")
        mapped = sports.LEAGUES.get(qid)
        league = {"qid": qid, "sport": mapped[0] if mapped else sports.SPORTS.get(sport_q, ""), "live": bool(mapped),
                  "short": mapped[2] if mapped else page["title"]}
        tags = [x for x in (sports.SPORT_NAMES.get(league["sport"]) or names("P641", 1).capitalize(),) if x]
    elif kind in ("esteam", "esplayer"):
        # Who they are; what they are doing comes from VLR.gg (Valorant) or Liquipedia after load.
        from archer.sources import esports as es
        games = es.games_in(page.get("description") or "", page.get("extract") or "")
        player = kind == "esplayer"
        if not games and await es.on_vlr(client, page["title"], "player" if player else "team"):
            games = ["valorant"]
        born_txt, _, born = _fmt_time(_first(entity, "P569"))
        founded_txt, founded_y, _ = _fmt_time(_first(entity, "P571"))
        game = es.GAME_INFO.get(games[0]) if games else None
        if player:
            nat = names("P27", 1)
            tile("Team", current("P54"))
            tile("Game", game["name"] if game else "")
            tile("Age", f"{_age(born, None)} years" if born else "", born_txt)
            tile("Nationality", nat)
            spec("Born", born_txt, "calendar", sub=f"aged {_age(born, None)}" if born else "")
            spec("Nationality", nat, "globe", flag=flag_url(_iso_for(nat)))
            fact("Teams", ", ".join(reversed(name_list("P54", 8))))
        else:
            tile("Founded", str(founded_y or ""), founded_txt)
            tile("Country", names("P17", 1))
            tile("Based in", names("P159", 1))
            tile("Games", ", ".join(es.GAME_INFO[g]["name"] for g in games[:3]))
            spec("Founded", str(founded_y or ""), "calendar", sub=founded_txt)
            spec("Based in", names("P159", 1), "pin")
            spec("Country", names("P17", 1), "globe", flag=_iso_flag(names("P17", 1), country_ids, iso))
            fact("Owner", names("P127", 2))
            fact("CEO", names("P169", 1))
        esports = {"role": "player" if player else "team", "game": games[0] if games else "",
                   "games": [es.GAME_INFO[g] for g in games], "color": game["color"] if game else "",
                   "handle": es.handle(page["title"]), "team": current("P54") if player else ""}
        tags = [es.GAME_INFO[g]["name"] for g in games[:4]] or ["Esports"]
    else:
        inc_txt, inc_y, _ = _fmt_time(_first(entity, "P571"))
        tile("Since", str(inc_y or ""), inc_txt)
        tile("Country", names("P17", 1) or names("P495", 1))
        tags = name_list("P31", 3)  # "fictional human, television character" reads as tags, not a fact

    links = []
    if site := _first(entity, "P856"):
        links.append({"label": "Official site", "url": site, "kind": "site"})
    for prop, (label, template) in SOCIAL.items():
        if (handle := _first(entity, prop)) and isinstance(handle, str):
            if prop == "P345":
                template = "https://www.imdb.com/" + ("name/{}/" if handle.startswith("nm") else "title/{}/")
            links.append({"label": label, "url": template.format(handle), "kind": label.lower()})
    if (repo := string("P1324")).startswith("https://"):
        links.append({"label": "Source code", "url": repo, "kind": "source"})
    links.append({"label": "Wikipedia", "url": page.get("fullurl", ""), "kind": "wikipedia"})

    coords = _first(entity, "P625")
    # PageImages can omit non-free posters and some TV art. Wikidata's P18 is a
    # useful second source, while the browser can still try the thumbnail if an
    # original is present but refuses to load.
    original = page.get("original") or page.get("thumbnail") or {}
    image = original.get("source", "")
    thumbnail = page.get("thumbnail", {}).get("source", "")
    if not image and (file_name := _first(entity, "P18")) and isinstance(file_name, str):
        image = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(file_name)}?width=800"
    width, height = original.get("width", 1) or 1, original.get("height", 1) or 1
    cast = name_list("P161", 10) if kind in ("film", "tv") else []

    # Flags: a country leads with its own (Wikidata P41, the official image on Commons);
    # "Country" tiles elsewhere get a small one via that country's ISO code.
    flag = ""
    if kind == "country":
        if (flag_file := string("P41")):
            flag = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(flag_file)}?width=800"
        else:
            flag = flag_url(string("P297"))
    country_tile = next((t for t in tiles[:5] if t["label"] in ("Country", "Observed in")), None)
    if country_tile and country_ids:
        country_tile["flag"] = flag_url(iso)

    player = _music_player(entity, kind) if kind == "music" else None
    watch = _watch_data(entity, kind, page["title"]) if kind in ("film", "tv") else None

    icon, kind_label = KIND_META.get(kind, KIND_META["thing"])
    if race and race["grand_prix"]:
        icon, kind_label = "chequered", "Grand Prix"
    shown = tiles[:5]
    # Tiles that don't fit the bento move into the expandable About section, so nothing is lost.
    overflow = [(t["label"], t["value"] + (f" ({t['sub']})" if t["sub"] else "")) for t in tiles[5:]]

    out = {
        "kind": kind,
        "subkind": subkind,
        "badge": {"icon": icon, "label": kind_label + (f" · {SUBKIND_LABEL[subkind]}" if subkind else "")},
        "flag": flag,
        "tags": tags,
        "player": player,
        "watch": watch,
        "product": product,
        "game": game,
        "car": car,
        "race": race,
        "f1": f1,
        "circuit": circuit,
        "club": club,
        "league": league,
        "esports": esports,
        "specs": specs,
        "qid": qid,
        "title": page["title"],
        "description": page.get("description") or entity.get("descriptions", {}).get("en", {}).get("value", ""),
        "extract": page.get("extract", ""),
        "image": image if image.startswith("https://") else "",
        "image_fallback": thumbnail if thumbnail.startswith("https://") and thumbnail != image else "",
        # Poster layout only for genuinely tall art; wide title cards would be cropped to nonsense.
        "image_tall": height >= width * 1.1,
        # Logos (usually SVG wordmarks) are shown whole on a light card instead of cropped.
        "image_logo": ".svg" in image.split("?")[0].lower(),
        "corrected": corrected,
        "wiki_url": page.get("fullurl", ""),
        "tiles": shown,
        "facts": _unique_facts(facts + overflow, skip={t["label"] for t in shown})[:12],
        "links": links,
        "cast": cast,
        "works_role": works_role,
        # TMDB person id: lets the filmography use TMDB's posters when a key is configured.
        "tmdb_person": string("P4985") if works_role == "cast" and re.fullmatch(r"\d{1,9}", string("P4985")) else "",
        "coords": {"lat": coords["latitude"], "lon": coords["longitude"]} if coords else None,
    }
    return _entity_cache.put(key, out)


# ---------------------------------------------------------- works (lazy)

_WORK_QUERIES = {
    "cast": "?work wdt:P161 wd:{qid} . VALUES ?type {{ wd:Q11424 wd:Q5398426 wd:Q1259759 wd:Q581714 wd:Q24869 }} ?work wdt:P31 ?type .",
    "author": "?work wdt:P50 wd:{qid} . VALUES ?type {{ wd:Q7725634 wd:Q571 wd:Q47461344 wd:Q8261 }} ?work wdt:P31 ?type .",
    "performer": "?work wdt:P175 wd:{qid} . VALUES ?type {{ wd:Q482994 wd:Q208569 }} ?work wdt:P31 ?type .",
}
_TYPE_LABEL = {"Q11424": "Film", "Q24869": "Film", "Q5398426": "TV series", "Q1259759": "Miniseries",
               "Q581714": "Animated series", "Q482994": "Album", "Q208569": "Studio album"}


async def works(client: httpx.AsyncClient, qid: str, role: str) -> list[dict]:
    if role not in _WORK_QUERIES or not re.fullmatch(r"Q\d+", qid):
        return []
    key = (qid, role)
    if (hit := _works_cache.get(key)) is not None:
        return hit
    where = _WORK_QUERIES[role].format(qid=qid)
    sparql = f"""SELECT ?work ?workLabel ?type (MIN(?d) AS ?date) WHERE {{
      {where}
      OPTIONAL {{ ?work wdt:P577 ?d . }}
      OPTIONAL {{ ?work wdt:P580 ?d . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }} GROUP BY ?work ?workLabel ?type ORDER BY DESC(?date) LIMIT 40"""
    resp = await client.get(WDQS, params={"query": sparql, "format": "json"}, timeout=8)
    resp.raise_for_status()
    seen, out = set(), []
    for row in resp.json()["results"]["bindings"]:
        title = row["workLabel"]["value"]
        if title in seen or re.fullmatch(r"Q\d+", title):
            continue
        seen.add(title)
        year = row.get("date", {}).get("value", "")[:4]
        out.append({"title": title, "year": year,
                    "type": _TYPE_LABEL.get(row["type"]["value"].rsplit("/", 1)[-1], "Book" if role == "author" else "")})
    return _works_cache.put(key, out[:20])


_thumb_cache = TTLCache(ttl=86400, size=4096)


async def thumbs(client: httpx.AsyncClient, titles: list[str], size: int = 160, *, nonfree: bool = False) -> dict[str, str]:
    """Wikipedia page images for exact article titles, as {requested title: image URL}.

    Exact titles only, never a search, so nothing can pick up someone else's photo;
    disambiguation pages and pages without a Wikidata item get nothing. Batched 50 titles
    per request. nonfree=True also returns fair-use art (film posters, book covers).
    """
    out, missing = {}, []
    for title in dict.fromkeys(t.strip() for t in titles if t and t.strip()):
        hit = _thumb_cache.get((title.lower(), size, nonfree))
        if hit is None:
            missing.append(title)
        elif hit:
            out[title] = hit
    for i in range(0, len(missing), 50):
        chunk = missing[i:i + 50]
        params = {"action": "query", "format": "json", "formatversion": 2, "redirects": 1, "titles": "|".join(chunk),
                  "prop": "pageimages|pageprops", "piprop": "thumbnail", "pithumbsize": size, "pilimit": 50,
                  "ppprop": "wikibase_item|disambiguation"}
        if nonfree:
            params["pilicense"] = "any"
        data = (await _get(client, WP_API, **params)).get("query", {})
        # Follow "xander berkeley" -> "Xander Berkeley" -> redirect target back to what was asked.
        alias = {t: t for t in chunk}
        for step in data.get("normalized", []) + data.get("redirects", []):
            for asked, current in alias.items():
                if current == step.get("from"):
                    alias[asked] = step.get("to")
        pages = {p.get("title"): p for p in data.get("pages", [])}
        for asked, final in alias.items():
            page = pages.get(final, {})
            source = page.get("thumbnail", {}).get("source", "") if _usable(page) else ""
            source = source if source.startswith("https://") else ""
            _thumb_cache.put((asked.lower(), size, nonfree), source)
            if source:
                out[asked] = source
    return out


async def portraits(client: httpx.AsyncClient, titles: list[str]) -> dict[str, str]:
    """Small thumbnails beside AI key facts ("Portrayed by: Xander Berkeley")."""
    return await thumbs(client, titles, 160)
