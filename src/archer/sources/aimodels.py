"""AI model panels from Epoch AI's benchmarking hub (CC BY 4.0, refreshed daily).

The whole dataset is one ~2.5 MB zip, so it is downloaded at most once a day into DATA_DIR and
matched locally: a model query costs no network round trip once the file is on disk.
The Epoch Capabilities Index (ECI) puts every model on one scale, which is what makes a
"this model vs. its predecessors vs. its rivals" chart possible across labs and years.
"""

import asyncio
import csv
import io
import logging
import math
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date

import httpx

from archer.config import DATA_DIR
from archer.sources import modelprices

log = logging.getLogger("archer")

URL = "https://epoch.ai/data/benchmark_data.zip"
SOURCE_URL = "https://epoch.ai/benchmarks"
CACHE_FILE = DATA_DIR / "epoch_benchmarks.zip"
MAX_AGE = 24 * 3600
_ECI = "epoch_capabilities_index/eci_scores.csv"
_SCORES = "epoch_capabilities_index/processed_data_for_eci.csv"
_BENCH_META = "benchmark_metadata.csv"  # per benchmark: its own results file, score column, random baseline
_VERSIONS = "model_metadata.csv"         # model_version ("claude-opus-5_high") -> model ("Claude Opus 5")

# Benchmarks worth a tab, best-known first: (Epoch names, label, what it measures). The first name
# found in the zip is the one used; the rest are spellings Epoch has used for the same benchmark.
# All are fraction-correct scores, so they share a 0-100% axis.
BENCHMARKS = [
    (("GPQA diamond",), "GPQA Diamond", "PhD-level science questions"),
    (("SWE-Bench verified", "SWE-bench Verified", "SWE-bench Verified (Bash Only)", "SWE-bench verified bash only"),
     "SWE-bench Verified", "Real GitHub issues, fixed end to end"),
    (("FrontierMath-Tiers-1-3-v2-Private", "FrontierMath-2025-02-28-Private"), "FrontierMath", "Unpublished research-level maths problems"),
    (("HLE",), "Humanity's Last Exam", "Expert-written questions across many fields"),
    (("ARC-AGI-2",), "ARC-AGI-2", "Novel visual reasoning puzzles"),
    (("Terminal Bench", "Terminal-Bench 2.0"), "Terminal-Bench", "Agentic tasks in a command-line environment"),
    (("SimpleQA Verified",), "SimpleQA Verified", "Short factual questions, no browsing"),
    (("OTIS Mock AIME 2024-2025",), "AIME-style maths", "Competition maths (OTIS mock AIME)"),
    (("APEX-Agents",), "APEX-Agents", "Long-horizon professional agent tasks"),
    (("MMLU",), "MMLU", "Multiple-choice knowledge across 57 subjects"),
    (("MATH level 5",), "MATH Level 5", "Hardest tier of competition maths"),
    (("GSM8K",), "GSM8K", "Grade-school maths word problems"),
]
MAX_TABS = 4
PINNED = {"SWE-bench Verified"}  # always gets a tab when Epoch has results, even if this model wasn't run on it


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


# Any spelling of a benchmark -> its label, which is what Model.scores and Catalog.points are keyed by.
_ALIASES = {_norm(n): label for names, label, _ in BENCHMARKS for n in names}


# Favicons for the developer badge (served through /api/favicon).
ORG_SITES = {
    "OpenAI": "openai.com", "Anthropic": "anthropic.com", "Google DeepMind": "deepmind.google",
    "Google": "google.com", "Meta AI": "meta.com", "xAI": "x.ai", "DeepSeek": "deepseek.com",
    "Alibaba": "qwen.ai", "Mistral AI": "mistral.ai", "Moonshot": "moonshot.ai", "Z.ai (Zhipu AI)": "z.ai",
    "Microsoft": "microsoft.com", "MiniMax": "minimax.io", "Amazon": "amazon.com", "Nvidia": "nvidia.com",
    "Thinking Machines": "thinkingmachines.ai", "01.AI": "01.ai",
}

# Words that frame a model query without naming the model ("gpt 5 release date").
FILLER = {"what", "whats", "is", "the", "when", "was", "were", "released", "release", "date", "who", "made",
          "model", "models", "llm", "ai", "benchmark", "benchmarks", "score", "scores", "eci", "specs",
          "performance", "capabilities", "how", "good", "about", "tell", "me", "of", "an", "by",
          "openai", "anthropic", "google", "deepmind", "meta", "xai", "alibaba", "moonshot", "zhipu"}


@dataclass
class Model:
    name: str
    org: str
    released: date
    weights: str  # "Open weights" | "Closed weights" | "Other"
    eci: float
    scores: dict[str, float] = field(default_factory=dict)
    line: tuple = ()  # (org, words that name the product line); versions within a line are comparable
    version: float | None = None
    family: str = ""
    rank: int = 0
    price: float | None = None  # blended $ per million tokens; set per panel from the price table
    key: int = 0  # position in the chart being built; ties a mark to its show/hide toggle


@dataclass
class Point:
    """One row of a benchmark's own results file: a model version, often at one reasoning effort."""
    released: date
    value: float
    title: str
    model: str  # ECI model name; "" when the version isn't linked to one
    price: float | None = None  # set only on the price axis, where each point is one model


@dataclass
class Catalog:
    models: list[Model]
    keys: dict[tuple, Model]
    as_of: str  # dataset build date
    loaded_at: float
    points: dict[str, list[Point]] = field(default_factory=dict)  # benchmark -> every result Epoch plots
    baselines: dict[str, float] = field(default_factory=dict)     # benchmark -> random-guessing score


_catalog: Catalog | None = None
_refresh: asyncio.Task | None = None
_failed_at = 0.0  # last failed download; with no data at all, retry at most every RETRY seconds
RETRY = 600


# ------------------------------------------------------------------ parsing

_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_SIZE = re.compile(r"(?<![a-z0-9.])\d+(?:\.\d+)?b(?![a-z])")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower().replace("_", " "))


def _key(text: str) -> tuple:
    return tuple(sorted(_tokens(text)))


def _anatomy(name: str, org: str) -> tuple[tuple, float | None, str]:
    """(line, version, family) from a display name like "Claude Opus 4.5" or "GPT-6.1 Sol"."""
    base = re.sub(r"\s*\([^)]*\)", "", name).strip()
    low = _SIZE.sub(" ", base.lower())
    version, family, words = None, "", []
    for m in _TOKEN.finditer(low):
        tok = m.group()
        if tok[0].isdigit():
            if version is None and not re.fullmatch(r"\d{4}", tok):  # "0813" is a build stamp
                version = float(tok) if tok.count(".") <= 1 else None
                major = tok.split(".")[0]
                family = base[:m.start()] + major
            continue
        # A lone letter glued to the version ("K2", "V4", "o3") belongs to the version, not the line.
        glued = m.end() < len(low) and low[m.end()].isdigit() and len(tok) == 1
        if not glued:
            words.append(tok)
    family = family.strip(" -")
    if family.lower() == base.lower():
        # The name is its own generation ("Claude Opus 5"): name the line instead.
        family = re.sub(r"[\s-]*[A-Za-z]?\d.*$", "", base).strip(" -")
    return (org, tuple(sorted(set(words)))), version, family


def _rows(z: zipfile.ZipFile, name: str) -> list[dict]:
    return list(csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8")))


def _bench_points(z: zipfile.ZipFile) -> tuple[dict[str, list[Point]], dict[str, float]]:
    names = set(z.namelist())
    meta = {r["benchmark"]: r for r in _rows(z, _BENCH_META)} if _BENCH_META in names else {}
    versions = {r["model_version"]: r for r in _rows(z, _VERSIONS)} if _VERSIONS in names else {}
    by_norm = {_norm(b): r for b, r in meta.items()}
    points: dict[str, list[Point]] = {}
    baselines: dict[str, float] = {}
    for spellings, bench, _ in BENCHMARKS:
        m = next((r for n in spellings if (r := by_norm.get(_norm(n))) and r.get("source_file") in names), None)
        if not m:
            continue
        try:
            scale = float(m.get("scale") or 1) or 1
            if (base := float(m.get("random_baseline") or 0)) >= .02:
                baselines[bench] = base
        except ValueError:
            scale = 1
        found = []
        for r in _rows(z, m["source_file"]):
            try:
                value = float(r[m["score_column"]]) / scale
                released = date.fromisoformat(r["Release date"][:10])
            except (KeyError, ValueError, TypeError):
                continue
            version = r.get("Model version") or ""
            linked = versions.get(version, {})
            title = linked.get("display_name") or r.get("Name") or version
            found.append(Point(released, value, title, linked.get("model_group") or ""))
        if found and max(p.value for p in found) > 1.5:  # a file that reports percentages
            for p in found:
                p.value /= 100
        points[bench] = found
    return points, baselines


def _parse(blob: bytes) -> Catalog:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        eci_rows = _rows(z, _ECI)
        score_rows = _rows(z, _SCORES)
        points, baselines = _bench_points(z)
        as_of = date(*z.getinfo(_ECI).date_time[:3]).isoformat()

    scores: dict[str, dict[str, float]] = {}
    for r in score_rows:
        if bench := _ALIASES.get(_norm(r.get("benchmark") or "")):
            try:
                value = float(r["performance"])
                if value > scores.setdefault(r["Model"], {}).get(bench, -1):
                    scores[r["Model"]][bench] = value
            except (KeyError, ValueError):
                continue
    # Prefer the benchmark's own file: a model's best variant there is the score its graph shows.
    for bench, pts in points.items():
        best: dict[str, float] = {}
        for pt in pts:
            if pt.model:
                best[pt.model] = max(best.get(pt.model, 0.0), pt.value)
        for name, value in best.items():
            scores.setdefault(name, {})[bench] = value

    models = []
    for r in eci_rows:
        try:
            m = Model(name=r["Display name"] or r["Model"], org=(r["Organization"] or "").split(",")[0].strip(),
                      released=date.fromisoformat(r["date"]), weights=r.get("Accessibility group") or "",
                      eci=float(r["eci"]), scores=scores.get(r["Model"], {}))
        except (KeyError, ValueError):
            continue
        m.line, m.version, m.family = _anatomy(m.name, m.org)
        models.append(m)
    models.sort(key=lambda m: -m.eci)
    for i, m in enumerate(models, 1):
        m.rank = i

    # Exact names first; then looser aliases, where the newest (then strongest) model wins.
    keys: dict[tuple, Model] = {}
    for m in models:
        keys.setdefault(_key(m.name), m)
    aliases: dict[tuple, Model] = {}
    for m in models:
        base = re.sub(r"\s*\([^)]*\)", "", m.name)
        variants = {base, re.sub(r"\b\d{4}\b", "", base), _SIZE.sub(" ", base.lower())}
        if base.lower().startswith("claude "):
            variants |= {v.lower().removeprefix("claude ") for v in set(variants)}
        for v in variants:
            k = _key(v)
            if k and k not in keys and (k not in aliases or (m.released, m.eci) > (aliases[k].released, aliases[k].eci)):
                aliases[k] = m
    keys.update(aliases)
    return Catalog(models=models, keys=keys, as_of=as_of, loaded_at=time.time(), points=points, baselines=baselines)


# ------------------------------------------------------------------ loading

async def _download(client: httpx.AsyncClient) -> Catalog:
    resp = await client.get(URL, timeout=30)
    resp.raise_for_status()
    cat = await asyncio.to_thread(_parse, resp.content)  # validate before replacing the cached copy
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_bytes(resp.content)
    tmp.replace(CACHE_FILE)
    return cat


async def _refresh_now(client: httpx.AsyncClient) -> None:
    global _catalog, _failed_at
    try:
        _catalog = await _download(client)
    except (httpx.HTTPError, zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        log.warning("epoch model data refresh failed: %s", type(exc).__name__)
        _failed_at = time.monotonic()
        if _catalog:
            _catalog.loaded_at = time.time() - MAX_AGE + 3600  # retry in an hour, keep serving the old data


def _start_refresh(client: httpx.AsyncClient) -> asyncio.Task:
    global _refresh
    if _refresh is None or _refresh.done():
        _refresh = asyncio.create_task(_refresh_now(client))
    return _refresh


async def ensure(client: httpx.AsyncClient, wait: float = 6) -> Catalog | None:
    """The catalog, loading it from disk or Epoch as needed. Never raises; None means unavailable."""
    global _catalog
    if _catalog is None and CACHE_FILE.exists():
        try:
            _catalog = await asyncio.to_thread(_parse, CACHE_FILE.read_bytes())
            _catalog.loaded_at = CACHE_FILE.stat().st_mtime
        except (zipfile.BadZipFile, KeyError, OSError, ValueError):
            _catalog = None
    if _catalog is None:
        if time.monotonic() - _failed_at < RETRY and _failed_at:
            return None
        try:  # first run: wait a little, but let the download finish in the background regardless
            await asyncio.wait_for(asyncio.shield(_start_refresh(client)), wait)
        except asyncio.TimeoutError:
            return None
    elif time.time() - _catalog.loaded_at > MAX_AGE:
        _start_refresh(client)  # serve the day-old data now; swap in the fresh copy when it lands
    return _catalog


# ------------------------------------------------------------------ lookup

def find(cat: Catalog, text: str) -> Model | None:
    tokens = _tokens(text)
    if not tokens or len(tokens) > 12:
        return None
    if model := cat.keys.get(tuple(sorted(tokens))):
        if len("".join(tokens)) > 3:
            return model
    core = [t for t in tokens if t not in FILLER]
    if core and core != tokens:
        # Short names ("o3") only count when the query says it's about a model.
        return cat.keys.get(tuple(sorted(core)))
    return None


def _ago(d: date, today: date) -> str:
    days = (today - d).days
    if days < 0:
        return "upcoming"
    if days < 14:
        return "this week" if days < 7 else "last week"
    if days < 60:
        return f"{days // 7} weeks ago"
    months = round(days / 30.44)
    if months < 24:
        return f"{months} months ago"
    return f"{months // 12} years ago"


def _money(v: float) -> str:
    return f"${v:.2f}" if v < 10 else f"${v:.0f}"


def _row(m: Model, role: str, text: str, key: int, plotted: bool = True) -> dict:
    return {"name": m.name, "org": m.org, "site": ORG_SITES.get(m.org, ""), "role": role,
            "text": text, "date": m.released.strftime("%b %Y"), "key": key, "plotted": plotted,
            "price": f"{_money(m.price)}/M" if m.price else ""}


# Plot geometry, in SVG user units (the SVG scales to its column).
W, H = 760, 300
_L, _R, _T, _B = 46, 16, 14, 30
_PRICE_TICKS = [.01, .03, .1, .3, 1, 3, 10, 30, 100, 300]


def place_labels(marks: list[dict], right: float, top: float, bottom: float) -> None:
    """Put each label beside its dot, nudged up or down until it clears every other label and dot.

    archer.js runs the same pass with measured text widths whenever a model is shown or hidden,
    so the remaining labels close up instead of leaving gaps."""
    placed: list[tuple[float, float, float, float]] = []
    dots = [(mk["x"] - 7, mk["x"] + 7, mk["y"] - 7, mk["y"] + 7) for mk in marks]
    order = sorted(marks, key=lambda mk: (mk["role"] != "self", mk["y"]))
    for mk in order:
        width = 6.3 * len(mk["label"]) + 2
        start = mk["x"] + 9 + width <= right
        mk["anchor"] = "start" if start else "end"
        mk["lx"] = round(mk["x"] + 9 if start else mk["x"] - 9, 1)
        x0, x1 = (mk["lx"], mk["lx"] + width) if start else (mk["lx"] - width, mk["lx"])
        own = (mk["x"] - 7, mk["x"] + 7, mk["y"] - 7, mk["y"] + 7)
        chosen = None
        for step in range(0, 17):
            ly = mk["y"] + 4 + (13 * ((step + 1) // 2) * (1 if step % 2 else -1) if step else 0)
            if not top + 10 <= ly <= bottom - 2:
                continue
            box = (x0, x1, ly - 10, ly + 3)
            if not any(box[0] < o[1] and o[0] < box[1] and box[2] < o[3] and o[2] < box[3]
                       for o in placed + [d for d in dots if d != own]):
                chosen = ly
                break
        mk["ly"] = round(chosen if chosen is not None else min(max(mk["y"] + 4, top + 10), bottom - 2), 1)
        placed.append((x0, x1, mk["ly"] - 10, mk["ly"] + 3))
        mk["leader"] = abs(mk["ly"] - 4 - mk["y"]) > 6


def _plot(population: list[Point], cast: list[tuple[Model, str]], score, percent: bool, focus: date,
          baseline: float | None = None, by_price: bool = False) -> dict:
    """Score against release date, or against API price when the chart's models have prices.

    Every other result is a faint dot; the highlighted cast is labelled on top. On the price axis
    a dashed line joins the best-value models: nothing else scores higher for less."""
    if by_price:
        cast = [(m, role) for m, role in cast if m.price]
        pts = [p for p in population if p.price]
        xs = [math.log10(p.price) for p in pts] + [math.log10(m.price) for m, _ in cast]
        x0, x1 = min(xs) - .12, max(xs) + .12
        if x1 - x0 < 1:
            mid = (x0 + x1) / 2
            x0, x1 = mid - .5, mid + .5
        xof = lambda price: math.log10(price)  # noqa: E731
    else:
        start = date.fromordinal(focus.toordinal() - 2 * 365)  # two years of context keeps the recent cluster legible
        pts = [p for p in population if p.released >= start]
        dates = [p.released for p in pts] + [m.released for m, _ in cast]
        x0, x1 = min(dates).toordinal() - 20, max(dates).toordinal() + 20
        xof = lambda d: d.toordinal()  # noqa: E731
    values = [p.value for p in pts] + [score(m) for m, _ in cast]
    if percent:
        y0, y1, step = 0.0, 100.0, 25
    else:
        y0, y1 = math.floor(min(values) / 10) * 10, math.ceil(max(values) / 10) * 10
        step = 10 if y1 - y0 <= 50 else 20
    pw, ph = W - _L - _R, H - _T - _B

    def sx(v) -> float:
        return round(_L + (xof(v) - x0) / (x1 - x0) * pw, 1)

    def sy(v: float) -> float:
        return round(_T + (1 - (v - y0) / (y1 - y0)) * ph, 1)

    def fmt(v: float) -> str:
        return f"{v:.1f}%" if percent else f"{v:.0f}"

    grid = [{"y": sy(v), "label": f"{v:.0f}%" if percent else f"{v:.0f}"}
            for v in range(int(y0), int(y1) + 1, step)]
    if by_price:
        ticks = [t for t in _PRICE_TICKS if x0 < math.log10(t) < x1]
        xt = [{"x": sx(t), "label": _money(t).replace(".00", "")} for t in ticks]
        dots = [{"x": sx(p.price), "y": sy(p.value),
                 "title": f"{p.title} · {_money(p.price)}/M tokens · {fmt(p.value)}"} for p in pts]
    else:
        first, last = date.fromordinal(x0), date.fromordinal(x1)
        months = (1, 7) if (x1 - x0) < 2.5 * 365 else (1,)
        xt = [{"x": sx(d), "label": str(d.year) if d.month == 1 else f"Jul {d:%y}"}
              for y in range(first.year, last.year + 1) for mo in months
              if first < (d := date(y, mo, 1)) < last]
        dots = [{"x": sx(p.released), "y": sy(p.value), "title": f"{p.title} · {p.released:%b %Y} · {fmt(p.value)}"}
                for p in pts]

    marks = []
    for i, (m, role) in enumerate(cast):
        where = f"{_money(m.price)}/M tokens" if by_price else f"{m.released:%b %Y}"
        marks.append({"x": sx(m.price if by_price else m.released), "y": sy(score(m)), "role": role, "label": m.name,
                      "key": m.key, "title": f"{m.name} · {m.org} · {where} · {fmt(score(m))}"})
    place_labels(marks, W - _R, _T, _T + ph)
    marks.sort(key=lambda mk: mk["role"] == "self")  # the searched model draws last, on top

    frontier = ""
    if by_price:
        best, line = -1.0, []
        for price, value in sorted([(p.price, p.value) for p in pts] + [(m.price, score(m)) for m, _ in cast],
                                  key=lambda pv: (pv[0], -pv[1])):
            if value > best:
                best = value
                line.append(f"{sx(price)},{sy(value)}")
        frontier = " ".join(line) if len(line) > 1 else ""
    base = {"y": sy(baseline * 100), "label": f"Random guessing ({baseline * 100:.0f}%)"} if baseline else None
    return {"w": W, "h": H, "left": _L, "right": W - _R, "top": _T, "bottom": _T + ph, "grid": grid, "xt": xt,
            "dots": dots, "marks": marks, "base": base, "by_price": by_price, "frontier": frontier,
            "x_label": "API price, $ per million tokens (3:1 input:output)" if by_price else ""}


def _priced(models: list[Model]) -> bool:
    """The price axis is worth drawing once at least three of the highlighted models have a price."""
    return sum(1 for m in models if m.price) >= 3


def panel(cat: Catalog, model: Model, today: date | None = None, prices: dict[tuple, float] | None = None) -> dict:
    today = today or date.today()
    for m in cat.models:
        m.price = modelprices.lookup(prices or {}, m.name)
    same_line = [m for m in cat.models if m.line == model.line and m.version is not None and m is not model]
    earlier: dict[float, Model] = {}
    for m in sorted(same_line, key=lambda m: m.released):
        if model.version is not None and m.version < model.version:
            earlier[m.version] = m  # latest snapshot per version
    previous = [earlier[v] for v in sorted(earlier, reverse=True)[:2]]
    later = sorted((m for m in same_line if model.version is not None and m.version > model.version),
                   key=lambda m: (m.version, m.released))
    successor = later[0] if later else None

    # Rivals: each other lab's strongest model from the months around this release.
    best: dict[str, Model] = {}
    for m in cat.models:
        if m.org and m.org != model.org and -210 <= (m.released - model.released).days <= 30:
            if m.org not in best or m.eci > best[m.org].eci:
                best[m.org] = m
    rivals = sorted(best.values(), key=lambda m: -m.eci)[:4]

    cast = [(model, "self")] + [(m, "prev") for m in previous] + [(m, "rival") for m in rivals]
    by_price = _priced([m for m, _ in cast])
    for i, (m, _) in enumerate(cast):
        m.key = i
    eci_pop = [Point(m.released, m.eci, f"{m.name} · {m.org}", m.name, m.price) for m in cat.models
               if m.name not in {c.name for c, _ in cast}]
    charts = [{"id": "eci", "label": "Capability index", "about": "Epoch Capabilities Index: one scale across many benchmarks",
               "rows": sorted((_row(m, role, f"{m.eci:.0f}", i, not by_price or bool(m.price)) for i, (m, role) in enumerate(cast)),
                              key=lambda r: -float(r["text"])),
               "plot": _plot(eci_pop, cast, lambda m: m.eci, percent=False, focus=model.released, by_price=by_price),
               "count": sum(1 for p in eci_pop if p.price) + sum(1 for m, _ in cast if m.price) if by_price else len(cat.models)}]
    for _, bench, about in BENCHMARKS:
        tested_here = bench in model.scores
        if len(charts) > MAX_TABS or not (tested_here or (bench in PINNED and bench in cat.points)):
            continue
        rows = [(m, role) for m, role in cast if bench in m.scores]
        if len(rows) < 4:  # thin coverage: add the strongest contemporaries that were tested on it
            seen = {id(m) for m, _ in rows}
            extra = [m for m in cat.models if bench in m.scores and id(m) not in seen
                     and -240 <= (m.released - model.released).days <= 60]
            rows += [(m, "rival") for m in sorted(extra, key=lambda m: -m.eci)[:6 - len(rows)]]
        if len(rows) < 3:
            continue
        for i, (m, _) in enumerate(rows):
            m.key = i
        shown = {m.name for m, _ in rows}
        chart_by_price = _priced([m for m, _ in rows])
        if chart_by_price:
            # One dot per priced model at its best setting; reasoning-effort variants share a price.
            tested = [Point(m.released, m.scores[bench] * 100, f"{m.name} · {m.org}", m.name, m.price)
                      for m in cat.models if bench in m.scores and m.price and m.name not in shown]
            count = len(tested) + sum(1 for m, _ in rows if m.price)
        else:
            raw = cat.points.get(bench) or [Point(m.released, m.scores[bench], m.name, m.name)
                                            for m in cat.models if bench in m.scores]
            # The cast's own variants would sit under their marks, so they're left out of the dots.
            tested = [Point(p.released, p.value * 100, p.title, p.model) for p in raw if p.model not in shown]
            count = len(raw)
        about_here = about if tested_here else f"{about}. {model.name} has no result here yet"
        charts.append({"id": re.sub(r"[^a-z0-9]+", "-", bench.lower()).strip("-"), "label": bench, "about": about_here,
                       "rows": sorted((_row(m, role, f"{m.scores[bench] * 100:.1f}%", i, not chart_by_price or bool(m.price))
                                       for i, (m, role) in enumerate(rows)),
                                      key=lambda r: -float(r["text"].rstrip("%"))),
                       "plot": _plot(tested, rows, lambda m, b=bench: m.scores[b] * 100, percent=True,
                                     focus=model.released, baseline=cat.baselines.get(bench), by_price=chart_by_price),
                       "count": count})

    return {
        "name": model.name, "org": model.org, "site": ORG_SITES.get(model.org, ""),
        "released": _day(model.released),
        "released_ago": _ago(model.released, today), "family": model.family,
        "weights": model.weights if model.weights in ("Open weights", "Closed weights") else "",
        "eci": f"{model.eci:.0f}", "rank": model.rank, "total": len(cat.models),
        "price": f"{_money(model.price)}" if model.price else "",
        "previous": [m.name for m in previous], "successor": successor.name if successor else "",
        "has_prev": bool(previous), "has_rivals": bool(rivals),
        "charts": charts, "as_of": _day(date.fromisoformat(cat.as_of)), "source_url": SOURCE_URL,
        "priced": any(c["plot"]["by_price"] for c in charts), "price_source_url": modelprices.SOURCE_URL,
    }


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"
