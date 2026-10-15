"""Product panels, assembled from what the search results already say.

There is no keyless shopping API, and store pages refuse direct fetches (Flipkart answers 500,
Sony and Croma 403), so a panel is built from search snippets: store listings give prices,
variants and star ratings, review sites give expert scores, and Wikidata (when the product has
an article) gives the brand, release, lineage, colours and specs. A query is a product when
Wikidata says so, or when it names a model ("wh-1000xm5", "s24") and stores list that model.
The lazy /api/product adds one search for prices and one for reviews, both cached.
"""

import asyncio
import re
import statistics
from collections import Counter
from datetime import date
from urllib.parse import quote_plus

# Registrable host -> display name. Stores sell it; guides list prices; reviewers score it.
STORES = {
    "amazon.in": "Amazon", "amazon.com": "Amazon", "amazon.co.uk": "Amazon", "amazon.de": "Amazon",
    "amazon.ca": "Amazon", "amazon.com.au": "Amazon", "flipkart.com": "Flipkart", "croma.com": "Croma",
    "reliancedigital.in": "Reliance Digital", "vijaysales.com": "Vijay Sales", "tatacliq.com": "Tata CLiQ",
    "jiomart.com": "JioMart", "myntra.com": "Myntra", "ajio.com": "AJIO", "nykaa.com": "Nykaa",
    "maplestore.in": "Maple", "imagineonline.store": "Imagine", "unicornstore.in": "Unicorn",
    "sangeethamobiles.com": "Sangeetha", "poorvika.com": "Poorvika", "decathlon.in": "Decathlon",
    "bestbuy.com": "Best Buy", "walmart.com": "Walmart", "target.com": "Target", "newegg.com": "Newegg",
    "bhphotovideo.com": "B&H Photo", "adorama.com": "Adorama", "costco.com": "Costco", "currys.co.uk": "Currys",
    "argos.co.uk": "Argos", "johnlewis.com": "John Lewis",
    "mediamarkt.de": "MediaMarkt", "jbhifi.com.au": "JB Hi-Fi", "amzn.to": "Amazon",
    "amazon.com.mx": "Amazon", "rcwilley.com": "RC Willey", "crutchfield.com": "Crutchfield", "abt.com": "Abt",
}
GUIDES = {
    "cashify.in": "Cashify", "smartprix.com": "Smartprix", "91mobiles.com": "91mobiles",
    "gadgets360.com": "Gadgets 360", "gadgets.ndtv.com": "Gadgets 360", "pricebaba.com": "PriceBaba",
    "mysmartprice.com": "MySmartPrice", "pricehistory.app": "PriceHistory", "camelcamelcamel.com": "camelcamelcamel",
    "cardekho.com": "CarDekho", "carwale.com": "CarWale", "zigwheels.com": "ZigWheels", "bikewale.com": "BikeWale",
    "bikedekho.com": "BikeDekho", "gsmarena.com": "GSMArena", "kimovil.com": "Kimovil",
}
REVIEWERS = {
    "rtings.com": "RTINGS", "soundguys.com": "SoundGuys", "theverge.com": "The Verge", "techradar.com": "TechRadar",
    "cnet.com": "CNET", "tomsguide.com": "Tom's Guide", "engadget.com": "Engadget", "wired.com": "WIRED",
    "pcmag.com": "PCMag", "gsmarena.com": "GSMArena", "notebookcheck.net": "Notebookcheck",
    "androidauthority.com": "Android Authority", "9to5mac.com": "9to5Mac", "macrumors.com": "MacRumors",
    "whathifi.com": "What Hi-Fi?", "trustedreviews.com": "Trusted Reviews", "digitaltrends.com": "Digital Trends",
    "zdnet.com": "ZDNET", "tomshardware.com": "Tom's Hardware", "ign.com": "IGN", "gamespot.com": "GameSpot",
    "91mobiles.com": "91mobiles", "gadgets360.com": "Gadgets 360", "gadgets.ndtv.com": "Gadgets 360",
    "digit.in": "Digit", "beebom.com": "Beebom", "dxomark.com": "DXOMARK", "pocket-lint.com": "Pocket-lint",
    "t3.com": "T3", "stuff.tv": "Stuff", "expertreviews.co.uk": "Expert Reviews", "consumerreports.org": "Consumer Reports",
    "arstechnica.com": "Ars Technica", "mashable.com": "Mashable", "laptopmag.com": "Laptop Mag",
    "kitguru.net": "KitGuru", "anandtech.com": "AnandTech", "androidcentral.com": "Android Central",
    "imore.com": "iMore", "techcrunch.com": "TechCrunch", "livemint.com": "Mint", "indianexpress.com": "Indian Express",
    "hindustantimes.com": "Hindustan Times", "smartprix.com": "Smartprix", "cardekho.com": "CarDekho",
    "autocarindia.com": "Autocar India", "overdrive.in": "Overdrive", "caranddriver.com": "Car and Driver",
    "topgear.com": "Top Gear", "headphonezone.in": "Headphone Zone", "crutchfield.com": "Crutchfield",
}
# Product lines whose name doesn't say the maker: "iphone 16" is Apple's, "galaxy s24" Samsung's.
LINES = {"iphone": "Apple", "ipad": "Apple", "macbook": "Apple", "imac": "Apple", "airpods": "Apple",
         "galaxy": "Samsung", "pixel": "Google", "playstation": "Sony", "ps5": "Sony", "ps4": "Sony", "xperia": "Sony",
         "bravia": "Sony", "xbox": "Microsoft", "surface": "Microsoft", "kindle": "Amazon", "echo": "Amazon",
         "thinkpad": "Lenovo", "legion": "Lenovo", "zenbook": "Asus", "rog": "Asus", "vivobook": "Asus",
         "xps": "Dell", "alienware": "Dell", "inspiron": "Dell", "pavilion": "HP", "omen": "HP", "spectre": "HP",
         "redmi": "Xiaomi", "poco": "Xiaomi", "nord": "OnePlus", "geforce": "Nvidia", "rtx": "Nvidia",
         "radeon": "AMD", "ryzen": "AMD", "airwrap": "Dyson", "quietcomfort": "Bose", "gopro": "GoPro"}
VIDEO_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com", "vimeo.com", "instagram.com", "facebook.com", "x.com")

ACCESSORY = re.compile(
    r"\b(?:case|cases|cover|covers|screen ?guard|screen protector|tempered glass|protector|charger|charging cable|"
    r"cable|skin|skins|strap|straps|ear ?pads?|cushions?|stand|holder|mount|adapter|replacement|sticker|pouch|sleeve|"
    r"compatible with|dummy|keychain|decal)\b", re.I)
# A second-hand or refurbished listing: kept, but as a used offer that never sets the new price.
CONDITION = re.compile(r"\b(?:refurbished|renewed|pre-?owned|second ?hand|used|open box|for parts)\b", re.I)

# Second-hand markets by home country: (host, name, search page). Older products are often only sold
# used, so the panel adds these when a model is three or more years old. Search links are labelled as such.
USED_MARKETS = {
    "IN": [("olx.in", "OLX", "https://www.olx.in/items/q-{slug}"), ("quikr.com", "Quikr", ""), ("cashify.in", "Cashify", "")],
    "US": [("ebay.com", "eBay", "https://www.ebay.com/sch/i.html?_nkw={q}"), ("swappa.com", "Swappa", "https://swappa.com/search?q={q}"),
           ("mercari.com", "Mercari", "https://www.mercari.com/search/?keyword={q}"), ("backmarket.com", "Back Market", "")],
    "GB": [("ebay.co.uk", "eBay", "https://www.ebay.co.uk/sch/i.html?_nkw={q}"),
           ("gumtree.com", "Gumtree", "https://www.gumtree.com/search?search_term={q}"),
           ("backmarket.co.uk", "Back Market", ""), ("musicmagpie.co.uk", "musicMagpie", "")],
    "DE": [("kleinanzeigen.de", "Kleinanzeigen", "https://www.kleinanzeigen.de/s-{slug}/k0"),
           ("ebay.de", "eBay", "https://www.ebay.de/sch/i.html?_nkw={q}")],
    "AU": [("gumtree.com.au", "Gumtree", "https://www.gumtree.com.au/s-{slug}/k0"),
           ("ebay.com.au", "eBay", "https://www.ebay.com.au/sch/i.html?_nkw={q}")],
    "CA": [("kijiji.ca", "Kijiji", ""), ("ebay.ca", "eBay", "https://www.ebay.ca/sch/i.html?_nkw={q}")],
    "PK": [("olx.com.pk", "OLX", "https://www.olx.com.pk/items/q-{slug}")],
    "ID": [("olx.co.id", "OLX", "https://www.olx.co.id/items/q-{slug}")],
    "AE": [("dubizzle.com", "Dubizzle", "")],
    "FR": [("leboncoin.fr", "leboncoin", ""), ("backmarket.fr", "Back Market", "")],
    "SG": [("carousell.sg", "Carousell", "https://www.carousell.sg/search/{slug}")],
}
USED_HOSTS = {host: name for markets in USED_MARKETS.values() for host, name, _ in markets} | {
    "ebay.in": "eBay", "olx.com": "OLX", "olx.pl": "OLX", "olx.pt": "OLX", "olx.ro": "OLX", "olx.ua": "OLX",
    "vinted.com": "Vinted", "wallapop.com": "Wallapop", "craigslist.org": "Craigslist", "carousell.com": "Carousell",
    "jiji.ng": "Jiji", "mercari.jp": "Mercari"}
_SUFFIXES = {"max", "plus", "ultra", "mini", "lite", "pro", "fe", "neo", "air", "se", "s", "e", "x", "edge", "fold",
             "flip", "xl", "slim", "a", "t"}

CURRENCY = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "$": "USD", "us$": "USD", "usd": "USD",
            "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP"}
SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_PRICE = re.compile(
    rf"(?P<cur>₹|US\$|\$|€|£|(?<![A-Za-z])(?:Rs\.?|INR|USD|EUR|GBP)(?![A-Za-z]))\s*(?P<num>{_NUM})"
    rf"(?:\s*(?:-|–|to)\s*(?:₹|Rs\.?)?\s*(?P<num2>{_NUM}))?(?:\s*(?P<mult>lakh|lac|crore|cr)\b)?", re.I)
_NOT_BEFORE = re.compile(r"(?:save|saving|off|emi|cashback|cash back|discount|exchange|up ?to|coupon|extra|bank|worth|"
                         r"delivery|shipping|fee|fees|no.cost|instant|bonus|flat|less|reduced by|drop of|cut of|"
                         r"rental|deposit|subscription|mrp)\W{0,3}$", re.I)
_NOT_AFTER = re.compile(r"^\s*(?:(?:-|–|to)\s*\D{0,4}[\d,.]+\s*)?(?:/|per\s|a\s)\s*(?:mo|month|yr|year|week|day)|"
                        r"^\s*(?:(?:-|–|to)\s*\D{0,4}[\d,.]+\s*)?(?:off|cashback|discount|emi|instant|bank|bonus|cheaper|less|"
                        r"more|savings?)\b|^\s*%", re.I)
# A price inside a trade-in, financing or discount sentence is never the selling price
# ("Get $35 - $885 off an iPhone 16 when you trade in an iPhone 8").
_NOT_SENTENCE = re.compile(r"trade.?in|exchange|financ|instalment|installment|\bemi\b|per month|/mo\b|cashback|cash back|"
                           r"\boff\b|discount|save up to|bank offer|coupon|monthly", re.I)
_STORE_RATING = re.compile(r"(?<![\d.])(?P<v>[1-5](?:\.\d)?)\s*(?:out of\s*5(?:\s*stars?)?|/\s*5(?![\d.])|★|stars?\b)", re.I)
_VOTES = re.compile(r"(?P<n>\d[\d,]*)\s*(?:global ratings|ratings|reviews)\b", re.I)
_SCORE = re.compile(r"(?<![\d.])(?P<v>\d{1,3}(?:\.\d{1,2})?)\s*(?:/|out of)\s*(?P<of>5|10|100)(?![\d.])", re.I)
_SCORE_STARS = re.compile(r"(?<![\d.])(?P<v>[1-5](?:\.5)?)\s*stars?\b", re.I)
_LEAD_DATE = re.compile(r"^(?P<d>\d{1,2} [A-Z][a-z]{2,8},? \d{4}|[A-Z][a-z]{2,8} \d{1,2},? \d{4})\s*(?:\.\.\.|…|[-—–·:])?\s*")
_SITE_SUFFIX = re.compile(r"\s*[|\-–—:]\s*(?:[A-Z][\w'.&? ]{1,30}|[\w.-]+\.(?:com|in|net|org|co\.uk|tv))$")
_STORAGE = re.compile(r"(?<![\d.])(\d{2,4}|1|2)\s?(GB|TB)\b(?!\s*(?:RAM|LPDDR|DDR|memory|unified))", re.I)

COLOURS = [  # keyword -> swatch; first match wins, so specific names come before plain ones
    ("desert", "#c4a484"), ("natural titanium", "#bab3a8"), ("natural", "#bab3a8"), ("starlight", "#efe6d8"),
    ("midnight", "#232a35"), ("space gr", "#5b5d61"), ("graphite", "#4b4c4f"), ("ultramarine", "#6a7fdb"),
    ("lavender", "#c7b8e8"), ("teal", "#3f8f8f"), ("sage", "#a9b89c"), ("mint", "#a8e0c4"), ("coral", "#f08a72"),
    ("rose", "#e7b6b4"), ("navy", "#26324f"), ("cream", "#efe5cf"), ("beige", "#d9c7a7"), ("sand", "#d8c3a0"),
    ("platinum", "#d9d9d6"), ("titanium", "#8e8d89"), ("bronze", "#9c6b3c"), ("copper", "#b87333"),
    ("silver", "#c9cbcd"), ("gold", "#d8bd7c"), ("black", "#1b1b1d"), ("white", "#f5f5f2"), ("grey", "#8a8d91"),
    ("gray", "#8a8d91"), ("blue", "#3f6fd8"), ("green", "#3f9a64"), ("pink", "#f2a7c0"), ("purple", "#8a63c9"),
    ("violet", "#8a63c9"), ("red", "#d23c3c"), ("yellow", "#f2cf4a"), ("orange", "#ee8a3a"), ("brown", "#7a5434"),
]
CATEGORIES = [  # label, words in result titles that suggest it
    ("Headphones", r"headphones?|over[- ]ear|on[- ]ear"), ("Earbuds", r"earbuds|tws|in[- ]ear|airpods"),
    ("Smartphone", r"smartphones?|5g phone|mobile phone|\bphone\b|iphone|galaxy s\d"),
    ("Laptop", r"laptops?|notebook|macbook|chromebook"), ("Tablet", r"tablets?|\bipad\b"),
    ("Smartwatch", r"smart ?watch|watch series|galaxy watch"), ("Camera", r"cameras?|mirrorless|dslr"),
    ("Television", r"\btv\b|television|qled|oled tv"), ("Games console", r"consoles?|playstation|xbox|nintendo switch"),
    ("Speaker", r"speakers?|soundbar"), ("Monitor", r"monitors?"), ("Graphics card", r"graphics card|\bgpu\b|geforce|radeon"),
    ("Processor", r"\bcpu\b|processor"), ("E-reader", r"e-?reader|kindle"), ("Car", r"\bcar\b|\bsuv\b|sedan|hatchback"),
    ("Motorcycle", r"motorcycle|motorbike|\bbike\b|scooter"), ("Shoes", r"shoes?|sneakers?|running shoe"),
    ("Keyboard", r"keyboards?"), ("Mouse", r"\bmouse\b"), ("Router", r"router|mesh wi-?fi"), ("Drone", r"drones?"),
    ("Vacuum cleaner", r"vacuum|robot vacuum"), ("Appliance", r"refrigerator|washing machine|air conditioner|microwave"),
]
_SPEC_WORDS = re.compile(r"\b(?:chip|processor|core|display|screen|battery|camera|mah|hz|gb|tb|mp|inch|hours?|bluetooth|"
                         r"anc|noise|driver|ram|rom|storage|wi-?fi|usb|weight|ip\d\d|amoled|oled|lcd|refresh|zoom|mm)\b", re.I)


def _table(host: str, table: dict[str, str]) -> str:
    host = host.lower().removeprefix("www.")
    for key, name in table.items():
        if host == key or host.endswith("." + key):
            return name
    return ""


def _site_word(host: str) -> str:
    """apple for www.apple.com / store.apple.co.in."""
    labels = host.lower().removeprefix("www.").split(".")
    while len(labels) > 1 and labels[-1] in ("com", "org", "net", "co", "in", "uk", "io", "us", "de", "jp", "au", "ca"):
        labels.pop()
    return labels[-1] if labels else ""


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _chunks(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower())


# ------------------------------------------------------------------ prices

def fmt_money(amount: float, currency: str) -> str:
    """₹1,09,900 with lakh grouping; $999.99 / €1,099 elsewhere."""
    symbol = SYMBOL.get(currency, currency + " ")
    decimals = 0 if amount == int(amount) or amount >= 1000 else 2
    if currency == "INR":
        whole, _, frac = f"{amount:.{decimals}f}".partition(".")
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        text = ",".join(([head] if head else []) + pairs + [tail]) if head or pairs else tail
        return symbol + text + (f".{frac}" if frac else "")
    return f"{symbol}{amount:,.{decimals}f}"


def _amount(num: str, mult: str) -> float:
    value = float(num.replace(",", ""))
    mult = (mult or "").lower()
    return value * (1e5 if mult in ("lakh", "lac") else 1e7 if mult in ("crore", "cr") else 1)


def find_price(text: str) -> dict | None:
    """The first real selling price in a snippet; skips discounts, EMIs, cashback and per-month figures."""
    for m in _PRICE.finditer(text):
        before, after = text[max(0, m.start() - 26):m.start()], text[m.end():m.end() + 18]
        if _NOT_BEFORE.search(before) or _NOT_AFTER.search(after):
            continue
        # Judge the clause around the price: "From ₹79,900 or ₹6,658/mo." keeps the first figure.
        marks = (". ", ";", "|", " or ", ", ")
        start = max(text.rfind(k, 0, m.start()) + len(k) if text.rfind(k, 0, m.start()) != -1 else 0 for k in marks)
        ends = [i for i in (text.find(k, m.end()) for k in marks) if i != -1]
        if _NOT_SENTENCE.search(text[start:min(ends) if ends else len(text)]):
            continue
        currency = CURRENCY[m.group("cur").lower().strip()]
        low = _amount(m.group("num"), m.group("mult"))
        high = _amount(m.group("num2"), m.group("mult")) if m.group("num2") else 0.0
        if high and high < low:
            low, high = high, low
        floor = 99 if currency == "INR" else 2
        if not floor <= low <= 5e8:
            continue
        return {"currency": currency, "amount": low, "high": high if high > low else 0.0}
    return None


def store_rating(text: str) -> tuple[float, int] | None:
    if m := _STORE_RATING.search(text):
        votes = _VOTES.search(text)
        return float(m.group("v")), int(votes.group("n").replace(",", "")) if votes else 0
    return None


def review_score(text: str) -> tuple[float, str] | None:
    """An expert score normalised to 10, with the reviewer's own wording ("4.5/5")."""
    for m in _SCORE.finditer(text):
        value, scale = float(m.group("v")), int(m.group("of"))
        if 0 < value <= scale and not (scale == 100 and value < 20):
            shown = f"{m.group('v')}/{scale}"
            return round(value / scale * 10, 1), shown
    if m := _SCORE_STARS.search(text):
        value = float(m.group("v"))
        return round(value * 2, 1), f"{m.group('v')}/5"
    return None


# ---------------------------------------------------------------- matching

# Makers a name may lead with or leave out ("lg g3 oled" vs Croma's "LG G3 165 cm (65 inch) OLED").
BRANDS = {"sony", "lg", "samsung", "apple", "google", "oneplus", "xiaomi", "redmi", "poco", "realme", "oppo", "vivo", "iqoo",
          "motorola", "nothing", "nokia", "honor", "huawei", "bose", "jbl", "sennheiser", "boat", "dell", "hp", "lenovo", "asus",
          "acer", "msi", "microsoft", "nikon", "canon", "fujifilm", "panasonic", "philips", "dyson", "nintendo", "amazon",
          "garmin", "tcl", "hisense", "gopro", "dji", "logitech", "razer", "corsair", "nvidia", "amd", "intel", "toyota",
          "honda", "hyundai", "tata", "mahindra", "maruti", "suzuki", "kia", "bmw", "audi", "yamaha", "bajaj", "tvs", "ather"}


class Matcher:
    """Does a listing title name this exact model? The model words ("G3", "16 Pro", "WH-1000XM5") must
    appear in order; the other words anywhere, and a leading maker may be missing. "iPhone 16 Pro" matches
    "Apple iPhone 16 Pro (128 GB)" but not "iPhone 16 Pro Max"; "lg g3 oled" matches "LG G3 164 cm (65 inch) OLED"."""

    def __init__(self, *names: str):
        names = [n for n in names if n and _chunks(n)]
        self.parts = {p for n in names for p in _chunks(n)}
        self.has_model = any(re.search(r"\d", p) for p in self.parts)
        self.rules = []
        for name in names:
            words = [w for w in re.split(r"\s+", name.lower().strip()) if _chunks(w)]
            digits = [i for i, w in enumerate(words) if re.search(r"\d", w)]
            if digits:
                first, last = digits[0], digits[-1]
                while last + 1 < len(words) and _compact(words[last + 1]) in _SUFFIXES:
                    last += 1  # "16 pro": a variant word right after the number belongs to the model
                core, rest = words[first:last + 1], words[:first] + words[last + 1:]
            else:
                core, rest = words, []
            required = [_compact(w) for i, w in enumerate(rest) if not (_compact(w) in BRANDS or (i == 0 and first > 0 and len(rest) > 1))] if digits else []
            pattern = r"[\s\-_.]*".join(r"[\s\-_.]*".join(map(re.escape, _chunks(w))) for w in core)
            self.rules.append((re.compile(r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])(?:[\s\-_/,()]*(?P<next>[a-z0-9]+))?"), required))

    def __call__(self, text: str) -> bool:
        low = re.sub(r"[™®©]", "", text.lower())
        words = set(re.findall(r"[a-z0-9]+", low)) | {_compact(low)}
        for pattern, required in self.rules:
            if not all(w in words or any(w in x for x in words if len(x) > len(w) + 2) for w in required):
                continue
            for m in pattern.finditer(low):
                after = m.group("next") or ""
                if after in _SUFFIXES and after not in self.parts:
                    continue
                return True
        return False


def _is_accessory(title: str, name: str) -> bool:
    return bool(ACCESSORY.search(title)) and not ACCESSORY.search(name)


def detect(name: str, results: list) -> bool:
    """Results-based product test for names without a Wikidata product: a model name that stores list."""
    words = _chunks(name)
    if not 1 <= len(words) <= 6 or re.search(r"\b(?:best|top|under|below|vs|versus|cheap|budget|compare|list|"
                                              r"how|why|what|near|history|meaning)\b", name.lower()):
        return False
    match = Matcher(name)
    commerce = reviews = 0
    for r in results[:12]:
        if not match(r.title) or _is_accessory(r.title, name):
            continue
        if not match.has_model:
            # "kindle paperwhite" leads its listings ("Amazon Kindle Paperwhite…"); "rice" sits inside other names.
            title, first = _compact(r.title), _compact((r.title.split() or [""])[0])
            if title.find(_compact(name)) not in (0, len(first)):
                continue
        if _table(r.host, STORES) or _table(r.host, GUIDES):
            commerce += 1
        elif _table(r.host, REVIEWERS) or re.search(r"\breview", r.title, re.I):
            reviews += 1
    if match.has_model:
        return commerce >= 2 or (commerce >= 1 and reviews >= 1)
    return commerce >= 3


def display_name(name: str, results: list) -> str:
    """Spell the name the way listings do: "iphone 16 pro" -> "iPhone 16 Pro", "sony wh-1000xm5" -> "Sony WH-1000XM5"."""
    titles = " ".join(r.title for r in results[:12])
    out = []
    for word in re.findall(r"\S+", name):
        seen = Counter(re.findall(rf"(?<![\w-]){re.escape(word)}(?![\w-])", titles, re.I))
        if seen:
            best = max(seen.items(), key=lambda kv: (kv[1], kv[0] != kv[0].lower()))[0]
            if best == best.lower():
                best = best.upper() if re.search(r"\d", best) and re.search(r"[a-z]", best) else best.capitalize()
            out.append(best)
        elif re.search(r"\d", word) and re.search(r"[a-z]", word, re.I):
            out.append(word.upper())
        else:
            out.append(word[:1].upper() + word[1:])
    text = " ".join(out)
    return re.sub(r"^Iphone|^IPhone|^Ipad|^IPad|^Imac|^IMac|^Ipod|^IPod", lambda m: "i" + m.group(0)[1:].capitalize(), text)


# ------------------------------------------------------------------- parts

def _clean_title(title: str) -> str:
    return _SITE_SUFFIX.sub("", title).strip()


_CORP = re.compile(r"[,.]?\s+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|llc|plc|gmbh|ag|sa|group|electronics)\.?$", re.I)


def brand_name(brand: str) -> str:
    """Apple Inc. -> Apple; Sony Group Corporation -> Sony."""
    brand = brand.strip()
    while (trimmed := _CORP.sub("", brand)) != brand:
        brand = trimmed
    return brand


# Store search, category and list pages: their snippets describe other products and their links don't
# open this one (amazon.in/lg-g3-oled/s?k=…, croma.com/l/…, amazon.com/clp/…, "LG TV Price List").
_NOT_PRODUCT_URL = re.compile(r"/s(?:/|\?|$)|/search|/sch/|/searchb|[?&](?:k|q|query|keyword|text|search)=|/l/|/b\?|/b/|"
                              r"/browse|/categor|/collections?/|/clp/|/list/|/pricelist|/brand-store|/stores?/|/deals|/offers|"
                              r"/unboxed/|/blogs?/|/news/|/articles?/|/stories/|/compare|/q-[^/]*$|/s-[^/]+/k0|/prices/|/resource-center/", re.I)
# A snippet that repeats deal badges ("4.2 · 33% OFF. ₹14,990. ₹9,990 ; 4.2 · 33% OFF…") is quoting a carousel
# of other products on the page, so none of its prices belong to this one.
_CAROUSEL = re.compile(r"\d{1,2}\s?% off", re.I)
_NOT_PRODUCT_TITLE = re.compile(r"price list|\bbest\b.*\bunder\b|\btop \d+|\ball\b.*\bmodels\b|\bcompare\b|^amazon\.\w+:", re.I)
_VARIANT = re.compile(r"(?<![\d.])(\d{2,3})(?:\s?(?:-|\s)?(?:inch(?:es)?|in\b|\"|″|”))|\((\d{2,3})\s?(?:inch|in|\")\)", re.I)


def product_page(url: str, title: str) -> bool:
    path = re.sub(r"^https?://[^/]+", "", url)
    return not _NOT_PRODUCT_URL.search(path) and not _NOT_PRODUCT_TITLE.search(title)


def variant(title: str) -> str:
    """The size or capacity a listing is for: "65-inch", "256 GB"."""
    if m := _VARIANT.search(title):
        return f"{m.group(1) or m.group(2)}-inch"
    if m := _STORAGE.search(title):
        return f"{m.group(1)} {m.group(2).upper()}"
    return ""


def _local_page(url: str, host: str, country: str) -> bool:
    cc = country.lower()
    return bool(re.search(rf"/(?:{cc}|en-{cc}|{cc}-en|en_{cc})(?:/|$)|\.(?:co\.)?{cc}$", url.lower().split("?")[0] + "|" + host)
                or host.lower().endswith(f".{cc}") or host.lower().endswith(f".co.{cc}"))


def offers(results: list, name: str, match: Matcher, brand: str = "", home: str = "INR", country: str = "IN",
           old: bool = False) -> list[dict]:
    """Store product pages for this model. Second-hand listings come only from the home country's markets;
    for an older model, price trackers keep their link but not their (usually stale launch) price."""
    local = {host for host, _, _ in USED_MARKETS.get(country.upper(), [])}
    by_store: dict[tuple, dict] = {}
    words = {_compact(brand), _compact(brand.split()[0]) if brand.split() else "", (_chunks(name) or [""])[0]} - {""}
    for r in results:
        if not match(r.title) or _is_accessory(r.title, name):
            continue
        used = _table(r.host, USED_HOSTS)
        if used and not _table(r.host, {h: h for h in local}):
            continue  # another country's marketplace (Swappa for an Indian search)
        if used and _table(r.host, GUIDES) and not CONDITION.search(f"{r.title} {r.url}"):
            used = ""  # Cashify's price pages are a tracker; only its refurbished pages are second-hand
        store, role = (used, "used") if used else (_table(r.host, STORES), "store")
        if store and role == "store" and CONDITION.search(r.title):
            role = "used"  # Amazon Renewed and the like
        if not store and _site_word(r.host) in words:
            store, role = (brand or _site_word(r.host).capitalize()), "official"
        if not store and (store := _table(r.host, GUIDES)):
            role = "guide"
        if not store or not product_page(r.url, r.title):
            continue
        text = f"{r.title} · {r.content}"
        carousel = len(_CAROUSEL.findall(r.content or "")) >= 2
        price = None if (old and role == "guide") or carousel else find_price(text)
        if price and price["currency"] != home.upper():
            price = None  # another country's price (apple.com's US store for an Indian search)
        rating = store_rating(r.content) if role in ("store", "official") else None
        offer = {
            "store": store, "role": role, "url": r.url, "host": r.host, "title": _clean_title(r.title),
            "price": price["amount"] if price else None, "high": price["high"] if price else 0.0,
            "currency": price["currency"] if price else "", "price_text": fmt_money(price["amount"], price["currency"]) if price else "",
            "rating": rating[0] if rating else None, "votes": rating[1] if rating else 0,
            "stock": "Out of stock" if re.search(r"out of stock|currently unavailable|sold out", text, re.I) else "",
            "thumb": r.thumbnail if r.thumbnail.startswith("https://") else "",
            "variant": variant(r.title),
        }
        key = (store, offer["variant"], role == "used")  # each size a store lists is its own row; used apart
        prior = by_store.get(key)
        offer["local"] = _local_page(r.url, r.host, country)
        if prior is not None and prior["local"] != offer["local"] and not (prior["price"] and offer["price"]):
            # The same store's page for this country wins over its other regions (apple.com/in over apple.com).
            if offer["local"]:
                by_store[key] = offer
            continue
        if prior is None or (offer["price"] and (not prior["price"] or offer["price"] < prior["price"])):
            if prior and not offer["thumb"]:
                offer["thumb"] = prior["thumb"]
            by_store[key] = offer
    found = list(by_store.values())
    summary = price_summary(found, home)
    if summary:
        cur, low = summary["currency"], summary["low"]
        for o in found:
            # Prices far off the typical one are usually an accessory, a bundle or a different variant.
            off = not o["variant"] and (o["price"] or 0) and not summary["floor"] <= o["price"] <= summary["ceiling"]
            if o["price"] and o["role"] != "used" and (o["currency"] != cur or off):
                o["price"], o["price_text"], o["high"] = None, "", 0.0
            o["best"] = bool(o["price"] and o["role"] in ("store", "official") and o["price"] == low and summary["stores"] > 1)
    # Stores with a price (cheapest first), then price trackers, then listings without a price.
    found.sort(key=lambda o: (o["role"] == "used", o["price"] is None, o["role"] == "guide", o["price"] or 0))
    return [o for o in found if o["role"] != "used"][:8] + [o for o in found if o["role"] == "used"][:5]


def used_summary(found: list[dict], home: str = "INR") -> dict | None:
    """Second-hand prices on their own line: "Used from ₹6,500 · 3 listings"."""
    used = [o for o in found if o["role"] == "used" and o["price"] and o["currency"] == home.upper()]
    if not used:
        return None
    middle = statistics.median(o["price"] for o in used)
    kept = [o for o in used if middle * 0.3 <= o["price"] <= middle * 3]
    low = min(o["price"] for o in kept)
    return {"low_text": fmt_money(low, home.upper()), "count": len(kept), "stores": sorted({o["store"] for o in kept})}


def price_summary(found: list[dict], home: str = "INR") -> dict | None:
    priced = [o for o in found if o["price"] and o["role"] != "used"]
    if not priced:
        return None
    counts = Counter(o["currency"] for o in priced)
    currency = home.upper() if home.upper() in counts else counts.most_common(1)[0][0]
    pool = [o for o in priced if o["currency"] == currency]
    selling = [o for o in pool if o["role"] != "guide"] or pool
    # Trackers vote on what's typical too, so one stray store price can't set the range on its own.
    middle = statistics.median(o["price"] for o in pool)
    floor, ceiling = middle * 0.45, middle * 2.4
    # A listing that names its size is trusted: a 77-inch TV may cost three times the 55-inch one.
    kept = [o for o in selling if o.get("variant") or floor <= o["price"] <= ceiling]
    if not kept:
        return None
    low = min(o["price"] for o in kept)
    high = max(max(o["price"], o["high"]) for o in kept)
    return {"currency": currency, "symbol": SYMBOL.get(currency, ""), "low": low, "high": high,
            "low_text": fmt_money(low, currency), "high_text": fmt_money(high, currency) if high > low * 1.01 else "",
            "stores": len({o["store"] for o in kept if o["role"] != "guide"}), "listed": all(o["role"] == "guide" for o in kept),
            "variant": _span(sorted({o["variant"] for o in kept if o.get("variant")}, key=_size)),
            "floor": floor, "ceiling": ceiling}


def _size(label: str) -> float:
    number = float(re.match(r"[\d.]+", label).group())
    return number * 1024 if label.endswith("TB") else number


def _span(labels: list[str]) -> str:
    """The sizes a price range covers: "77-inch", "55- to 77-inch", "128 GB to 1 TB"."""
    if len(labels) < 2:
        return labels[0] if labels else ""
    low, high = labels[0], labels[-1]
    return f"{low.removesuffix('-inch')}- to {high}" if high.endswith("-inch") else f"{low} to {high}"


_YEAR = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")


def release_year(results: list, match: Matcher, year: int | None = None) -> int | None:
    """Wikidata's release year, else the earliest year that two or more listings and reviews of this model mention."""
    if year:
        return year
    seen = []
    for r in results[:30]:
        if not match(r.title):
            continue
        lead = _LEAD_DATE.match(r.content or "")
        seen += [int(y) for y in _YEAR.findall(f"{r.title} {lead.group('d') if lead else ''} {(r.date or '')[:4]}")]
    return min(seen) if len(seen) >= 2 else None


def secondhand(name: str, year: int | None, country: str, now: int | None = None) -> dict:
    """Older models (three or more years) also point at the country's second-hand markets."""
    now = now or date.today().year
    markets = USED_MARKETS.get(country.upper(), [])
    old = bool(year and now - year >= 3 and markets)
    stale = bool(year and now - year >= 6)  # trackers' prices are then launch-era figures
    q, slug = quote_plus(name), re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    links = [{"name": n, "url": t.format(q=q, slug=slug)} for _, n, t in markets if t] if old else []
    return {"old": old, "stale": stale, "year": year, "links": links, "market": markets[0][1] if old else ""}


def reviews(results: list, name: str, match: Matcher) -> list[dict]:
    out, hosts = [], set()
    for r in results:
        host = r.host.lower().removeprefix("www.")
        site = _table(host, REVIEWERS)
        if host in VIDEO_HOSTS or host in hosts or _table(host, STORES):
            continue
        content = r.content or ""
        scored = review_score(f"{r.title} · {content}")
        # A review says so in its title or gives a score; spec sheets and price pages are not reviews.
        titled = re.search(r"\breview\b|\btested\b|hands-on", r.title, re.I)
        if not (titled or scored and not _table(host, GUIDES)):
            continue
        if not match(f"{r.title} {content[:160]}") or _is_accessory(r.title, name):
            continue
        lead = _LEAD_DATE.match(content)
        excerpt = content[lead.end():] if lead else content
        excerpt = re.sub(r"\s+", " ", excerpt).strip(" .…")
        excerpt = re.sub(r"^(?:overall|verdict|score|rating)\s*:?\s*[\d.]+\s*/\s*\d+\s*(?:\.\.\.|…)?\s*", "", excerpt, flags=re.I)
        if excerpt and not re.search(r"[.!?\"”)]$", excerpt):
            excerpt += "…"  # search snippets stop mid-sentence
        hosts.add(host)
        out.append({"site": site or _site_word(host).capitalize(), "host": host, "url": r.url,
                    "title": _clean_title(r.title), "excerpt": excerpt[:240] + ("…" if len(excerpt) > 240 else ""),
                    "score": scored[0] if scored else None, "score_text": scored[1] if scored else "",
                    "date": lead.group("d") if lead else "", "known": bool(site)})
    out.sort(key=lambda x: (x["score"] is None, not x["known"]))
    return out[:8]


def variants(results: list, match: Matcher, colours: list[str] | None = None) -> dict:
    storage, found = set(), []
    for r in results[:20]:
        if not match(r.title) or not _table(r.host, STORES) and not _table(r.host, GUIDES):
            continue
        for size, unit in _STORAGE.findall(r.title):
            storage.add((int(size) * (1024 if unit.upper() == "TB" else 1), f"{size} {unit.upper()}"))
        groups = re.findall(r"\(([^()]{3,80})\)", r.title) + re.findall(r"[-–,]\s*([A-Za-z][A-Za-z ]{2,24})\s*$", r.title)
        for group in groups:
            for part in group.split(","):
                part = part.strip()
                if part and len(part.split()) <= 3 and not re.search(r"\d", part) and swatch(part):
                    found.append(part.title())
    # Store titles name the finishes ("Desert Titanium"); Wikidata's plain colours are the fallback.
    names = list(dict.fromkeys(found or [c.title() for c in colours or []]))[:8]
    return {"storage": [label for _, label in sorted(storage)][:6],
            "colors": [{"name": n, "hex": swatch(n)} for n in names if swatch(n)]}


def swatch(name: str) -> str:
    low = name.lower()
    return next((hex_ for word, hex_ in COLOURS if word in low), "")


def highlights(results: list, name: str, match: Matcher) -> list[str]:
    """Spec bullets that store snippets carry ("A18 Pro Chip, 6 Core Processor ; 128 GB ROM")."""
    out, low_name = [], name.lower()
    for r in results[:20]:
        if not match(r.title) or not _table(r.host, STORES):
            continue
        for segment in re.split(r"\s;\s|\s\|\s|·", r.content or ""):
            segment = re.split(r"(?<=[A-Za-z0-9])\.\s", segment.strip())[0].strip(" .…-")
            segment = re.sub(r"\s*,?\s*$", "", segment)
            if (6 <= len(segment) <= 48 and _SPEC_WORDS.search(segment) and low_name not in segment.lower()
                    and not segment.lower().startswith(("buy", "shop", "get ", "free", "with ", "also"))
                    and segment.lower() not in (h.lower() for h in out)):
                out.append(segment[:1].upper() + segment[1:])
    return out[:6]


def category(results: list, label: str = "") -> str:
    label = re.sub(r"\s*(?:model|product|line|series|family)$", "", (label or "").strip(), flags=re.I)
    if label and label.lower() not in ("", "product", "electronic device"):
        return label[:1].upper() + label[1:]
    text = " ".join(r.title for r in results[:12]).lower()
    scores = [(len(re.findall(pattern, text)), name) for name, pattern in CATEGORIES]
    best = max(scores)
    return best[1] if best[0] >= 2 else ""


def _rivals(name: str, related: list[str], lineage: dict) -> list[str]:
    """Things people compare this with: "x vs y" related searches first, then the next and previous model."""
    out, low = [], name.lower()
    for term in related:
        sides = re.split(r"\s+(?:vs\.?|versus)\s+", term.lower())
        if len(sides) == 2:
            other = sides[1] if Matcher(name)(sides[0]) else sides[0] if Matcher(name)(sides[1]) else ""
            if other and other != low:
                out.append(other)
    out += [lineage[k] for k in ("next", "previous") if lineage.get(k)]
    return list(dict.fromkeys(out))[:3]


# ------------------------------------------------------------------- panel

def _gallery(entity: dict | None) -> list[str]:
    """The article's photo, if it has one. Listing thumbnails are often banners or store logos, so
    archer.js tries the image search for this exact model first and uses them only as a fallback."""
    if not entity or not entity.get("image"):
        return []
    # A product article's SVG is a drawing of the product, not a logo; its PNG render loads everywhere.
    if entity.get("image_logo"):
        return [entity["image_fallback"]] if entity.get("image_fallback") else []
    return [entity["image"]]


def live_parts(name: str, results: list, *, brand: str = "", colours: list[str] | None = None, home: str = "INR",
               aliases: tuple[str, ...] = (), country: str = "IN", old: bool = False) -> dict:
    """Everything a panel takes from search results; /api/product recomputes it over more results."""
    match = Matcher(name, *aliases)
    found = offers(results, name, match, brand, home, country, old)
    summary = price_summary(found, home)
    if summary:
        summary = {k: v for k, v in summary.items() if k not in ("floor", "ceiling")}
    rated = [o for o in found if o["rating"]]
    found_reviews = reviews(results, name, match)
    scored = [r["score"] for r in found_reviews if r["score"] is not None]
    return {
        "price": summary,
        "used": used_summary(found, home),
        "offers": found,
        "rating": {"value": round(statistics.fmean(o["rating"] for o in rated), 1), "sources": len(rated),
                   "votes": sum(o["votes"] for o in rated)} if rated else None,
        "score": {"value": round(statistics.fmean(scored), 1), "count": len(scored)} if scored else None,
        "reviews": found_reviews,
        "variants": variants(results, match, colours),
        "highlights": highlights(results, name, match),
        "thumbs": [o["thumb"] for o in found if o["thumb"]],
    }


def panel(name: str, results: list, *, entity: dict | None = None, intent: str = "", related: list[str] | None = None,
          home: str = "INR", country: str = "IN") -> dict:
    product = (entity or {}).get("product") or {}
    title = (entity or {}).get("title", "")
    base = name
    if title:
        named, more = set(_chunks(name)), set(_chunks(title))
        # The article title, unless the search is more specific ("galaxy s24 ultra" on the "Galaxy S24" article).
        if not (more < named) and (Matcher(name)(title) or not Matcher(title)(name)):
            base = title
    shown = display_name(base, results)
    aliases = (name,) if base != name else ()
    brand = brand_name(product.get("brand") or "")
    first = (_chunks(name) or [""])[0]
    if brand and _compact(brand) not in _compact(shown) and first and any(_site_word(r.host) == first for r in results[:12]):
        brand = first.capitalize()  # the brand field named a product line ("iPhone"); the site names the maker
    if not brand and first and any(_site_word(r.host) == first for r in results[:12]):
        brand = shown.split()[0]
    brand = brand or LINES.get(first, "")
    used = secondhand(shown, release_year(results, Matcher(shown, *aliases), product.get("year")), country)
    live = live_parts(shown, results, brand=brand, colours=product.get("colors"), home=home, aliases=aliases,
                      country=country, old=used["stale"])
    official = next((o["url"] for o in live["offers"] if o["role"] == "official"), "")
    site = next((l["url"] for l in (entity or {}).get("links", []) if l["kind"] == "site"), "") or official
    lineage = {"previous": product.get("previous", ""), "next": product.get("next", "")}
    description = (entity or {}).get("description", "")
    if not description:
        brand_result = next((r for r in results[:10] if brand and _site_word(r.host) == brand.lower() and r.content), None)
        description = (brand_result.content.split(". ")[0][:180] if brand_result else "")
    rivals = [display_name(r, results) for r in _rivals(shown, related or [], lineage)]
    questions = [f"Is the {shown} worth buying?",
                 f"{shown} vs {rivals[0]}" if rivals else f"What are the best alternatives to the {shown}?",
                 f"What do reviewers criticise about the {shown}?",
                 f"Where is the {shown} cheapest right now?"]
    specs = [(t["label"], t["sub"] if t["label"] == "Released" and t.get("sub") else t["value"] + (f" ({t['sub']})" if t.get("sub") else ""),
              t["icon"]) for t in (entity or {}).get("tiles", []) if t["label"] != "Brand"]
    specs += [(label, value, "info") for label, value in (entity or {}).get("facts", [])
              if label not in {s[0] for s in specs}]
    specs = [(label, value[:1].upper() + value[1:], icon) for label, value, icon in specs]
    links = [{"label": "Official site", "url": site, "kind": "site"}] if site else []
    if entity and entity.get("wiki_url"):
        links.append({"label": "Wikipedia", "url": entity["wiki_url"], "kind": "wikipedia"})
    tab = {"price": "prices", "review": "reviews", "specs": "specs", "variants": "overview"}.get(intent, "overview")
    if tab == "specs" and not (specs or live["highlights"]):
        tab = "overview"
    return {
        "name": shown,
        "query_name": name,
        "aliases": list(aliases),
        "brand": brand,
        "category": category(results, product.get("category", "")),
        "released": product.get("released", ""),
        "year": product.get("year"),
        "description": description,
        "extract": (entity or {}).get("extract", ""),
        "wiki_url": (entity or {}).get("wiki_url", ""),
        "url": site or (live["offers"][0]["url"] if live["offers"] else ""),
        "links": links,
        "images": _gallery(entity),
        "lineage": lineage,
        "rivals": rivals,
        "specs": specs[:12],
        "questions": questions,
        "tab": tab,
        "intent": intent,
        "secondhand": used,
        **live,
    }


_RESOLUTION = re.compile(r"(\d{2,5})\s*[x×]\s*(\d{2,5})")


def photos(images: list, name: str, brand: str = "", aliases: tuple[str, ...] = ()) -> list[dict]:
    """Product photos from where the product is sold: the maker's own site, then stores, then review
    sites, each credited and linked. Banners, accessories and other models stay out."""
    match = Matcher(name, *aliases)
    words = {_compact(brand), (_chunks(name) or [""])[0]} - {""}
    out = []
    for i, r in enumerate(images):
        small, full = r.thumbnail or r.img_src, r.img_src or r.thumbnail
        if not small.startswith("https://") or _is_accessory(r.title, name) or re.search(r"remote control", r.title, re.I):
            continue
        if size := _RESOLUTION.search(r.resolution or ""):
            w, h = int(size.group(1)), int(size.group(2))
            if not 0.5 <= w / h <= 2.4 or max(w, h) < 300:
                continue  # page banners and icons
        host = r.host.lower().removeprefix("www.")
        if _site_word(host) in words:
            tier, source = 0, brand or _site_word(host).capitalize()
        elif store := _table(host, STORES):
            tier, source = 1, store
        elif reviewer := _table(host, REVIEWERS):
            tier, source = 2, reviewer
        else:
            tier, source = 3, _site_word(host).capitalize()
        if not match(r.title) and tier > 0:
            continue
        out.append({"thumb": small, "full": full, "url": r.url, "host": host, "source": source, "tier": tier, "rank": i})
    out.sort(key=lambda p: (p["tier"], p["rank"]))
    # At most four from the maker, so the stores' own listing shots show too.
    out = [p for p in out if p["tier"] == 0][:4] + [p for p in out if p["tier"] > 0]
    sold = [p for p in out if p["tier"] < 3]
    return (sold if len(sold) >= 4 else sold + [p for p in out if p["tier"] == 3])[:8]


def comparison_sides(question: str) -> list[str]:
    """The products a comparison names: "iPhone 16 vs iPhone 17?" -> ["iphone 16", "iphone 17"]. Each side needs a
    model number or two words, so "is it worth it or not" names nothing."""
    q = re.sub(r"[?!.]+$", "", " ".join(question.lower().split()))
    q = re.sub(r"^(?:which is better[,:]?|should i (?:buy|get)|compare|difference between)\s+", "", q)
    parts = re.split(r"\s+(?:vs\.?|versus|or|and|compared (?:to|with)|against)\s+", q)
    if len(parts) < 2:
        return []
    sides = [re.sub(r"^(?:the|a|an)\s+", "", p).strip() for p in parts]
    named = [s for s in sides if 1 <= len(s.split()) <= 6 and (re.search(r"\d", s) or len(s.split()) >= 2)][:3]
    return named if len(named) >= 2 else []


async def gather(search, name: str, q: str, market: str = "") -> list:
    """This search's results plus one search for prices and one for reviews (all cached for 10 minutes);
    for an older model, one more on the country's second-hand market ("nintendo 3ds olx")."""
    queries = list(dict.fromkeys([q, f"{name} price", f"{name} review"] + ([f"{name} used {market}"] if market else [])))
    responses = await asyncio.gather(*(search.search(x, "general", 1) for x in queries), return_exceptions=True)
    results, seen = [], set()
    for response in responses:
        if isinstance(response, BaseException):
            continue
        for r in response.results:
            if r.url not in seen:
                seen.add(r.url)
                results.append(r)
    return results
