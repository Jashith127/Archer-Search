"""Server-owned search evidence. Search snippets are never labeled as read pages."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import re
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


PREVIEW_CHARS = 240


def preview(item: "Evidence") -> dict:
    """An evidence payload trimmed for the stream. The source panel renders at most a
    220-char excerpt, so shipping the full text (up to 24k chars per source) was
    bandwidth the client discarded. Validation and the model prompt still use the
    complete retained text — only this wire representation is trimmed."""
    data = item.payload()
    text = data.get("text") or ""
    data["text"] = text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else "")
    return data


# Forum and community hosts: attributed opinion, not reporting. Kept citable, but
# never promoted to a read `page`, so a forum thread cannot outrank real reporting
# in ranked_evidence just because it was fetchable.
DISCUSSION_HOSTS = (
    r"(^|\.)reddit\.com",
    r"(^|\.)stackoverflow\.com",
    r"(^|\.)stackexchange\.com",
    r"(^|\.)superuser\.com",
    r"(^|\.)serverfault\.com",
    r"(^|\.)quora\.com",
    r"(^|\.)news\.ycombinator\.com",
    r"(^|\.)ycombinator\.com",
    r"(^|\.)steamcommunity\.com",
    r"(^|\.)vlr\.gg",
    r"(^|\.)liquipedia\.net",
    r"(^|\.)resetera\.com",
    r"(^|\.)tomshardware\.com/forums",
    r"(^|\.)techpowerup\.com/forums",
    r"(^|\.)headps\.com",
    r"(^|\.)anandtech\.com/forums",
    r"(^|\.)discussions\.apple\.com",
    r"(^|\.)discourse\.",
    r"(^|\.)lemmy\.\w+",
)


def discussion_host(host: str) -> bool:
    """Is this host a forum or community rather than an editorial source?"""
    host = (host or "").lower()
    return any(re.search(pattern, host) for pattern in DISCUSSION_HOSTS)


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
    # Did Archer actually retrieve this content, rather than inferring it from a
    # search-result snippet? True for a page Archer fetched and read, and for
    # structured tool/card data it retrieved itself. False for search excerpts and
    # for pages whose read failed. Not a trust score — a provenance flag, used to
    # stop an unverified snippet from being the only support for a summary.
    verified: bool = False

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
    # Structured data Archer fetched itself (tools, cards) and pages it read are
    # provenance-verified, even when upstream-cached; only search snippets and
    # discussions are not.
    verified = kwargs.get("kind") in ("page", "tool", "card") or bool(kwargs.get("verified"))
    item = Evidence(f"S{len(bundle) + 1}", title[:240], url, text[:24000],
                    **{**kwargs, "verified": verified})
    bundle.append(item)
    return item


def sent_ids(ordered) -> set[str]:
    """The evidence IDs actually serialised into a prompt. Validation must use this
    set, not the whole bundle: a citation to a source the model never saw would
    otherwise pass the 'unknown source' check and be grounded against text it was
    never given."""
    return {e.id for e in ordered}


def snippet_only(citations, bundle) -> bool:
    """True when every cited source is an unverified snippet: a search-result
    excerpt, a discussion post, or a page whose read failed. Such a set may not be
    the sole support for a summary, because nothing verified the underlying page.

    `bundle` may be a sequence of Evidence or an id-keyed mapping.
    """
    if isinstance(bundle, dict):
        cited = [bundle[i] for i in citations if i in bundle]
    else:
        index = {e.id: e for e in bundle}
        cited = [index[i] for i in citations if i in index]
    if not cited:
        return False
    return all(not getattr(e, "verified", False) for e in cited)


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
                published=result.date, thumbnail=public_url(result.thumbnail), kind="discussion" if discussion_host(result.host) else "excerpt")
    return bundle
