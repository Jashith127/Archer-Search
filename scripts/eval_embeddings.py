"""Evaluate Archer's history features against a real history export (scripts/export_history.py).

Everything runs locally through Archer's own History and Personalizer code. Nothing here sends
the export anywhere; results go to a JSON file next to it.

    uv run python scripts/eval_embeddings.py my-searches.json --model potion-base-8M --out eval.json
    uv run python scripts/eval_embeddings.py my-searches.json --model lexical

--model takes lexical, potion-base-8M, potion-retrieval-32M, ollama, or a folder holding any
Model2Vec model.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

# Search "engines" that are really site navigation recorded as keyword searches.
NAV_ENGINES = {"github.com", "x.com", "twitter.com", "mail.google.com", "www.linkedin.com", "web.whatsapp.com"}
WEB_ENGINES = {"www.google.com", "google.com", "duckduckgo.com", "archer.localhost", "localhost", "127.0.0.1", "www.bing.com",
               "bing.com", "search.brave.com", "www.startpage.com", "www.ecosia.org", "www.perplexity.ai"}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PATHLIKE = re.compile(r"^[\w.-]+(/[\w.-]+)+/?$")


def kind_of(entry: dict) -> str:
    """web | site (shopping, video and forum searches) | nav (paths, IDs, private mail)."""
    q, engine = entry["q"].strip(), entry["engine"]
    video_id = VIDEO_ID.match(q) and re.search(r"[A-Z]", q) and re.search(r"[a-z]", q) and not q.istitle()
    if engine in NAV_ENGINES or PATHLIKE.match(q) or video_id or re.match(r"^https?://", q):
        return "nav"
    return "web" if engine in WEB_ENGINES else "site"


def host(url: str) -> str:
    return (urlsplit(url).hostname or url.split("/")[2] if "://" in url else url).removeprefix("www.")


def load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for i, s in enumerate(data["searches"]):
        s = dict(s, idx=i, kind=kind_of(s), t=datetime.fromisoformat(s["at"]).timestamp())
        out.append(s)
    return out


def ambient_hosts(searches: list[dict], share: float = 0.012) -> set[str]:
    """Hosts that follow many unrelated searches (open tabs, AI chats, dev servers): time-window noise."""
    counts = Counter()
    for s in searches:
        counts.update({host(o["url"]) for o in s["opened"]})
    n = len(searches)
    return {h for h, c in counts.items() if c / n >= share and not h.endswith(("wikipedia.org",))} | {
        h for h in counts if re.fullmatch(r"[a-p]{32}", h) or h.startswith(("localhost", "127.0.0.1", "archer.localhost"))}


def clean_opened(s: dict, ambient: set[str], window: float = 120) -> list[dict]:
    """Pages plausibly opened from this search: linked by the browser, or soon after and not ambient."""
    keep = []
    for o in s["opened"]:
        h = host(o["url"])
        dt = datetime.fromisoformat(o["at"]).timestamp() - s["t"]
        if o["linked_by"] == "link" or (h not in ambient and dt <= window):
            keep.append(o)
    return keep


# ---------------------------------------------------------------- models
def embeddings_for(model: str):
    """An archer.embed.Embeddings configured for `model` (a name, or a folder with a Model2Vec model)."""
    from archer import embed
    from archer.config import settings
    emb = embed.Embeddings()
    folder = Path(model)
    if folder.is_dir():
        embed.STATIC_MODELS[folder.name] = "local"
        emb.folder = folder.parent
        settings.semantic_model = folder.name
    else:
        settings.semantic_model = model
    emb.warm()
    if emb.active().name != emb.configured().name:
        raise SystemExit(f"Model {model} isn't available (download failed?); refusing to evaluate word matching under its name.")
    return emb


# ------------------------------------------------------------- variants
# Searches on everyday topics that a tech-heavy history rarely covers; any match is a false one
# unless your history really has that topic (check the examples in the output).
NOVEL = ('how to grow tomatoes on a balcony', 'when to prune rose bushes', 'best fertilizer for money plant', 'how to file income tax return online', 'itr 2 vs itr 3 difference', 'what is capital gains tax on mutual funds', 'how to knit a scarf for beginners', 'crochet granny square pattern', 'symptoms of iron deficiency in women', 'how much water should a toddler drink', 'pregnancy diet first trimester', 'how to treat a sprained ankle at home', 'best time to visit ladakh', 'trekking routes in uttarakhand', 'visa requirements for schengen from india', 'how to make sourdough starter', 'eggless chocolate cake recipe', 'how to cook quinoa', 'difference between baking soda and baking powder', 'how to remove coffee stain from shirt', 'how to unclog a bathroom drain', 'best indoor plants for low light', 'how to train a puppy to sit', 'why does my cat sleep so much', 'aquarium nitrogen cycle explained', 'how to change a flat bike tyre', 'car insurance renewal online', 'how to check pf balance', 'what is a mutual fund sip', 'fixed deposit interest rates senior citizens', 'how to write a resignation letter', 'cover letter for teaching job', 'how to prepare for upsc prelims', 'neet syllabus 2027', 'photosynthesis light and dark reactions', 'how to balance chemical equations', 'pythagoras theorem proof', 'history of the mughal empire', 'causes of world war 1', 'who wrote the ramayana', 'meaning of the word ephemeral', 'synonyms for happy', 'how to learn french fast', 'best yoga poses for back pain', 'how many calories in a banana', 'marathon training plan for beginners', 'how to improve sleep quality', 'benefits of meditation', 'how to fold a fitted sheet', 'wedding gift ideas for sister', 'how to tie a tie', 'how to iron a shirt without iron', 'best sunscreen for oily skin', 'how to get rid of dandruff naturally', 'skincare routine for dry skin', 'how to apply for a passport in india', 'aadhaar card address change', 'how to register a two wheeler', 'rules of chess castling', 'how to play sudoku', 'crossword clue river in egypt', 'how to fish for trout', 'best birdwatching spots in karnataka', 'how to start a vegetable garden', 'compost bin at home', 'solar panel subsidy india', 'electricity bill calculation per unit', 'how to save money on groceries', 'monthly budget template', 'how to negotiate rent', 'rental agreement format', 'how to sell old furniture', 'how to clean a refrigerator', 'best washing machine settings for wool', 'how to descale a kettle', 'how to paint a room', 'wall putty vs primer', 'plumber near me', 'how to make cold brew coffee', 'masala chai recipe', 'how to make paneer at home', 'idli batter fermentation tips', 'how to store fresh coriander', 'weather in shimla in december', 'snowfall in manali', 'cheapest train from bangalore to chennai', 'irctc tatkal booking time', 'how to apply for driving licence', 'traffic fine check online', 'how to report lost phone in india', 'how to donate blood', 'nearest hospital emergency', 'first aid for burns', 'how to perform cpr', 'vitamin d deficiency symptoms', 'normal blood pressure range by age', 'how to lower cholesterol naturally', 'diabetes diet chart', 'thyroid symptoms in women', 'how to care for a newborn', 'baby sleep schedule 6 months', 'toddler tantrums how to handle', 'best board games for family', 'how to make origami crane', 'watercolor painting techniques', 'how to sharpen a pencil properly', 'calligraphy for beginners', 'how to write a haiku', 'famous poems by tagore', 'classical music raga yaman', 'how to tune a violin', 'piano scales for beginners', 'how to sing in tune', 'best books on stoicism', 'summary of the alchemist', 'who is the prime minister of japan', 'population of brazil', 'capital of australia', 'how volcanoes are formed', 'why is the sky blue', 'how do vaccines work')


def variants(queries: list[str], seed: int = 3) -> list[dict]:
    """Typos, spacing, dropped and reordered words, filler and half-typed prefixes of each search."""
    rng = random.Random(seed)
    near = {"a": "s", "s": "a", "e": "r", "r": "e", "i": "o", "o": "i", "n": "m", "m": "n", "t": "y", "l": "k"}

    def typo(q):
        letters = [i for i, c in enumerate(q) if c.isalpha()]
        if len(letters) < 4:
            return None
        i = rng.choice(letters[1:-1])
        op = rng.choice(["swap", "drop", "dup", "near"])
        if op == "swap" and i + 1 < len(q):
            return q[:i] + q[i + 1] + q[i] + q[i + 2:]
        if op == "drop":
            return q[:i] + q[i + 1:]
        if op == "dup":
            return q[:i] + q[i] + q[i:]
        return q[:i] + near.get(q[i].lower(), "x") + q[i + 1:]

    def split(q):
        ws = q.split()
        long = [k for k, w in enumerate(ws) if len(w) >= 7]
        if not long:
            return None
        k = rng.choice(long)
        ws[k] = ws[k][: len(ws[k]) // 2] + " " + ws[k][len(ws[k]) // 2:]
        return " ".join(ws)

    def drop(q):
        ws = q.split()
        if len(ws) < 3:
            return None
        del ws[rng.randrange(len(ws))]
        return " ".join(ws)

    def reorder(q):
        ws = q.split()
        if len(ws) < 3:
            return None
        shuffled = ws[:]
        rng.shuffle(shuffled)
        return " ".join(shuffled) if shuffled != ws else None

    kinds = {
        "typo": typo,
        "two typos": lambda q: typo(typo(q) or "") or None,
        "spaces removed": lambda q: q.replace(" ", "") if " " in q else None,
        "word split": split, "word dropped": drop, "word order": reorder,
        "filler added": lambda q: rng.choice(["how to ", "best ", "what is ", "", ""]) + q + rng.choice([" reddit", " 2026", " review", " free", " explained"]),
        "typed prefix": lambda q: q[: max(3, int(len(q) * 0.6))] if len(q) >= 8 else None,
    }
    out = []
    for q in queries:
        for kind, make in kinds.items():
            v = make(q)
            if v and v.strip().casefold() != q.casefold():
                out.append({"kind": kind, "q": v, "target": q})
    return out


def near_misses(queries: list[str]) -> list[dict]:
    """Each search with a model or version number, with that number changed: a different thing."""
    out = []
    for q in queries:
        m = re.search(r"\d+", q)
        if m:
            n = m.group()
            other = str(int(n) + 1) if len(n) < 4 else n[:-2] + str((int(n[-2:]) + 10) % 100).zfill(2)
            out.append({"a": q, "b": q[: m.start()] + other + q[m.end():]})
    return out


# ------------------------------------------------------------- runs
async def evaluate(searches: list[dict], model: str, labels: Path | None = None, without: tuple[str, ...] = ()) -> dict:
    import tempfile
    import time as clock
    import numpy as np
    import archer.personalize as P
    from archer.history import History, norm

    emb = embeddings_for(model)
    for rule in without:
        P.RULES[rule] = False
    ambient = ambient_hosts(searches) - {"youtube.com"}
    web = [s for s in searches if s["kind"] != "nav"]
    unique = list({norm(s["q"]): s["q"].strip() for s in web if s["q"].strip()}.values())
    report: dict = {"model": emb.active().name, "without": list(without), "searches": len(web), "distinct": len(unique)}

    # 1. Matching against the whole history: variants, new topics, near-misses.
    store = History(Path(tempfile.mkdtemp()) / "eval.sqlite3")
    pers = P.Personalizer(store, emb)
    for s in web:  # real times: a search's neighbours in time are part of its meaning
        store.record_search(s["q"], now=s["t"])
    cache: dict[str, tuple] = {}

    async def scores(q):
        if q not in cache:
            _, _, thr, _, sc = await pers._past_scores(q)
            cache[q] = (thr, sc)
        return cache[q]

    per = defaultdict(lambda: [0, 0, 0])
    for run in variants(unique):
        thr, sc = await scores(run["q"])
        target = sc.get(norm(run["target"]), -1.0)
        per[run["kind"]][0] += 1
        per[run["kind"]][1] += target >= thr
        per[run["kind"]][2] += bool(sc) and target >= max(sc.values())
    report["variants"] = {k: {"runs": n, "found": round(f / n, 3), "best_match": round(b / n, 3)} for k, (n, f, b) in per.items()}
    false = []
    for q in NOVEL:
        thr, sc = await scores(q)
        hits = [k for k, s in sc.items() if s >= thr]
        if hits:
            false.append({"search": q, "matched": max(hits, key=sc.get)})
    report["new_topics"] = {"runs": len(NOVEL), "falsely_matched": round(len(false) / len(NOVEL), 3), "examples": false}
    near = near_misses(unique)
    known = pers.known_word()
    report["near_misses"] = {"runs": len(near), "kept_out_of_recall": round(sum(P.different_thing(n["b"], n["a"], known) for n in near) / max(len(near), 1), 3)}
    topics = await pers.topics(limit=500)
    report["topics"] = {"groups": len(topics), "searches_grouped": sum(t["size"] for t in topics),
                        "largest": [{"label": t["label"], "size": t["size"], "examples": t["queries"][:5]} for t in topics[:15]]}

    # 2. Hand labels, if given: a JSON list of {"a", "b", "y"} with y 2 = same need, 1 = related, 0 = unrelated.
    if labels:
        rows = json.loads(labels.read_text(encoding="utf-8"))
        have = {e["norm"] for e in store.queries()}
        for r in rows:
            for q in (r["a"], r["b"]):
                if norm(q) not in have:
                    store.record_search(q, now=1.0)  # outside every session
        cache.clear()
        judged = []
        for r in rows:
            thr, sc = await scores(r["a"])
            judged.append((r["y"], sc.get(norm(r["b"]), -1.0) >= thr))
        shown = [y for y, ok in judged if ok]
        report["labelled_pairs"] = {"pairs": len(judged), "shown": len(shown),
                                    "precision": round(float(np.mean([y >= 1 for y in shown])), 3) if shown else None,
                                    "same_need_found": round(float(np.mean([ok for y, ok in judged if y == 2])), 3),
                                    "related_found": round(float(np.mean([ok for y, ok in judged if y >= 1])), 3)}

    # 3. Replay: every search in date order, with history as it was then.
    store = History(Path(tempfile.mkdtemp()) / "replay.sqlite3")
    pers = P.Personalizer(store, emb)
    now = {"t": 0.0}
    real_time = P.time.time
    P.time.time = lambda: now["t"]
    first_from: dict[str, str] = {}
    stats, times = Counter(), []
    try:
        for s in web:
            now["t"] = s["t"]
            opened = clean_opened(s, ambient)
            started = clock.perf_counter()
            out = await pers.personalize(s["q"], [])
            times.append(clock.perf_counter() - started)
            stats["with_related"] += bool(out.related)
            stats["related_shown"] += len(out.related)
            stats["with_recall"] += bool(out.recall)
            again = [o["url"] for o in opened if o["url"] in first_from and norm(first_from[o["url"]]) != norm(s["q"])]
            if again:
                stats["reopened"] += 1
                stats["reopened_in_recall"] += any(u in {r["url"] for r in out.recall} for u in again)
                stats["reopened_site_in_recall"] += any(host(u) in {r["host"] for r in out.recall} for u in again)
            sid = store.record_search(s["q"], now=s["t"])
            for o in opened:
                store.record_click(sid, o["url"], o["title"], "", now=datetime.fromisoformat(o["at"]).timestamp())
                first_from.setdefault(o["url"], s["q"])
    finally:
        P.time.time = real_time
    n = len(web)
    report["replay"] = {"searches": n, "with_related": round(stats["with_related"] / n, 3), "related_per_search": round(stats["related_shown"] / n, 2),
                        "with_recall": round(stats["with_recall"] / n, 3), "reopened_pages": stats["reopened"],
                        "reopened_page_in_recall": round(stats["reopened_in_recall"] / max(stats["reopened"], 1), 3),
                        "reopened_site_in_recall": round(stats["reopened_site_in_recall"] / max(stats["reopened"], 1), 3),
                        "ms_median": round(1000 * float(np.median(times)), 1), "ms_p95": round(1000 * float(np.quantile(times, 0.95)), 1)}
    return report


def main() -> None:
    import asyncio
    ap = argparse.ArgumentParser(description="Evaluate Archer's history matching on your own exported history.")
    ap.add_argument("export", type=Path, help="JSON from scripts/export_history.py")
    ap.add_argument("--model", default="potion-base-8M", help="lexical, potion-base-8M, potion-retrieval-32M, ollama, or a Model2Vec folder")
    ap.add_argument("--labels", type=Path, help="optional hand-labelled pairs (see evaluate())")
    ap.add_argument("--without", default="", help="comma-separated matching rules to switch off (see personalize.RULES)")
    ap.add_argument("--out", type=Path, default=Path("archer-eval.json"))
    args = ap.parse_args()
    without = tuple(r for r in args.without.split(",") if r)
    report = asyncio.run(evaluate(load(args.export), args.model, args.labels, without))
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("topics",)}, ensure_ascii=False, indent=1)[:4000])
    print(f"Full report in {args.out.resolve()}")


if __name__ == "__main__":
    main()
