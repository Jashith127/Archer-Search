"""Entity panels from Wikipedia (match, description, image) and Wikidata (typed facts)."""

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

# P31 (instance of) -> panel kind
KINDS = {
    "character": {"Q15632617", "Q95074", "Q15773347", "Q15773317", "Q3658341", "Q1114461", "Q80447738"},
    "person": {"Q5"},
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
    # Product models (smartphone, computer, console, car…); anything with a manufacturer or brand also counts.
    "product": {"Q19723451", "Q10929058", "Q55990535", "Q3231690"},
    "place": {"Q515", "Q1549591", "Q5119", "Q1637706", "Q200250", "Q7930989", "Q123705", "Q486972", "Q3957"},
}
ACTOR = {"Q33999", "Q10800557", "Q10798782", "Q2405480", "Q2259451", "Q948329"}
WRITER = {"Q36180", "Q6625963", "Q482980", "Q49757", "Q1930187", "Q4853732", "Q28389"}
MUSICIAN = {"Q177220", "Q639669", "Q753110", "Q488205", "Q130857", "Q183945", "Q855091", "Q2252262"}
ATHLETE = {"Q2066131", "Q937857", "Q12299841", "Q3665646", "Q10833314", "Q10843402", "Q11513337",
           "Q10871364", "Q19204627", "Q13141064", "Q11338576", "Q10349745", "Q4009406", "Q13382576", "Q14089670"}
POLITICIAN = {"Q82955", "Q193391", "Q372436", "Q30461"}
EXECUTIVE = {"Q43845", "Q484876", "Q131524", "Q2961975", "Q1979607"}

CURRENCY_UNITS = {"Q4917": "$", "Q80524": "₹", "Q4916": "€", "Q25224": "£", "Q8146": "¥"}
UNIT_SUFFIX = {"Q11573": "m", "Q174728": "cm", "Q11570": "kg", "Q712226": "km²", "Q828224": "km",
               "Q7727": "min", "Q11574": "s", "Q35852": "ha", "Q232291": "mi²", "Q11579": "K",
               "Q25267": "°C", "Q42289": "°F", "Q483261": "u", "Q13147228": "g/cm³", "Q844211": "kg/m³",
               "Q41803": "g", "Q3241121": "g/mol", "Q174789": "mm"}
_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

# Badge shown above every entity panel: kind -> (icon, label). Icons live in templates/ui_icons.html.
KIND_META = {
    "person": ("user", "Person"), "film": ("film", "Film"), "tv": ("tv", "TV series"), "book": ("book", "Book"),
    "music": ("music", "Music"), "country": ("flag", "Country"), "org": ("building", "Organisation"),
    "species": ("leaf", "Species"), "chemical": ("flask", "Chemical"), "disease": ("virus", "Health condition"),
    "software": ("code", "Software"), "holiday": ("sparkle", "Holiday"), "landmark": ("landmark", "Landmark"),
    "place": ("pin", "Place"), "product": ("package", "Product"), "thing": ("info", "Topic"), "character": ("user", "Fictional character"),
}
SUBKIND_LABEL = {"athlete": "Athlete", "politician": "Politician", "executive": "Business leader", "writer": "Writer"}
TILE_ICONS = {
    "Age": "calendar", "Died": "calendar", "Born in": "pin", "Parents": "users", "Spouse": "heart",
    "Height": "ruler", "Occupation": "briefcase", "Team": "shield", "Position": "target", "Number": "hash",
    "Office": "landmark", "Party": "flag", "Organisation": "building", "Net worth": "coins",
    "Notable work": "book", "Released": "calendar", "Runtime": "clock", "Director": "film",
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
    pages = (await _get(client, WP_API, **_WP_PROPS, titles=candidate)).get("query", {}).get("pages", [])
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
    for i in range(0, len(ids), 50):
        data = await _get(client, WD_API, action="wbgetentities", ids="|".join(ids[i:i + 50]),
                          props=props, languages="en|mul", format="json")
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
    for kind, qids in KINDS.items():
        if types & qids:
            return kind
    # Many items are typed by a subclass we don't list (Python is a "multi-paradigm
    # programming language", not "programming language"), so fall back to tell-tale properties.
    claims = entity.get("claims", {})
    for kind, props in (("software", ("P348", "P277", "P1324")), ("chemical", ("P274", "P246")),
                        ("disease", ("P780", "P1995", "P494")), ("country", ("P36", "P38")),
                        ("film", ("P57", "P2047")), ("book", ("P50",)), ("species", ("P225",))):
        if sum(p in claims for p in props) >= (2 if kind in ("country", "film") else 1):
            return kind
    if "P176" in claims or "P1716" in claims:
        return "product"
    if _first(entity, "P625"):
        return "place"
    return "thing"


# ------------------------------------------------------------ panel build

async def lookup(client: httpx.AsyncClient, candidate: str, *, fresh: bool = False) -> dict | None:
    key = candidate.lower()
    if not fresh and (hit := _entity_cache.get(key)) is not None:
        return hit or None
    page, corrected = await _find_page(client, candidate)
    if not page:
        _entity_cache.put(key, {})
        return None
    qid = page["pageprops"]["wikibase_item"]
    entity = (await _wbget(client, [qid], "claims|descriptions|labels")).get(qid, {})
    kind = _kind(entity)

    ref_props = ["P19", "P20", "P106", "P26", "P22", "P25", "P57", "P161", "P136", "P449", "P50", "P123",
                 "P495", "P740", "P264", "P527", "P112", "P169", "P159", "P17", "P6", "P141", "P171",
                 "P27", "P175", "P31", "P54", "P102", "P39", "P413", "P69", "P641", "P800", "P108", "P36",
                 "P38", "P37", "P35", "P1995", "P780", "P2176", "P1060", "P828", "P84", "P131", "P1435",
                 "P178", "P275", "P277", "P400", "P306", "P3966", "P837", "P140", "P1269", "P30", "P1441", "P170",
                 "P176", "P1716", "P155", "P156", "P1366", "P462", "P880", "P186"]
    refs = {i for p in ref_props for i in _ids(entity, p)}
    refs |= {i for p in ("P54", "P39", "P108", "P6", "P35") for i in _current_ids(entity, p)}
    for p in ("P2048", "P2047", "P2142", "P2130", "P2046", "P2044", "P1082", "P1128", "P2139", "P2218",
              "P2067", "P2101", "P2102", "P2054", "P1174", "P2049", "P2610", "P2284", "P2928"):
        if (v := _first(entity, p)) and isinstance(v, dict) and "unit" in v:
            refs.add(v["unit"].rsplit("/", 1)[-1])
    label_ents = await _wbget(client, sorted(r for r in refs if r.startswith("Q")), "labels")
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

    def tile(label: str, value: str, sub: str = "") -> None:
        if value:
            tiles.append({"label": label, "value": value, "sub": sub, "icon": TILE_ICONS.get(label, "info"), "flag": ""})

    def fact(label: str, value: str) -> None:
        if value:
            facts.append((label, value))

    occupations = set(_ids(entity, "P106", 20))
    works_role = None

    subkind = ""
    product = None
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
    country_ids = _ids(entity, "P17", 1) or _ids(entity, "P495", 1)
    if country_tile and country_ids:
        claims = await _get(client, WD_API, action="wbgetclaims", entity=country_ids[0], property="P297", format="json")
        iso = next((c["mainsnak"]["datavalue"]["value"] for c in claims.get("claims", {}).get("P297", [])
                    if c.get("mainsnak", {}).get("snaktype") == "value"), "")
        country_tile["flag"] = flag_url(iso)

    player = _music_player(entity, kind) if kind == "music" else None
    watch = _watch_data(entity, kind, page["title"]) if kind in ("film", "tv") else None

    icon, kind_label = KIND_META.get(kind, KIND_META["thing"])
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
