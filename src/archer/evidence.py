"""Server-owned search evidence. Search snippets are never labeled as read pages."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit, urlunsplit


def public_url(value: str) -> str:
    try:
        p = urlsplit(value)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return ""
        if p.port not in (None, 80, 443):
            return ""
        return urlunsplit((p.scheme, p.netloc.lower(), p.path or "/", p.query, ""))
    except ValueError:
        return ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Evidence:
    id: str
    title: str
    url: str
    text: str
    kind: str = "excerpt"
    engines: list[str] = field(default_factory=list)
    published: str = ""
    retrieved: str = field(default_factory=now)
    limitation: str = "Search excerpt; page not read."
    thumbnail: str = ""

    def payload(self):
        return asdict(self)


def add(bundle: list[Evidence], title: str, url: str, text: str, **kwargs) -> Evidence | None:
    url = public_url(url) if url else ""
    if not text.strip():
        return None
    if url:
        prior = next((e for e in bundle if e.url == url), None)
        if prior:
            return prior
    item = Evidence(f"S{len(bundle) + 1}", title[:240], url, text[:24000], **kwargs)
    bundle.append(item)
    return item


def product_facts(data: dict) -> dict:
    """A product card as evidence: prices with their stores, scores with their outlets; no pictures."""
    return {
        **{k: data.get(k) for k in ("name", "brand", "category", "released", "description", "price", "rating",
                                    "score", "used", "variants", "highlights", "specs", "lineage")},
        "released_year": (data.get("secondhand") or {}).get("year"),
        "offers": [{k: o.get(k) for k in ("store", "role", "price_text", "rating", "stock", "url")} for o in data.get("offers", [])],
        "reviews": [{k: r.get(k) for k in ("site", "score_text", "date", "excerpt", "url")} for r in data.get("reviews", [])],
        "note": "Prices and ratings come from search-result snippets on the date retrieved; stores were not visited. "
                "Offers with role 'used' are second-hand or refurbished listings, priced separately from new.",
    }


def from_view(view) -> list[Evidence]:
    bundle = []
    if view.hero:
        kind, data = view.hero.kind, view.hero.data
        url = data.get("wiki_url") or data.get("url") or {
            "weather": "https://open-meteo.com/", "sun": "https://open-meteo.com/",
            "time": "https://open-meteo.com/", "define": "https://en.wiktionary.org/",
            "translate": "https://mymemory.translated.net/", "crypto": "https://www.coingecko.com/",
        }.get(kind, "")
        # Media and styling are not evidence; keep the actual card values and source caveats.
        kept = {k: v for k, v in data.items() if k not in (
            "image", "image_fallback", "image_logo", "image_tall", "flag", "badge", "player", "links", "path")}
        if kind == "entity":
            kept = {k: data.get(k) for k in ("title", "description", "extract")}
        if kind == "product":
            kept = product_facts(data)
        add(bundle, f"Archer {kind} card", url, json.dumps(kept, ensure_ascii=False, default=str),
            kind="card", limitation="Existing card snapshot; cached upstream data may be older. "
            "Wikidata streaming links do not confirm regional availability. "
            "Place headers alone contain no place recommendations or reviews.")
        if kind == "entity" and data.get("qid"):
            facts = {k: data.get(k) for k in ("title", "qid", "tiles", "facts", "cast", "watch")}
            add(bundle, "Wikidata facts · " + data.get("title", "Entity"),
                "https://www.wikidata.org/wiki/" + data["qid"], json.dumps(facts, ensure_ascii=False),
                kind="card", limitation="Wikidata structured claims, potentially cached for a day; streaming links do not verify regional availability.")
    if view.response:
        for result in view.response.results[:12]:
            add(bundle, result.title, result.url, result.content or "Search result title only: " + result.title, engines=result.engines,
                published=result.date, thumbnail=public_url(result.thumbnail), kind="discussion" if any(h in result.host for h in
                ("reddit.com", "stackexchange.com", "stackoverflow.com", "ycombinator.com", "quora.com")) else "excerpt")
    return bundle
