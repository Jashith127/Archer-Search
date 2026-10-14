"""Native model tools with typed, bounded inputs and reusable Archer data adapters."""
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from archer import utilities, views
from archer.evidence import add
from archer.sources import weather, wiki, watch
from archer.imagerank import rank_images
from archer.evidence import public_url


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Search(Arguments):
    query: str = Field(min_length=2, max_length=300)
    category: Literal["general", "news"] = "general"


class Read(Arguments):
    source_ids: list[str] = Field(min_length=1, max_length=6)


class Media(Arguments):
    query: str = Field(min_length=2, max_length=200)
    category: Literal["images", "videos"] = "images"


class Weather(Arguments):
    place: str = Field(default="", max_length=100)


class Entity(Arguments):
    name: str = Field(min_length=1, max_length=120)


class Places(Arguments):
    category: str = Field(min_length=1, max_length=60)
    area: str = Field(default="", max_length=100)


class Watch(Arguments):
    entity_source_id: str = Field(min_length=1, max_length=20)


class Calculate(Arguments):
    expression: str = Field(min_length=1, max_length=160)


class Loan(Arguments):
    principal: float = Field(gt=0, le=1e12, allow_inf_nan=False)
    annual_rate: float = Field(ge=0, le=100, allow_inf_nan=False)
    months: int = Field(ge=1, le=1200)


class Cards(Arguments):
    source_ids: list[str] = Field(min_length=1, max_length=4)


TOOLS = {
    "search_web": (Search, "Search for missing evidence. Up to 3 total additional queries; general or news. Never include bangs."),
    "read_sources": (Read, "Read public web pages from retrieved source IDs. Up to 6 total pages. Prefer primary evidence."),
    "search_media": (Media, "Find actual image or video results and show a source-linked gallery. Use for images, photos, visual examples, tutorials or demonstrations. Never guess image URLs."),
    "weather": (Weather, "Get a 7-day forecast and dated hourly readings. Empty place uses the user's configured home location."),
    "lookup_entity": (Entity, "Resolve an entity through Wikipedia/Wikidata, returning typed facts and film/TV watch IDs. Can be called for multiple comparison subjects."),
    "places": (Places, "Find places using Archer's OSM adapter. Category examples: cafe, restaurant, park, pharmacy. No reviews or ratings. Empty area uses home location."),
    "watch_providers": (Watch, "Verify country-specific film/TV availability for an entity evidence ID. Requires a configured TMDB key."),
    "calculate": (Calculate, "Compute safe arithmetic or unit conversions with Archer's deterministic tools. Never use model arithmetic."),
    "loan_payment": (Loan, "Calculate a fixed-rate monthly reducing-balance loan, plus total payment and interest. Principal is in the user's specified currency."),
    "show_cards": (Cards, "Display up to 4 tool results as custom Archer UI: forecast, places, entity facts, viewing providers or calculation. Use only evidence IDs already returned by those tools. Use this when a visual or interactive card helps the task."),
}


def schemas(names=None):
    return [{"type": "function", "function": {"name": name, "description": desc,
        "parameters": model.model_json_schema()}} for name, (model, desc) in TOOLS.items()
        if names is None or name in names]


class Toolbox:
    def __init__(self, searx, ext, reader, settings, evidence, emit):
        self.searx, self.ext, self.reader, self.settings = searx, ext, reader, settings
        self.evidence, self.emit = evidence, emit
        self.searches, self.reads, self.calls = 0, set(), 0
        self.ui = {}
        self.shown = set()
        self.executed = set()

    async def call(self, name, raw):
        if name not in TOOLS:
            raise ValueError("Unknown tool.")
        args = TOOLS[name][0].model_validate(json.loads(raw))
        key = (name, args.model_dump_json())
        if key in self.executed:
            return {"notice": "Already called with these arguments. Reuse the existing evidence."}
        if self.calls >= 12:
            raise ValueError("Tool-call budget reached.")
        self.calls += 1
        self.executed.add(key)
        before = len(self.evidence)
        if name == "search_web":
            if self.searches >= 3 or any(t.startswith("!") for t in args.query.split()):
                raise ValueError("Search budget reached or unsupported bang query.")
            self.searches += 1
            response = await self.searx.search(args.query, args.category)
            for result in response.results[:6]:
                add(self.evidence, result.title, result.url, result.content or "Search result title only: " + result.title,
                    published=result.date, engines=result.engines, thumbnail=public_url(result.thumbnail))
            if response.error or response.unresponsive:
                self.emit("notice", {"text": response.error or "Some search engines were unavailable; coverage is partial."})
        elif name == "read_sources":
            await self.read(args.source_ids)
        elif name == "search_media":
            if getattr(self, "media_searches", 0) >= 2 or any(t.startswith("!") for t in args.query.split()):
                raise ValueError("Media search limit reached or unsupported query.")
            self.media_searches = getattr(self, "media_searches", 0) + 1
            response = await self.searx.search(args.query, args.category)
            results = rank_images(args.query, response.results) if args.category == "images" else response.results
            found = []
            for result in results:
                thumb = public_url(result.thumbnail or result.img_src)
                url = public_url(result.url)
                if not thumb or not url:
                    continue
                item = {"title": result.title, "url": url, "thumbnail": thumb,
                    "image": public_url(result.img_src), "host": result.host, "length": result.length}
                found.append(item)
                if len(found) >= (6 if args.category == "images" else 3):
                    break
            if not found:
                raise ValueError("No usable media results were returned by the image/video engines.")
            self.record("media", ("Images" if args.category == "images" else "Videos") + " · " + args.query, "",
                {"category": args.category, "items": found}, "Retrieved media results, linked to their original pages. Images are not proof of specifications or availability.")
        elif name == "show_cards":
            for sid in args.source_ids:
                if sid not in self.ui:
                    raise ValueError("Only structured tool results can be rendered as cards.")
                if sid not in self.shown:
                    self.emit("card", {"source_id": sid, **self.ui[sid]})
                    self.shown.add(sid)
        elif name == "weather":
            place = args.place or self.settings.home_location
            location = await weather.geocode(self.ext, place)
            if not location:
                raise ValueError("Couldn't resolve that weather location.")
            data = {**await weather.forecast(self.ext, location["latitude"], location["longitude"]),
                "place": weather.place_label(location)}
            self.record("weather", data["place"] + " forecast", "https://open-meteo.com/", data,
                "Forecast predictions, not observations for future hours. Use dated forecast_hours. No air-quality data.")
        elif name == "lookup_entity":
            data = await wiki.lookup(self.ext, args.name, fresh=True)
            if not data:
                raise ValueError("No matching entity; clarify the subject or search the web.")
            kept = {k: data[k] for k in ("title", "description", "qid", "kind", "tiles", "facts", "cast", "watch") if k in data}
            url = "https://www.wikidata.org/wiki/" + data["qid"] if data.get("qid") else data["wiki_url"]
            self.record("entity", data["title"], url, kept,
                "Fresh Wikipedia/Wikidata retrieval; the underlying claims may lag real events. Streaming links do not confirm availability.")
            add(self.evidence, data["title"] + " · Wikipedia summary", data["wiki_url"], data.get("extract", ""),
                kind="card", limitation="Wikipedia introductory summary; use Wikidata evidence for the structured facts.")
        elif name == "places":
            if args.category not in views.PLACE_TYPES:
                raise ValueError("Unsupported place category. Use cafe, restaurant, park, pharmacy, or another supported Archer category.")
            data = await views.places_payload(self.ext, args.category, args.area, not bool(args.area))
            if data.get("error"):
                raise ValueError(data["error"])
            self.record("places", data["label"] + " in " + data["area"], data["map"], data,
                "OSM has no ratings or reviews. Hours are listings, not live open status. Photon fallback has fewer fields. Distances are from the area center, not live user GPS.")
        elif name == "watch_providers":
            entity = self.ui.get(args.entity_source_id, {}).get("data", {})
            listing = entity.get("watch") or {}
            if not listing.get("tmdb") or not self.settings.tmdb_api_key:
                raise ValueError("No TMDB key or matching title ID. Regional availability could not be verified.")
            data = await watch.tmdb_providers(self.ext, self.settings.tmdb_api_key,
                listing["media"], str(listing["tmdb"]), self.settings.home_country, fresh=True)
            data = {**data, "country": self.settings.home_country}
            self.record("watch", entity.get("title", "Title") + " viewing options", "https://www.themoviedb.org/", data,
                "TMDB/JustWatch country-specific listing. Check the service before paying.")
        elif name == "calculate":
            data = utilities.calculate(args.expression) or utilities.convert_units(args.expression)
            if not data:
                raise ValueError("Unsupported calculation; use a bounded arithmetic expression or unit conversion.")
            self.record("calculation", args.expression, "", data, "Deterministic Archer calculation.")
        elif name == "loan_payment":
            rate = args.annual_rate / 1200
            payment = args.principal / args.months if rate == 0 else args.principal * rate / (-math.expm1(-args.months * math.log1p(rate)))
            data = {**args.model_dump(), "monthly_payment": round(payment, 2),
                "total_payment": round(payment * args.months, 2), "total_interest": round(payment * args.months - args.principal, 2),
                "assumptions": "Fixed annual nominal rate, monthly reducing balance; no fees, taxes or prepayment penalties."}
            self.record("calculation", f"Monthly loan payment · {args.months} months", "", data, data["assumptions"])
        self.emit("sources", {"sources": [{**e.payload(), "text": e.text[:800]} for e in self.evidence]})
        return {"new_source_ids": [e.id for e in self.evidence[before:]], "searches_left": 3-self.searches,
                "pages_left": 6-len(self.reads), "notice": "Tool result is in the evidence bundle; retain its limitations."}

    def record(self, kind, title, url, data, limitation):
        # Separate structured API evidence from an excerpt for the provider homepage.
        encoded = json.dumps(data, ensure_ascii=False)
        existing = next((e for e in self.evidence if e.title == title and e.kind == "tool" and e.text == encoded), None)
        if existing:
            return existing
        item = add(self.evidence, title, "", encoded, kind="tool", limitation=limitation)
        item.url = url
        self.ui[item.id] = {"kind": kind, "title": title, "data": data}
        return item

    async def read(self, ids, maximum=6):
        import asyncio
        import httpx
        selected = []
        for sid in ids:
            item = next((e for e in self.evidence if e.id == sid), None)
            if not item or not item.url or item.kind not in ("excerpt", "discussion") or "could not be read" in item.limitation:
                continue
            if item.url in self.reads or len(self.reads) >= maximum:
                continue
            self.reads.add(item.url)
            selected.append(item)
        async def one(item):
            try:
                text = await self.reader.read(item.url)
                # Keep relevant passages, rather than always feeding the first page section.
                terms = set(self.question.lower().split()) if hasattr(self, "question") else set()
                passages = [text[i:i+1800] for i in range(0, len(text), 1800)]
                passages.sort(key=lambda p: sum(t in p.lower() for t in terms if len(t) > 3), reverse=True)
                item.text = "\n\n".join(passages[:4])
                item.kind, item.limitation = "page", "Selected extracted passages; page may omit inaccessible content."
            except (ValueError, OSError, httpx.HTTPError, TimeoutError):
                item.limitation = "Page could not be read; only the search excerpt is available."
                self.emit("notice", {"text": item.title + ": couldn't read the page; keeping its excerpt."})
        await asyncio.gather(*(one(item) for item in selected))
