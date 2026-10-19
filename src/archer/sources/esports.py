"""Esports teams and players: VLR.gg for Valorant, Liquipedia for every other game.

Wikidata says who a team or player is; this says what they are doing. Valorant gets the most, from
VLR.gg's team and player pages (it has no API, so the HTML is read, tolerantly): the roster and staff,
recent series with every map's score and both agent compositions, the map pool with attack and defence
round rates, the team's rating over time, event placements and winnings; for a player, the agent pool
with rating, ACS, K:D, KAST and ADR per agent, and the last maps played with their agent and line.

Other games come from Liquipedia's MediaWiki API, within its terms: a descriptive User-Agent, gzip, one
request every two seconds, and a rendered (parse) request at most every thirty, each cached a day. The
infobox gives the facts (region, team, role, status, titles); the active roster is the one parse.

Every function returns {} when the source is down or doesn't know the name; the panel keeps Wikidata's.
"""

import asyncio
import html
import re
import time
import unicodedata

import httpx

from archer.sources import TTLCache

VLR = "https://www.vlr.gg"
LIQUIPEDIA = "https://liquipedia.net/{wiki}/api.php"
HEADERS = {"User-Agent": "Archer (self-hosted personal search)", "Accept-Encoding": "gzip"}
_cache = TTLCache(ttl=1800, size=256)
DAY = 86400

# Games an esports article can be about, in the order they are looked for, with Liquipedia's wiki name and
# the accent the panel takes. Valorant is first: when an organisation fields a Valorant team, that's the
# panel it gets, because it is the one with the most to show.
GAMES = (
    ("valorant", "Valorant", "valorant", "#ff4655", ("valorant",)),
    ("lol", "League of Legends", "leagueoflegends", "#c89b3c", ("league of legends", "lol esports")),
    ("cs", "Counter-Strike", "counterstrike", "#f2a900", ("counter-strike", "cs:go", "cs2", "csgo")),
    ("dota2", "Dota 2", "dota2", "#e33b2e", ("dota 2", "dota")),
    ("overwatch", "Overwatch", "overwatch", "#f99e1a", ("overwatch",)),
    ("rocketleague", "Rocket League", "rocketleague", "#1a8cff", ("rocket league",)),
    ("r6", "Rainbow Six Siege", "rainbowsix", "#4ea6e8", ("rainbow six",)),
    ("apex", "Apex Legends", "apexlegends", "#da292a", ("apex legends",)),
    ("fortnite", "Fortnite", "fortnite", "#9d4dbb", ("fortnite",)),
    ("cod", "Call of Duty", "callofduty", "#7fbf3f", ("call of duty",)),
    ("mlbb", "Mobile Legends", "mobilelegends", "#3b82f6", ("mobile legends",)),
    ("pubg", "PUBG", "pubg", "#f2a900", ("pubg", "playerunknown")),
    ("sc2", "StarCraft II", "starcraft2", "#2fa3ff", ("starcraft",)),
    ("fgc", "Fighting games", "fighters", "#e74c3c", ("street fighter", "tekken", "fighting game")),
)
GAME_INFO = {key: {"key": key, "name": name, "wiki": wiki, "color": color} for key, name, wiki, color, _ in GAMES}


def games_in(*texts: str) -> list[str]:
    """Game keys an article mentions, Valorant first, then in the order the text names them."""
    text = " ".join(t for t in texts if t).casefold()
    found = []
    for key, _, _, _, words in GAMES:
        at = min((text.find(w) for w in words if w in text), default=-1)
        if at >= 0:
            found.append((0 if key == "valorant" else 1, at, key))
    return [key for _, _, key in sorted(found)]


def handle(title: str) -> str:
    """The name a scene knows: "Faker (gamer)" is Faker, "Sentinels (esports)" is Sentinels."""
    return re.sub(r"\s*\((?:esports?|gamer|player|e-sports|team|organization|organisation|professional gamer)\)\s*$",
                  "", title or "", flags=re.I).strip()


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", plain.casefold())


def _text(fragment: str) -> str:
    """Visible text of an HTML fragment, whitespace collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


# VLR's images (team and event logos, player photos, map art) live on owcdn.net, which refuses a browser's
# request that carries no Referer, and the results page sends none. Archer fetches them itself instead and
# serves them from IMG_PROXY, so they load and the browser never contacts the CDN.
OWCDN = "https://owcdn.net/img/"
IMG_PROXY = "/api/vlr/img/"
IMG_NAME = re.compile(r"[0-9a-f]{8,24}\.(?:png|jpe?g|webp|gif|svg)")
_images = TTLCache(ttl=7 * DAY, size=400)


def _src(url: str) -> str:
    """VLR writes images as //owcdn.net/... or /img/...; the page needs https and nothing that is a placeholder.
    owcdn.net images are handed out as IMG_PROXY paths."""
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = VLR + url
    if not url.startswith("https://") or "/tmp/vlr.png" in url or "/ph/sil.png" in url:
        return ""
    if url.startswith(OWCDN) and IMG_NAME.fullmatch(url[len(OWCDN):]):
        return IMG_PROXY + url[len(OWCDN):]
    return url


_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
          "svg": "image/svg+xml"}


async def image(client: httpx.AsyncClient, name: str) -> tuple[bytes, str] | None:
    """One owcdn.net image by file name (only ever a hex name), kept a week. None when it can't be had."""
    if not IMG_NAME.fullmatch(name or ""):
        return None
    if (hit := _images.get(name)) is not None:
        return hit
    try:
        resp = await client.get(OWCDN + name, headers=HEADERS, timeout=10)
    except httpx.HTTPError:
        return None
    kind = _TYPES[name.rsplit(".", 1)[1]]
    if resp.status_code != 200 or len(resp.content) > 2_000_000:
        return None
    return _images.put(name, (resp.content, kind))


def _flag(css: str) -> str:
    """VLR's "flag mod-us" -> https://flagcdn.com/us.svg (its own codes are ISO 3166-1 alpha-2)."""
    m = re.search(r"mod-([a-z]{2})\b", css or "")
    return f"https://flagcdn.com/{m.group(1)}.svg" if m and m.group(1) != "un" else ""  # flagcdn has the EU's


def _num(text: str):
    text = (text or "").replace(",", "").replace("%", "").replace("$", "").strip()
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _section(page: str, label: str, stop: str = 'wf-label mod-large') -> str:
    """The HTML from a "wf-label" heading to the next one."""
    m = re.search(r'wf-label mod-large"[^>]*>\s*' + r"\s+".join(map(re.escape, label.split())) + r"\s*<", page)
    if not m:
        return ""
    end = page.find(stop, m.end())
    return page[m.end(): end if end > 0 else len(page)]


_gates: dict[int, asyncio.Semaphore] = {}


def _gate() -> asyncio.Semaphore:
    loop = id(asyncio.get_running_loop())
    if loop not in _gates:
        _gates[loop] = asyncio.Semaphore(2)
    return _gates[loop]


async def _page(client: httpx.AsyncClient, path: str, ttl: float = 1800, **params) -> str:
    key = "vlr:" + path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    if (hit := _cache.get(key)) is not None:
        return hit
    async with _gate():
        resp = await client.get(VLR + path, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return _cache.put(key, resp.text, ttl=ttl)


# ------------------------------------------------------------ Valorant: VLR.gg

AGENT_ROLES = {
    "duelist": ("jett", "reyna", "raze", "phoenix", "yoru", "neon", "iso", "waylay"),
    "initiator": ("sova", "breach", "skye", "kayo", "fade", "gekko", "tejo"),
    "controller": ("brimstone", "viper", "omen", "astra", "harbor", "clove"),
    "sentinel": ("killjoy", "cypher", "sage", "chamber", "deadlock", "vyse", "veto"),
}
_ROLE_OF = {agent: role for role, agents in AGENT_ROLES.items() for agent in agents}


def agent(name: str) -> dict:
    key = re.sub(r"[^a-z]", "", (name or "").casefold())
    return {"key": key, "name": key[:1].upper() + key[1:] if key != "kayo" else "KAY/O",
            "role": _ROLE_OF.get(key, "flex"), "icon": f"{VLR}/img/vlr/game/agents/{key}.png" if key else ""}


async def _search(client: httpx.AsyncClient, name: str, kind: str, exact: bool = False) -> tuple[str, str] | None:
    """(id, slug) of the team or player VLR has under exactly this name, or the only near match."""
    page = await _page(client, "/search/", ttl=DAY, q=name, type=kind + "s")
    hits = re.findall(r'href="/search/r/' + kind + r'/(\d+)/idx".*?search-item-title">\s*(.*?)\s*</div>', page, re.S)
    wanted = _fold(name)
    named = [(ident, _text(title)) for ident, title in hits]
    same = [ident for ident, title in named if _fold(title) == wanted]
    if same:
        return same[0], ""
    if exact:
        return None
    close = [ident for ident, title in named[:3] if wanted and (_fold(title).startswith(wanted) or wanted.startswith(_fold(title)))]
    return (close[0], "") if len(close) == 1 and kind == "team" else None


def _match_items(block: str) -> list[dict]:
    """Series from a team or player page's match list, with each map's score and agents."""
    out = []
    # One chunk per series: its card (up to the first </a>) and then its maps, up to the next series.
    for chunk in re.split(r'(?=<a href="/\d+/[^"]+" class="wf-card fc-flex m-item">)', block)[1:]:
        m = re.match(r'<a href="(/\d+/[^"]+)" class="wf-card fc-flex m-item">(.*?)</a>(.*)', chunk, re.S)
        if not m:
            continue
        href, head, games = m.group(1), m.group(2), m.group(3)
        event = re.search(r'm-item-event[^>]*>\s*<div[^>]*>(.*?)</div>(.*?)</div>', head, re.S)
        names = [_text(x) for x in re.findall(r'm-item-team-name">(.*?)</span>', head, re.S)]
        tags = [_text(x) for x in re.findall(r'm-item-team-tag">(.*?)</span>', head, re.S)]
        logos = [_src(x) for x in re.findall(r'm-item-logo[^"]*">\s*<img src="([^"]*)"', head)]
        result = re.search(r'm-item-result (mod-\w+)[^>]*>\s*<span>(.*?)</span>\s*<div>:</div>\s*<span>(.*?)</span>', head, re.S)
        date = re.search(r'm-item-date">\s*<div>\s*(.*?)\s*</div>\s*(.*?)\s*</div>', head, re.S)
        maps = []
        for g in re.finditer(r'm-item-games-item[^"]*">(.*?)</a>', games, re.S):
            body = g.group(1)
            score = re.search(r'class="map">\s*(.*?)\s*</div>\s*<div class="score">\s*(\d+)<span>-</span>(\d+)', body, re.S)
            if not score:
                continue
            outcome = re.search(r'm-item-games-result fc-flex (mod-\w+)', body)
            sides = re.split(r'm-item-games-result', body)
            ours = re.findall(r'/agents/(\w+)\.png', sides[0])
            theirs = re.findall(r'/agents/(\w+)\.png', sides[1]) if len(sides) > 1 else []
            line = {}
            if "pg-stat" in body:  # a player's own map: their agent, rating and K/D/A
                rating = re.search(r'title="Rating[^"]*">.*?pg-v">\s*([\d.]+)', body, re.S)
                kda = re.search(r'K/D/A</div>\s*<div class="pg-v">(\d+)<span[^>]*>/</span>(\d+)<span[^>]*>/</span>(\d+)', body)
                line = {"rating": _num(rating.group(1)) if rating else None,
                        "k": int(kda.group(1)) if kda else None, "d": int(kda.group(2)) if kda else None,
                        "a": int(kda.group(3)) if kda else None}
                theirs = []
            maps.append({"map": _text(score.group(1)), "us": int(score.group(2)), "them": int(score.group(3)),
                         "won": bool(outcome and outcome.group(1) == "mod-win"),
                         "agents": [agent(a) for a in ours], "against": [agent(a) for a in theirs], **line})
        if len(names) < 2:
            continue
        us, them = (_num(result.group(2)), _num(result.group(3))) if result else (None, None)
        state = (result.group(1).removeprefix("mod-") if result else "")
        out.append({"url": VLR + href, "event": _text(event.group(1)) if event else "",
                    "stage": _text(event.group(2)) if event else "",
                    "team": names[0], "team_tag": tags[0] if tags else "", "team_logo": logos[0] if logos else "",
                    "opp": names[1], "opp_tag": tags[1] if len(tags) > 1 else "", "opp_logo": logos[1] if len(logos) > 1 else "",
                    "us": us, "them": them,
                    "outcome": "W" if state == "win" else "L" if state == "loss" else "",
                    "date": (date.group(1).strip().replace("/", "-") if date else ""),
                    "time": (_text(date.group(2)) if date else ""), "maps": maps})
    return out


def _placements(block: str) -> tuple[str, list[dict]]:
    winnings = re.search(r'Total Winnings\s*</div>\s*<span[^>]*>\s*(.*?)\s*</span>', block, re.S)
    out = []
    for m in re.finditer(r'<a [^>]*href="(/event/[^"]+)"[^>]*class="wf-module-item (?:team|player)-event-item[^"]*"[^>]*>(.*?)</a>'
                         r'|<a class="wf-module-item (?:team|player)-event-item[^"]*" href="(/event/[^"]+)"[^>]*>(.*?)</a>', block, re.S):
        href, body = m.group(1) or m.group(3), m.group(2) or m.group(4)
        name = re.search(r'text-of"[^>]*>\s*(.*?)\s*</div>', body, re.S)
        series = [_text(s) for s in re.findall(r'(?:team-event-item-series|ge-text-light)">\s*(.*?)</span>', body, re.S)]
        prize = re.search(r'font-weight: 700;">\s*(\$[\d,]+)\s*</span>', body)
        year = re.search(r'<div>\s*(\d{4})\s*</div>\s*$', body.strip())
        # "Play-Ins – 7th–8th", then "Group Stage – 7th–8th": the last stage listed is where the run ended.
        stage, _, placing = (series[-1] if series else "").rpartition(" – ")
        best = re.search(r"(\d+)(?:st|nd|rd|th)", placing)
        out.append({"event": _text(name.group(1)) if name else "", "stage": stage.strip(),
                    "place": placing, "rank": int(best.group(1)) if best else None,
                    "prize": prize.group(1) if prize else "", "year": year.group(1) if year else "",
                    "url": VLR + href})
    return (_text(winnings.group(1)) if winnings else ""), out


def _rating_history(page: str) -> list[dict]:
    """The team's rating over time, from the tooltips VLR draws its chart with: oldest first."""
    out = []
    # One chunk per tooltip; each is a handful of short divs, so the patterns stay bounded ([^<]).
    for chunk in page.split('<div class="tip ')[1:]:
        chunk = chunk[:1500]
        m = re.match(r'[^>]*>\s*<div>([^<]*)</div>\s*<div class="tip-title">(.*?)</div>\s*<div>([^<]*)</div>', chunk, re.S)
        change = re.search(r'result-(win|loss)">\s*(?:Win|Loss)\s*([+-]\d+)', chunk)
        rating = re.search(r'&ndash;\s*(\d{3,4})\s*vs', chunk)
        if not m or not rating:
            continue
        out.append({"date": _text(m.group(1)), "opp": _text(m.group(2)).removeprefix("vs.").strip(),
                    "event": _text(m.group(3)), "won": bool(change and change.group(1) == "win"),
                    "change": int(change.group(2)) if change else 0,
                    "rating": int(rating.group(1)) + (int(change.group(2)) if change else 0)})  # the tip shows it before
    return out


def _map_pool(page: str) -> list[dict]:
    table = re.search(r'<table class="wf-table mod-team-maps".*?<tbody>(.*?)</tbody>', page, re.S)
    if not table:
        return []
    out = []
    for row in re.split(r"<tr[ >]", table.group(1))[1:]:
        name = re.search(r'mod-first mod-highlight"[^>]*>\s*([A-Za-z ]+?)\s*\((\d+)\)\s*(?:<img src="([^"]*)")?', row)
        if not name:
            continue
        cells = [_text(c) for c in re.findall(r'<div class="mod-first[^"]*"[^>]*>(.*?)</div>', row, re.S)][1:]
        nums = [c for c in cells if re.fullmatch(r"[\d.]+%?", c)]
        if len(nums) < 11:
            continue
        out.append({"map": name.group(1).strip(), "played": int(name.group(2)), "image": _src(name.group(3) or ""),
                    "win": _num(nums[0]), "w": _num(nums[1]), "l": _num(nums[2]),
                    "atk_first": _num(nums[3]), "def_first": _num(nums[4]),
                    "atk": _num(nums[5]), "atk_w": _num(nums[6]), "atk_l": _num(nums[7]),
                    "def": _num(nums[8]), "def_w": _num(nums[9]), "def_l": _num(nums[10])})
    return sorted(out, key=lambda m: -m["played"])


def parse_team(page: str, stats: str = "") -> dict:
    name = re.search(r'<h1 class="wf-title"[^>]*>\s*(.*?)\s*</h1>', page, re.S)
    if not name:
        return {}
    tag = re.search(r'team-header-tag">\s*(.*?)\s*</h2>', page, re.S)
    logo = re.search(r'team-header-logo">\s*<div>\s*<img src="([^"]*)"', page)
    country = re.search(r'team-header-country">\s*<i class="([^"]*)"></i>\s*(.*?)\s*</div>', page, re.S)
    links = [{"url": u, "label": _text(t)} for u, t in
             re.findall(r'<a href="(https://[^"]+)" target="_blank"[^>]*>\s*(.*?)\s*</a>', page.split("team-header-links", 1)[-1].split("</div>", 1)[0])
             if _text(t)]
    roster_block = _section(page, "Current Roster")
    players, staff = [], []
    target = players
    for part in re.split(r'(wf-module-label"[^>]*>\s*(?:players|staff)\s*</div>)', roster_block):
        if "wf-module-label" in part:
            target = staff if "staff" in part else players
            continue
        for m in re.finditer(r'<a href="/player/(\d+)/([^"]+)"[^>]*>(.*?)</a>', part, re.S):
            body = m.group(3)
            alias = re.search(r'name-alias">\s*(?:<i class="([^"]*)"[^>]*></i>)?\s*(.*?)\s*(?:<i class="fa fa-star|</div>)', body, re.S)
            real = re.search(r'name-real">\s*(.*?)\s*</div>', body, re.S)
            role = re.search(r'name-role">\s*(.*?)\s*</div>', body, re.S)
            photo = re.search(r'roster-item-img">\s*<img src="([^"]*)"', body)
            target.append({"alias": _text(alias.group(2)) if alias else m.group(2), "name": _text(real.group(1)) if real else "",
                           "flag": _flag(alias.group(1) if alias else ""), "photo": _src(photo.group(1)) if photo else "",
                           "captain": "fa-star" in body, "role": _text(role.group(1)) if role else "",
                           "url": f"{VLR}/player/{m.group(1)}/{m.group(2)}"})
    # The rating card holds one block per roster "core"; the active one is the current five.
    core = page.split('team-core-block mod-active', 1)[-1].split('team-core-block ', 1)[0] if "team-core-block mod-active" in page else ""
    rank = re.search(r'mod-rank">.*?(?:rank-num[^"]*|rating-num)">\s*([^<]*?)\s*</div>', core, re.S)
    region = re.search(r'mod-rank">.*?rating-txt">\s*([^<]*?)\s*</div>', core, re.S)
    rating = re.search(r'mod-rating">.*?rating-num">\s*(\d+)', core, re.S)
    record = re.search(r'mod-streak">.*?class="win">(\d+)W</span>\s*<span class="loss">(\d+)L', core, re.S)
    upcoming = _match_items(_section(page, "Upcoming Matches"))
    winnings, placements = _placements(_section(page, "Event Placements"))
    results = _match_items(_section(page, "Recent Results"))
    return {
        "role": "team", "name": _text(name.group(1)), "tag": _text(tag.group(1)) if tag else "",
        "logo": _src(logo.group(1)) if logo else "", "country": _text(country.group(2)) if country else "",
        "flag": _flag(country.group(1)) if country else "", "links": links[:3],
        "rank": _text(rank.group(1)) if rank else "", "region": _text(region.group(1)) if region else "",
        "rating": int(rating.group(1)) if rating else None,
        "record": {"w": int(record.group(1)), "l": int(record.group(2))} if record else {},
        "players": players, "staff": staff, "results": results[:8], "upcoming": upcoming[:4],
        "history": _rating_history(core)[-60:], "winnings": winnings, "placements": placements[:10],
        "maps": _map_pool(stats) if stats else [],
    }


def _agents(page: str) -> list[dict]:
    table = re.search(r'<table class="st-table mod-agent-rows".*?<tbody>(.*?)</tbody>', page, re.S)
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", table.group(1) if table else "", re.S):
        name = re.search(r'/agents/(\w+)\.png', row)
        use = re.search(r'mod-use">\s*\((\d+)\)\s*(\d+)%', row)
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)][2:]
        if not name or not use or len(cells) < 14:
            continue
        out.append({**agent(name.group(1)), "maps": int(use.group(1)), "use": int(use.group(2)),
                    "rounds": _num(cells[0]), "rating": _num(cells[1]), "acs": _num(cells[2]), "kd": _num(cells[3]),
                    "kast": _num(cells[4]), "adr": _num(cells[5]), "kpr": _num(cells[6]), "apr": _num(cells[7]),
                    "fkfd": _num(cells[8]), "k": _num(cells[9]), "d": _num(cells[10]), "a": _num(cells[11]),
                    "fk": _num(cells[12]), "fd": _num(cells[13])})
    return out


def _teams_list(block: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<a class="wf-module-item[^"]*" href="/team/(\d+)/([^"]+)"[^>]*>(.*?)</a>', block, re.S):
        body = m.group(3)
        name = re.search(r'font-weight: 500;">\s*(.*?)\s*</div>', body, re.S)
        logo = re.search(r'<img src="([^"]*)"', body)
        notes = [_text(x) for x in re.findall(r'ge-text-light"[^>]*>(.*?)</div>', body, re.S)]
        out.append({"name": _text(name.group(1)) if name else m.group(2), "logo": _src(logo.group(1)) if logo else "",
                    "note": next((n for n in notes if n.startswith(("joined", "left"))), ""),
                    "tag": next((n for n in notes if n and not n.startswith(("joined", "left"))), ""),
                    "url": f"{VLR}/team/{m.group(1)}/{m.group(2)}"})
    return out


def _career(agents: list[dict]) -> dict:
    """Totals across every agent: rounds-weighted rating, ACS and ADR; kills over deaths; role shares."""
    rounds = sum(a["rounds"] or 0 for a in agents)
    if not rounds:
        return {}
    weighted = lambda key: round(sum((a[key] or 0) * (a["rounds"] or 0) for a in agents) / rounds, 2)  # noqa: E731
    kills, deaths = sum(a["k"] or 0 for a in agents), sum(a["d"] or 0 for a in agents)
    fk, fd = sum(a["fk"] or 0 for a in agents), sum(a["fd"] or 0 for a in agents)
    maps = sum(a["maps"] for a in agents) or 1
    roles: dict[str, int] = {}
    for a in agents:
        roles[a["role"]] = roles.get(a["role"], 0) + a["maps"]
    return {"rounds": rounds, "maps": maps, "rating": weighted("rating"), "acs": round(weighted("acs")),
            "adr": round(weighted("adr")), "kast": round(weighted("kast")), "kd": round(kills / deaths, 2) if deaths else None,
            "kills": kills, "fkfd": round(fk / fd, 2) if fd else None,
            "roles": sorted(({"role": r, "share": round(100 * n / maps)} for r, n in roles.items()), key=lambda r: -r["share"])}


def parse_player(page: str) -> dict:
    alias = re.search(r'player-header">.*?<h1 class="wf-title"[^>]*>\s*(.*?)\s*</h1>', page, re.S)
    if not alias:
        return {}
    real = re.search(r'player-real-name[^"]*">\s*(.*?)\s*</h2>', page, re.S)
    photo = re.search(r'wf-avatar mod-player".*?<img src="([^"]*)"', page, re.S)
    country = re.search(r'player-header">.*?<i class="(flag [^"]*)"[^>]*></i>\s*(.*?)\s*</div>', page, re.S)
    header = page.split("player-header", 1)[-1].split("wf-nav", 1)[0]
    links = [{"url": u, "label": _text(t)} for u, t in re.findall(r'<a [^>]*href="(https://[^"]+)" target="_blank">\s*(.*?)\s*</a>', header)]
    agents = _agents(page)
    winnings, placements = _placements(_section(page, "Event Placements"))
    current = _teams_list(_section(page, "Current Teams"))
    return {
        "role": "player", "alias": _text(alias.group(1)), "name": _text(real.group(1)) if real else "",
        "photo": _src(photo.group(1)) if photo else "", "country": _text(country.group(2)).title() if country else "",
        "flag": _flag(country.group(1)) if country else "", "links": links[:3],
        "agents": agents[:10], "career": _career(agents),
        "team": current[0] if current else None, "past": _teams_list(_section(page, "Past Teams"))[:8],
        "results": _match_items(_section(page, "Recent Results"))[:6],
        "winnings": winnings, "placements": placements[:8],
    }


async def on_vlr(client: httpx.AsyncClient, title: str, role: str) -> bool:
    """Whether VLR.gg has a team (or player) of exactly this name: the sign an organisation whose article
    names no game ("Gen.G is a professional esports organization") fields a Valorant roster."""
    try:
        return bool(await _search(client, handle(title), "player" if role == "player" else "team", exact=True))
    except (httpx.HTTPError, ValueError):
        return False


async def valorant(client: httpx.AsyncClient, title: str, role: str) -> dict:
    """A Valorant team or player from VLR.gg, or {}."""
    name = handle(title)
    kind = "player" if role == "player" else "team"
    try:
        hit = await _search(client, name, kind)
        if not hit:
            return {}
        ident = hit[0]
        if kind == "player":
            return parse_player(await _page(client, f"/player/{ident}/", timespan="all")) | {"source": f"{VLR}/player/{ident}"}
        page, stats = await asyncio.gather(_page(client, f"/team/{ident}/"),
                                           _page(client, f"/team/stats/{ident}/", ttl=6 * 3600), return_exceptions=True)
        if not isinstance(page, str):
            return {}
        return parse_team(page, stats if isinstance(stats, str) else "") | {"source": f"{VLR}/team/{ident}"}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return {}


# ------------------------------------------------------------ Valorant Champions Tour seasons

# What an event is, from its title, for the picker's groups and for choosing what to open on.
VCT_KINDS = (("champions", ("valorant champions 20", "champions 20")), ("masters", ("masters",)),
             ("gc", ("game changers",)), ("kickoff", ("kickoff", "lock//in", "lockin")), ("stage1", ("stage 1",)), ("stage2", ("stage 2",)),
             ("lcq", ("last chance",)))
VCT_KIND_NAMES = {"champions": "Champions", "masters": "Masters", "kickoff": "Kickoff", "stage1": "Stage 1",
                  "stage2": "Stage 2", "gc": "Game Changers", "lcq": "Last Chance", "other": "Other"}
# Smaller Game Changers events (monthly cash cups, third-party series) would bury the main ones.
_GC_SKIP = ("cash cup", "queens", "showmatch", "academy", "swift cup", "last chance", "promotion", "qualifier", "intercollegiate")


def _money(text: str) -> int:
    return int(re.sub(r"\D", "", text or "") or 0)


def _main_gc(event: dict) -> bool:
    low = event["name"].casefold()
    return event["kind"] == "gc" and not any(w in low for w in _GC_SKIP)


def vct_kind(name: str) -> str:
    low = (name or "").casefold()
    for kind, words in VCT_KINDS:
        if any(w in low for w in words):
            return "gc" if "game changers" in low else kind
    return "other"


def _event_items(page: str) -> list[dict]:
    out = []
    # Each card carries its own status, so the column it sits in doesn't matter.
    for m in re.finditer(r'<a class="wf-card mod-flex event-item" href="/event/(\d+)/([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        body = m.group(3)
        title = re.search(r'event-item-title">\s*(.*?)\s*</div>', body, re.S)
        status = re.search(r'event-item-desc-item-status mod-(\w+)', body)
        prize = re.search(r'mod-prize">\s*([^<]*?)\s*<div', body, re.S)
        dates = re.search(r'mod-dates">\s*([^<]*?)\s*<div', body, re.S)
        flag = re.search(r'mod-location">\s*<i class="([^"]*)"', body)
        thumb = re.search(r'event-item-thumb">\s*<img src="([^"]*)"', body)
        name = _text(title.group(1)) if title else m.group(2)
        out.append({"id": m.group(1), "slug": m.group(2), "name": name, "kind": vct_kind(name),
                    "status": status.group(1) if status else "", "prize": _text(prize.group(1)) if prize else "",
                    "dates": _text(dates.group(1)).replace("—", " – ") if dates else "",
                    "flag": _flag(flag.group(1)) if flag else "", "logo": _src(thumb.group(1)) if thumb else ""})
    return out


def _pick(events: list[dict], year: int, this_year: int, hint: str = "", region: str = "") -> str:
    """The event a search opens on: one it names; else the season's Champions once it has begun (the event
    a season is about); else a VCT event being played now; else the most recent to finish (the hub lists
    them newest first). Game Changers only when it is all there is, or when the search names it."""
    if region and (local := [e for e in events if region.casefold() in e["name"].casefold()
                                 and (not hint or e["kind"] == hint)]):
        events = local
    if hint and (named := [e for e in events if e["kind"] == hint]):
        live = [e for e in named if e["status"] == "ongoing"]
        done = [e for e in named if e["status"] == "completed"]
        return (live or done or named)[0]["id"]
    champs = [e for e in events if e["kind"] == "champions"]
    if champs and (year < this_year or champs[0]["status"] in ("ongoing", "completed")):
        return champs[0]["id"]
    if live := [e for e in events if e["status"] == "ongoing" and e["kind"] != "gc"]:
        return live[0]["id"]
    done = [e for e in events if e["status"] == "completed" and e["kind"] != "gc"] or \
        [e for e in events if e["status"] == "completed"]
    return (done or events or [{"id": ""}])[0]["id"]


async def vct_season(client: httpx.AsyncClient, year: int, this_year: int, hint: str = "", region: str = "") -> dict:
    """A VCT season: every VCT and main Game Changers event of `year`, grouped, and the one to open on."""
    try:
        pages = await asyncio.gather(_page(client, f"/vct-{year}", ttl=900 if year >= this_year else DAY),
                                     _page(client, f"/gc-{year}", ttl=900 if year >= this_year else DAY),
                                     return_exceptions=True)
    except httpx.HTTPError:
        return {}
    events, seen = [], set()
    for i, page in enumerate(pages):
        if not isinstance(page, str):
            continue
        for e in _event_items(page):
            if e["id"] in seen or i == 1 and not _main_gc(e):
                continue
            seen.add(e["id"])
            events.append(e)
    # The Game Changers hub lists dozens of splits and cups: the Championship and the biggest events stay.
    gc = [e for e in events if e["kind"] == "gc"]
    keep = {e["id"] for e in sorted(gc, key=lambda e: ("championship" not in e["name"].casefold(), -_money(e["prize"])))[:6]}
    events = [e for e in events if e["kind"] != "gc" or e["id"] in keep]
    if not events:
        return {}
    for e in events:
        # "VCT 2026: Americas Kickoff" is "Americas" under Kickoff; "Valorant Masters London 2026" is "London".
        short = re.sub(r"^(?:VCT|Valorant|Game Changers)\s*\d{4}\s*:\s*|^Valorant\s+|\s*\b\d{4}\b", "", e["name"]).strip()
        for word in ("Masters ", "Champions", "Kickoff", "Stage 1", "Stage 2", "Game Changers "):
            if e["kind"] != "gc" or word == "Game Changers ":
                short = short.replace(word, "").strip()
        e["short"] = short or VCT_KIND_NAMES.get(e["kind"], e["name"])
    order = [k for k in VCT_KIND_NAMES if any(e["kind"] == k for e in events)]
    groups = [{"kind": k, "name": VCT_KIND_NAMES[k], "events": [e for e in events if e["kind"] == k]} for k in order]
    # Show a season in the order it is played: Kickoff, Masters, stages, Champions; Game Changers after.
    play = ["kickoff", "masters", "stage1", "stage2", "lcq", "champions", "gc", "other"]
    groups.sort(key=lambda g: play.index(g["kind"]))
    return {"year": year, "groups": groups, "pick": _pick(events, year, this_year, hint, region),
            "live": any(e["status"] == "ongoing" for e in events),
            "source": f"{VLR}/vct-{year}"}


def _standings(page: str) -> list[dict]:
    # From the table to the next section heading: the rows are flat divs, so nesting can't be counted on.
    marker = "wf-ptable wf-ptable--standings"
    block = page.split(marker, 1)[-1].split("wf-label", 1)[0] if marker in page else ""
    out = []
    for row in re.split(r'<div class="row[^"]*" role="row">', block)[2:]:
        place = re.search(r'role="cell"[^>]*>\s*(\d+)<sup>\w+</sup>(?:\s*[–-]\s*(\d+)<sup>\w+</sup>)?', row)
        prize = re.findall(r'role="cell" style="justify-content: flex-end">\s*([^<]*?)\s*</div>', row)
        team = re.search(r'<a href="/team/(\d+)/([^"]+)"[^>]*>\s*<img src="([^"]*)"[^>]*>\s*<div[^>]*>\s*(.*?)<div class="ge-text-light"[^>]*>\s*(.*?)\s*</div>', row, re.S)
        if not place:
            continue
        lo, hi = int(place.group(1)), int(place.group(2) or place.group(1))
        out.append({"rank": lo, "place": f"{_ordinal(lo)}" + (f"–{_ordinal(hi)}" if hi != lo else ""),
                    "prize": _text(prize[0]) if prize else "",
                    "team": _text(team.group(4)) if team else "TBD", "country": _text(team.group(5)) if team else "",
                    "flag": _country_flag(_text(team.group(5))) if team else "",
                    "logo": _src(team.group(3)) if team else "",
                    "url": f"{VLR}/team/{team.group(1)}/{team.group(2)}" if team else ""})
    return out


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _sidebar(page: str, label: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<a class="wf-module-item[^"]*" href="(/\d+/[^"]+)">(.*?)</a>', _section(page, label), re.S):
        body = m.group(2)
        series = re.search(r'event-sidebar-matches-series[^"]*">\s*(.*?)\s*</div>', body, re.S)
        sides = re.findall(r'event-sidebar-matches-team"[^>]*>\s*<div class="name">\s*(?:<i class="([^"]*)"[^>]*></i>)?\s*<span[^>]*>\s*(.*?)\s*</span>'
                           r'\s*</div>\s*<div class="score[^"]*">\s*([^<]*?)\s*</div>\s*<div class="eta[^"]*">\s*([^<]*?)\s*</div>', body, re.S)
        if len(sides) < 2:
            continue
        a, b = sides[0], sides[1]
        sa, sb = _num(a[2]), _num(b[2])
        stage = _text(series.group(1)) if series else ""
        out.append({"url": VLR + m.group(1), "stage": stage, "final": "grand final" in stage.casefold(),
                    "a": {"name": _text(a[1]), "flag": _flag(a[0]), "score": sa},
                    "b": {"name": _text(b[1]), "flag": _flag(b[0]), "score": sb},
                    "winner": "a" if sa is not None and sb is not None and sa > sb else "b" if sa is not None and sb is not None and sb > sa else "",
                    "eta": _text(a[3]) if label != "Latest Results" else ""})
    return out


def parse_event(page: str) -> dict:
    name = re.search(r'event-header-main-title">\s*(.*?)\s*</h1>', page, re.S)
    if not name:
        return {}
    desc = re.search(r'event-header-main-desc">\s*(.*?)\s*</h2>', page, re.S)
    logo = re.search(r'event-header-thumb">.*?<img src="([^"]*)"', page, re.S)
    meta = dict((_text(k).casefold(), v) for k, v in re.findall(
        r'<div class="ge-text-light label">\s*(.*?)\s*</div>\s*<div class="value"[^>]*>(.*?)</div>', page, re.S))
    location_flag = re.search(r'<i class="(flag mod-\w+)', meta.get("location", ""))
    standings = _standings(page)
    results = _sidebar(page, "Latest Results")
    upcoming = _sidebar(page, "Upcoming Matches")
    final = next((r for r in results if r["final"]), None)
    champion = standings[0] if standings and standings[0]["rank"] == 1 and standings[0]["team"] != "TBD" else None
    if final and champion:
        won = final["a"] if final["winner"] == "a" else final["b"] if final["winner"] == "b" else None
        if won and won["name"].casefold() != champion["team"].casefold():
            final = None  # a name that doesn't line up is a different match
    # The sidebar names the finalists but draws no logos: they come from the standings, by name, with
    # the country for when there is no logo (and a flag, when the sidebar had one).
    if final:
        by_name = {s["team"].casefold(): s for s in standings}
        def known(side: dict) -> dict:
            row = by_name.get(side["name"].casefold(), {})
            return {**side, "logo": row.get("logo", ""), "country": row.get("country", ""),
                    "flag": side["flag"] or row.get("flag", "")}
        final = {**final, "a": known(final["a"]), "b": known(final["b"])}
    name_text = _text(name.group(1))
    return {"name": name_text, "kind": vct_kind(name_text), "desc": _text(desc.group(1)) if desc else "",
            "logo": _src(logo.group(1)) if logo else "",
            "dates": _text(meta.get("dates", "")), "prize": _text(meta.get("prize", "")),
            "location": _text(meta.get("location", "")), "flag": _flag(location_flag.group(1)) if location_flag else "",
            "standings": standings[:16], "champion": champion, "final": final,
            "results": results[:12], "upcoming": upcoming[:8],
            "done": bool(champion), "live": bool(upcoming) and not champion}


async def vct_event(client: httpx.AsyncClient, ident: str) -> dict:
    """One event's standings, champion, grand final and results, from its VLR.gg page."""
    if not re.fullmatch(r"\d{1,6}", ident or ""):
        return {}
    try:
        page = await _page(client, f"/event/{ident}/", ttl=600)
    except httpx.HTTPError:
        return {}
    return parse_event(page) | {"id": ident, "source": f"{VLR}/event/{ident}"}


# ------------------------------------------------------------ every other game: Liquipedia

_last: dict[str, float] = {"api": 0.0, "parse": 0.0}
_lp_lock: dict[int, asyncio.Lock] = {}


async def _liquipedia(client: httpx.AsyncClient, wiki: str, kind: str, ttl: float = DAY, **params) -> dict:
    """One Liquipedia API reply, cached a day and spaced as its terms ask. A parse that would come too soon
    after the last one is skipped (None) rather than queued: a roster is not worth a half-minute wait."""
    key = f"lp:{wiki}:" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    if (hit := _cache.get(key)) is not None:
        return hit
    loop = id(asyncio.get_running_loop())
    lock = _lp_lock.setdefault(loop, asyncio.Lock())
    async with lock:
        gap = (30.0 if kind == "parse" else 2.0) - (time.monotonic() - _last[kind])
        if gap > 0:
            if kind == "parse":
                return {}
            await asyncio.sleep(gap)
        _last[kind] = time.monotonic()
        resp = await client.get(LIQUIPEDIA.format(wiki=wiki), params={**params, "format": "json"}, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return _cache.put(key, resp.json(), ttl=ttl)


def _infobox(wikitext: str) -> dict[str, str]:
    """`|field=value` pairs of the first Infobox template, values with links and templates flattened."""
    start = wikitext.find("{{Infobox")
    if start < 0:
        return {}
    depth, i = 0, start
    while i < len(wikitext) - 1:
        pair = wikitext[i:i + 2]
        depth += pair == "{{"
        depth -= pair == "}}"
        i += 2 if pair in ("{{", "}}") else 1
        if depth == 0:
            break
    body = wikitext[start + 2:i - 2]
    fields, depth, current = {}, 0, ""
    for ch_i, ch in enumerate(body):
        if body[ch_i:ch_i + 2] in ("{{", "[["):
            depth += 1
        elif body[ch_i:ch_i + 2] in ("}}", "]]"):
            depth -= 1
        if ch == "|" and depth == 0:
            if "=" in current:
                k, v = current.split("=", 1)
                fields[k.strip()] = v.strip()
            current = ""
        else:
            current += ch
    if "=" in current:
        k, v = current.split("=", 1)
        fields[k.strip()] = v.strip()
    return fields


def _plain(value: str) -> str:
    value = re.sub(r"<ref.*?</ref>|<ref[^>]*/>", "", value or "", flags=re.S)
    value = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", value)
    value = re.sub(r"\[https?://\S+ ([^\]]+)\]", r"\1", value)
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>|'''?", " ", value)).strip(" ,")


def _titles(achievements: str) -> list[dict]:
    """World titles from a player's infobox: each line names the event and its date."""
    out = []
    for line in re.split(r"<hr\s*/?>", achievements or ""):
        event = re.search(r"\[\[([^|\]]+)\|([^\]]+)\]\]", line)
        date = re.search(r"date=(\d{4})", line)
        if event:
            out.append({"event": _plain(event.group(2)), "year": date.group(1) if date else ""})
    return out


def _country_flag(country: str) -> str:
    from archer.sources.wiki import flag_url, _iso_for
    if (country or "").casefold() == "europe":
        return "https://flagcdn.com/eu.svg"
    return flag_url(_iso_for(country)) if country else ""


def _roster(fragment: str) -> list[dict]:
    out = []
    for row in re.findall(r'<tr class="table2&#95;&#95;row--body">(.*?)</tr>', fragment, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        player = re.search(r'<a href="/[^/]+/([^"]+)" title="[^"]*">(.*?)</a>', cells[0] if cells else "")
        country = re.search(r'<span class="flag"><img alt="([^"]*)"', cells[0] if cells else "")
        if not player:
            continue
        out.append({"alias": _text(player.group(2)), "country": country.group(1) if country else "",
                    "name": _text(cells[1]) if len(cells) > 1 else "",
                    "position": _text(cells[2]) if len(cells) > 2 else "",
                    "joined": (re.findall(r"\d{4}-\d{2}-\d{2}", _text(cells[3])) or [""])[0] if len(cells) > 3 else ""})
    for p in out:
        p["flag"] = _country_flag(p["country"])
    return out


async def liquipedia(client: httpx.AsyncClient, title: str, role: str, game: str) -> dict:
    """A team or player from the game's Liquipedia wiki: infobox facts, and a team's active roster."""
    info = GAME_INFO.get(game)
    if not info:
        return {}
    name = handle(title)
    try:
        data = await _liquipedia(client, info["wiki"], "api", action="query", prop="revisions", rvprop="content",
                                 rvslots="main", titles=name, redirects=1)
        pages = list((data.get("query") or {}).get("pages", {}).values())
        if not pages or "missing" in pages[0]:
            return {}
        page = pages[0]
        text = page["revisions"][0]["slots"]["main"]["*"]
        box = _infobox(text)
        if not box:
            return {}
        out = {"role": role, "game": info, "title": page.get("title", name),
               "source": f"https://liquipedia.net/{info['wiki']}/{page.get('title', name).replace(' ', '_')}",
               "facts": []}
        fact = lambda label, value: out["facts"].append({"label": label, "value": value}) if value else None  # noqa: E731
        if role == "player":
            out.update({"alias": _plain(box.get("id", "")) or name, "name": _plain(box.get("romanized_name") or box.get("name", "")),
                        "team": _plain(box.get("team", "")), "position": _plain(box.get("role") or box.get("position", "")).title(),
                        "status": _plain(box.get("status", "")), "country": _plain(box.get("country", "")),
                        "born": _plain(box.get("birth_date", "")), "titles": _titles(box.get("achievements", "")),
                        "flag": _country_flag(_plain(box.get("country", ""))),
                        "nicknames": _plain(box.get("nicknames", "")), "ids": _plain(box.get("ids", ""))})
            fact("Team", out["team"])
            fact("Role", out["position"])
            fact("Status", out["status"])
            fact("Also known as", out["nicknames"])
            fact("Accounts", out["ids"])
        else:
            created = [_plain(re.sub(r"\{\{[^}]*\}\}:\s*", "", v)).replace("-??", "") for k, v in sorted(box.items()) if k.startswith("created")]
            out.update({"location": _plain(box.get("location", "")), "city": _plain(box.get("city", "")),
                        "flag": _country_flag(_plain(box.get("location", ""))),
                        "region": _plain(box.get("region", "")).upper() if len(box.get("region", "")) <= 4 else _plain(box.get("region", "")),
                        "league": _plain(box.get("league", "")).upper(), "created": [c for c in created if c][:2],
                        "coach": _plain(box.get("coach") or box.get("coaches", "")), "manager": _plain(box.get("manager", ""))})
            fact("Based in", ", ".join(x for x in (out["city"], out["location"]) if x))
            fact("Region", out["region"])
            fact("League", out["league"])
            fact("Coach", out["coach"])
            roster = await _liquipedia(client, info["wiki"], "parse", action="parse", title=page.get("title", name),
                                       text="{{ActiveSquadAuto}}", prop="text", contentmodel="wikitext", disablelimitreport=1)
            out["roster"] = _roster(((roster.get("parse") or {}).get("text") or {}).get("*", "")) if roster else []
        return out
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return {}
