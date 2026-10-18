"""Query typing for the results page.

Deterministic patterns (utilities, live widgets, weather, time, definitions, places)
belong to the rules layer. Entities are decided by data: a Wikipedia title that matches
the query. GLiNER2 (build step 2) will replace the `question`/`howto`/`general` guesses
and feed entity candidates; the hero modules keyed by `kind` stay the same.
"""

import re
from dataclasses import dataclass, field

from archer import utilities

QUESTION_WORDS = ("who", "what", "why", "how", "when", "where", "which", "is", "are", "can",
                  "does", "do", "should", "will", "was", "did")
_ENTITY_PREFIX = re.compile(r"^(?:who|what) (?:is|are|was|were) (?:an? |the )?|^tell me about ")
# A trailing word that says which kind of thing is meant: "archer show", "dune movie", "apple company".
# Panel kind (wiki.KINDS) and the words added to the Wikipedia search for it.
KIND_HINTS = {
    "tv show": ("tv", "TV series"), "tv series": ("tv", "TV series"), "series": ("tv", "TV series"),
    "show": ("tv", "TV series"), "sitcom": ("tv", "TV series"), "anime": ("tv", "anime"),
    "web series": ("tv", "web series"), "movie": ("film", "film"), "film": ("film", "film"),
    "book": ("book", "book"), "novel": ("book", "novel"), "song": ("music", "song"), "album": ("music", "album"),
    "band": ("music", "band"), "company": ("org", "company"), "brand": ("org", "company"),
    "actor": ("person", "actor"), "actress": ("person", "actress"), "singer": ("person", "singer"),
    # Motorsport. "racing series" is longer than "series", so the sorted alternation tries it first.
    "car": ("car", "car"), "rally": ("race", "rally"), "racing series": ("race", "racing series"),
}
_KIND_HINT = re.compile(r"^(?P<core>.+?)\s+(?:the\s+)?(?P<hint>" + "|".join(sorted(map(re.escape, KIND_HINTS), key=len, reverse=True)) + r")$")
_WEATHER = re.compile(
    r"^(?:weather|forecast|temperature)(?:\s+(?:in|at|for|of))?(?:\s+(?P<a>.+?))?(?:\s+(?:today|tomorrow|now))?$"
    r"|^(?P<b>.+?)\s+(?:weather|forecast|temperature)(?:\s+(?:today|tomorrow|now))?$"
    r"|^(?:will it rain|is it raining)(?:\s+(?:in|at)\s+(?P<c>.+?))?(?: today| tomorrow)?\??$"
)
_TIME = re.compile(
    r"^(?:what(?:'s| is) the )?(?:current |local )?time(?:\s+(?:in|at|now in)\s+(?P<a>.+?))?\??$"
    r"|^(?P<b>.+?)\s+(?:local )?time(?: now)?$"
)
_SUN = re.compile(
    r"^(?:what time is )?(?P<event>sunrise|sunset)(?: time)?(?:\s+(?:in|at|for))?(?:\s+(?P<a>.+?))?(?: today| tomorrow)?\??$"
    r"|^(?P<b>.+?)\s+(?P<event2>sunrise|sunset)(?: time)?(?: today)?$"
)
_DEFINE = re.compile(
    r"^(?:define|definition of|meaning of|dictionary)\s+(?P<a>[\w' -]+)$"
    r"|^(?P<b>[\w'-]+(?: [\w'-]+)?)\s+(?:meaning|definition)$"
    r"|^what does (?P<c>[\w' -]+) mean\??$"
)
# Shopping words around a product name: "iphone 16 pro price in india", "buy kindle paperwhite".
# The product panel opens on the matching section; the name alone goes to the entity lookup.
_PRODUCT_INTENT = re.compile(
    r"^(?:buy\s+)?(?P<core>.+?)\s+(?P<intent>prices?(?: in (?:india|the us|usa|us|uk))?|cost|reviews?|specs|"
    r"specifications|spec sheet|buy(?: online)?|deals?|offers?|colou?rs|variants|launch price)$"
    r"|^buy\s+(?P<core2>.+)$"
)
_INTENT_KIND = {"cost": "price", "deal": "price", "offer": "price", "buy": "price", "launch": "price", "review": "review",
                "spec": "specs", "colou": "variants", "color": "variants", "varia": "variants", "price": "price"}
_NEWSY = re.compile(r"\b(?:news|latest|today|live|update|updates|breaking)\b")
_VISUAL = re.compile(r"\b(?:photos?|pictures?|images?|wallpapers?|pics)\b")

# ------------------------------------------------------------- translate
LANGUAGES = {
    "english": "en", "hindi": "hi", "kannada": "kn", "tamil": "ta", "telugu": "te", "malayalam": "ml",
    "marathi": "mr", "bengali": "bn", "gujarati": "gu", "punjabi": "pa", "urdu": "ur", "odia": "or",
    "french": "fr", "spanish": "es", "german": "de", "italian": "it", "portuguese": "pt", "dutch": "nl",
    "japanese": "ja", "korean": "ko", "chinese": "zh-CN", "mandarin": "zh-CN", "russian": "ru",
    "arabic": "ar", "turkish": "tr", "indonesian": "id", "thai": "th", "vietnamese": "vi", "greek": "el",
    "polish": "pl", "swedish": "sv", "ukrainian": "uk", "hebrew": "he", "persian": "fa", "nepali": "ne",
    "sinhala": "si", "swahili": "sw", "filipino": "tl", "malay": "ms", "norwegian": "no", "danish": "da",
    "finnish": "fi", "czech": "cs", "romanian": "ro", "hungarian": "hu", "sanskrit": "sa",
}
_LANG = "|".join(sorted(LANGUAGES, key=len, reverse=True))
_TRANSLATE = re.compile(
    rf"^translate\s+(?P<a>.+?)\s+(?:to|into|in)\s+(?P<la>{_LANG})$"
    rf"|^how (?:do (?:you|i)|to) say\s+(?P<b>.+?)\s+in\s+(?P<lb>{_LANG})\??$"
    rf"|^(?P<c>.+?)\s+in\s+(?P<lc>{_LANG})$"
    rf"|^(?P<d>.+?)\s+(?:to|into)\s+(?P<ld>{_LANG})(?: translation)?$"
)

# "movies in hindi" is a search for Hindi movies, not a translation.
_NOT_TRANSLATE = re.compile(
    r"\b(?:movies?|films?|songs?|music|news|books?|series|channels?|videos?|shows?|dubbed|courses?|jobs?|"
    r"subtitles?|keyboard|typing|font|names?|newspapers?|apps?|websites?)$"
)

# ----------------------------------------------------------------- crypto
COINS = {
    "bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum", "ether": "ethereum",
    "solana": "solana", "sol": "solana", "dogecoin": "dogecoin", "doge": "dogecoin", "xrp": "ripple",
    "ripple": "ripple", "cardano": "cardano", "ada": "cardano", "bnb": "binancecoin", "tether": "tether",
    "usdt": "tether", "usdc": "usd-coin", "litecoin": "litecoin", "ltc": "litecoin", "polkadot": "polkadot",
    "tron": "tron", "trx": "tron", "toncoin": "the-open-network", "shiba inu": "shiba-inu",
    "shib": "shiba-inu", "avalanche": "avalanche-2", "avax": "avalanche-2", "chainlink": "chainlink",
    "polygon": "polygon-ecosystem-token", "monero": "monero", "xmr": "monero", "pepe": "pepe",
}
# Names that are safe to treat as a price lookup with no "price" word.
_COIN_ALONE = {"bitcoin", "btc", "ethereum", "eth", "dogecoin", "litecoin", "cardano", "xrp", "usdt", "shiba inu"}
_COIN = "|".join(sorted(map(re.escape, COINS), key=len, reverse=True))
_FIAT = r"usd|inr|eur|gbp|jpy|aud|cad|sgd|aed|chf|cny"
_CRYPTO = re.compile(
    rf"^(?:price of |value of )?(?P<coin>{_COIN})(?:\s+(?:price|rate|value|live|today|now|to|in|=))*"
    rf"(?:\s+(?P<fiat>{_FIAT}))?(?:\s+(?:price|rate|now|today|live))*$"
)

# ----------------------------------------------------------------- places
PLACE_TYPES = {
    "cafe": ("amenity", "cafe", "Cafes"), "coffee shop": ("amenity", "cafe", "Cafes"),
    "restaurant": ("amenity", "restaurant", "Restaurants"), "hotel": ("tourism", "hotel", "Hotels"),
    "hospital": ("amenity", "hospital", "Hospitals"), "pharmacy": ("amenity", "pharmacy", "Pharmacies"),
    "pharmacie": ("amenity", "pharmacy", "Pharmacies"), "chemist": ("amenity", "pharmacy", "Pharmacies"),
    "medical store": ("amenity", "pharmacy", "Pharmacies"), "atm": ("amenity", "atm", "ATMs"),
    "bank": ("amenity", "bank", "Banks"), "petrol pump": ("amenity", "fuel", "Fuel stations"),
    "petrol bunk": ("amenity", "fuel", "Fuel stations"), "gas station": ("amenity", "fuel", "Fuel stations"),
    "park": ("leisure", "park", "Parks"), "gym": ("leisure", "fitness_centre", "Gyms"),
    "bar": ("amenity", "bar", "Bars"), "pub": ("amenity", "pub", "Pubs"), "brewery": ("craft", "brewery", "Breweries"),
    "bakery": ("shop", "bakery", "Bakeries"), "bakerie": ("shop", "bakery", "Bakeries"),
    "supermarket": ("shop", "supermarket", "Supermarkets"), "grocery store": ("shop", "supermarket", "Supermarkets"),
    "cinema": ("amenity", "cinema", "Cinemas"), "movie theater": ("amenity", "cinema", "Cinemas"),
    "movie theatre": ("amenity", "cinema", "Cinemas"), "school": ("amenity", "school", "Schools"),
    "library": ("amenity", "library", "Libraries"), "librarie": ("amenity", "library", "Libraries"),
    "museum": ("tourism", "museum", "Museums"), "police station": ("amenity", "police", "Police stations"),
    "dentist": ("amenity", "dentist", "Dentists"), "clinic": ("amenity", "clinic", "Clinics"),
    "doctor": ("amenity", "doctors", "Doctors"), "ev charging station": ("amenity", "charging_station", "EV chargers"),
    "charging station": ("amenity", "charging_station", "EV chargers"), "temple": ("amenity", "place_of_worship", "Places of worship"),
    "mall": ("shop", "mall", "Malls"), "salon": ("shop", "hairdresser", "Salons"), "barber": ("shop", "hairdresser", "Salons"),
    "bookstore": ("shop", "books", "Bookstores"), "book shop": ("shop", "books", "Bookstores"),
    "ice cream": ("amenity", "ice_cream", "Ice cream"), "fast food": ("amenity", "fast_food", "Fast food"),
    "co-working space": ("amenity", "coworking_space", "Co-working spaces"), "coworking space": ("amenity", "coworking_space", "Co-working spaces"),
}
_PTYPE = "|".join(sorted(map(re.escape, PLACE_TYPES), key=len, reverse=True))
_PLACES = re.compile(
    rf"^(?:best |good |top |cheap |nearest )?(?P<cat>{_PTYPE})(?:e?s)?"
    rf"(?:\s+(?P<near>near me|nearby|around me|close to me)|\s+(?:in|near|around|at)\s+(?P<area>.+))$"
)

# ------------------------------------------------------- small JS tools
_TIMER = re.compile(
    r"^(?:set (?:a )?)?timer(?: for)?(?:\s+(?P<n>\d+(?:\.\d+)?)\s*(?P<u>s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?))?$"
    r"|^(?P<n2>\d+(?:\.\d+)?)\s*(?P<u2>s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?)\s+timer$"
)
_METRONOME = re.compile(r"^(?:metronome(?:\s+(?P<a>\d{2,3})(?:\s*bpm)?)?|(?P<b>\d{2,3})\s*bpm(?: metronome)?)$")
_FINANCE = re.compile(
    r"^(?:(?P<emi>emi|loan|mortgage|home loan|car loan|personal loan)(?: emi)?(?: calculator)?"
    r"|(?P<bmi>bmi)(?: calculator)?|(?P<tip>tip)(?: calculator)?|calculate (?:my )?(?P<bmi2>bmi)|calculate (?:a )?(?P<tip2>tip))$"
)


@dataclass
class Query:
    text: str
    kind: str  # see views.HERO_KINDS; plus howto | question | general
    data: dict = field(default_factory=dict)
    entity_candidate: str | None = None  # text to look up on Wikipedia
    entity_hint: tuple[str, str] | None = None  # (panel kind, search words) from "… show" / "… movie"
    product_intent: str = ""  # price | review | specs | variants, from "… price" / "buy …"
    wants_news: bool = False
    wants_videos: bool = False
    wants_images: bool = False


def _group(m: re.Match, *names: str) -> str:
    groups = m.groupdict()
    keys = names or tuple(groups)
    return next((groups[k] for k in keys if groups.get(k)), "").strip()


def _seconds(n: str, unit: str) -> int:
    factor = 3600 if unit.startswith("h") else 1 if unit.startswith("s") else 60
    return int(float(n) * factor)


def _local_tools(low: str, text: str) -> Query | None:
    if m := _TIMER.match(low):
        n, u = _group(m, "n", "n2"), _group(m, "u", "u2")
        return Query(text, "timer", {"mode": "timer", "seconds": _seconds(n, u) if n else 300})
    if low in ("stopwatch", "stop watch", "start stopwatch"):
        return Query(text, "timer", {"mode": "stopwatch", "seconds": 0})
    if m := _METRONOME.match(low):
        bpm = int(_group(m, "a", "b") or 100)
        return Query(text, "metronome", {"bpm": min(max(bpm, 30), 280)})
    if m := _FINANCE.match(low):
        tool = "emi" if m.group("emi") else "bmi" if (m.group("bmi") or m.group("bmi2")) else "tip"
        return Query(text, "finance", {"tool": tool})
    if data := utilities.solve_equation(low):
        return Query(text, "solve", data)
    if data := utilities.graph(low):
        return Query(text, "graph", data)
    if data := utilities.tracking(text):
        return Query(text, "track", data)
    return None


def classify(q: str, home_currency: str = "inr") -> Query:
    text = " ".join(q.split())
    low = text.lower().rstrip("?").strip()

    for kind, fn in (("calc", utilities.calculate), ("unit", utilities.convert_units),
                     ("color", utilities.color), ("timestamp", utilities.timestamp),
                     ("random", utilities.random_tool)):
        if data := fn(low if kind != "calc" else text):
            return Query(text, kind, data)
    if tool := _local_tools(low, text):
        return tool

    if (m := _CRYPTO.match(low)) and (m.group("coin") in _COIN_ALONE or low != m.group("coin")):
        return Query(text, "crypto", {"coin": COINS[m.group("coin")], "fiat": (m.group("fiat") or home_currency).lower()})
    if m := _WEATHER.match(low):
        return Query(text, "weather", {"place": _group(m)})
    if m := _SUN.match(low):
        return Query(text, "sun", {"event": _group(m, "event", "event2"), "place": _group(m, "a", "b")})
    if (m := _TIME.match(low)) and not low.startswith(("time management", "time complexity")):
        place = _group(m)
        if len(place.split()) <= 4:
            return Query(text, "time", {"place": place})
    if m := _DEFINE.match(low):
        return Query(text, "define", {"word": _group(m)})
    if (m := _TRANSLATE.match(low)) and not _NOT_TRANSLATE.search(_group(m, "a", "b", "c", "d")):
        phrase, lang = _group(m, "a", "b", "c", "d"), _group(m, "la", "lb", "lc", "ld")
        if phrase and len(phrase.split()) <= 30:
            start = low.find(phrase)  # keep the user's original casing
            return Query(text, "translate", {"text": text[start:start + len(phrase)], "lang": LANGUAGES[lang],
                                             "lang_name": lang.title()})
    if m := _PLACES.match(low):
        tag_key, tag_value, label = PLACE_TYPES[m.group("cat")]
        return Query(text, "places", {"cat": m.group("cat"), "key": tag_key, "value": tag_value, "label": label,
                                      "area": (m.group("area") or "").strip(), "near_me": bool(m.group("near"))})

    words = low.split()
    is_howto = low.startswith(("how to ", "how do i ", "how do you ", "how can i "))
    is_question = text.endswith("?") or (words and words[0] in QUESTION_WORDS)
    kind = "howto" if is_howto else "question" if is_question else "general"

    candidate, hint, intent = None, None, ""
    stripped = _ENTITY_PREFIX.sub("", low)
    if kind == "general" and (m := _PRODUCT_INTENT.match(stripped)):
        stripped = (m.group("core") or m.group("core2")).strip()
        word = m.group("intent") or "buy"
        intent = next(v for k, v in _INTENT_KIND.items() if word.startswith(k))
    if not is_howto and len(stripped.split()) <= 6 and (kind == "general" or stripped != low):
        candidate = stripped
        if m := _KIND_HINT.match(stripped):
            candidate, hint = m.group("core"), KIND_HINTS[m.group("hint")]

    return Query(
        text, kind,
        entity_candidate=candidate,
        entity_hint=hint,
        product_intent=intent,
        wants_news=bool(_NEWSY.search(low)),
        wants_videos=is_howto or bool(re.search(r"\b(?:video|videos|trailer|tutorial)\b", low)),
        wants_images=bool(_VISUAL.search(low)),
    )
