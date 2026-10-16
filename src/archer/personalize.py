"""Personal search: what your history and site preferences change about a results page.

Everything is computed locally from `history.py` and `embed.py`; no model call, no network.

- **Ranking** blends the provider's order with meaning and with you:
  `score = 0.6·rank + 0.25·semantic + 0.15·affinity (+ site preference, + revisit)`, where
  *rank* is the provider's position (1, 0.8, 0.67, …), *semantic* is how close the result's title and snippet are to
  the query (normalised across the page), and *affinity* is how often you opened this site after
  searches like this one, fading over 30 days. Raised sites move up, lowered ones down, blocked
  ones disappear. The provider's order still carries the most weight, so exact matches (model
  numbers, names) stay near the top; meaning and habit reorder close calls, and a site you keep
  opening for a topic climbs a few places for that topic only.
- **Recall**: pages you opened before for searches like this one ("You found these before").
- **Related**: your earlier searches that mean the same thing, worded differently, and the ones
  from which you opened a page that is in this search's results.

Two searches "mean the same" when 0.7·embedding similarity + 0.3·word similarity clears a cutoff
(the two are stored as one concatenated vector, so a single dot product gives the blend), or when
they are the same after removing spaces and punctuation, or one typo apart. Calculator-style
searches (sums, units, currency) and one- or two-letter fragments never match anything, and a
one-word search needs a stronger match. These rules and the cutoffs come from an evaluation on a
real 1,400-search history (`scripts/eval_embeddings.py`, `docs/history-evaluation.md`).
- **Suggestions**: your past searches first in the address bar, by how often and how lately.
- **Meanings**: an "Other meanings" choice is remembered for the next search of that name.
- **Topics**: history grouped by meaning for the history page.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import Counter, OrderedDict
from difflib import SequenceMatcher
from functools import lru_cache
from dataclasses import dataclass, field, replace

import numpy as np

from archer.config import settings
from archer.embed import LEXICAL, Embeddings, embeddings as default_embeddings, words
from archer.history import History, host_of, norm

# Similarity above which two searches count as "about the same thing". Every model spreads its
# scores differently (one puts unrelated text near 0, another near 0.4), so each is calibrated on
# first use: CALIBRATION is twenty searches about unrelated things, and the cutoff sits SIGMAS
# standard deviations above how alike those look. Random unrelated pairs are easy; the hard
# negatives are near-topic searches ("the india" / "audi india"), so the margin is wide: on a
# hand-labelled set of 660 search pairs, 7 standard deviations gave 88% precision while still
# finding 85% of same-need pairs (3.5 found 92% but a third of what it showed was unrelated).
# Word matching alone has a fixed cutoff, fitted on the same pairs.
RELATED = {"lexical": 0.45}
SIGMAS = 7.0
DEFAULT_RELATED = 0.45  # if calibration itself fails
W_EMBED, W_LEXICAL = 0.7, 0.3
# A past search's meaning includes the searches around it (within SESSION seconds): "haiku" next to
# Claude searches is a model, not a poem. Its vector is blended with its neighbours' (CONTEXT_MIX).
SESSION, CONTEXT_MIX = 1800, 0.5
# Blending in neighbours pulls scores slightly toward the middle, so the cutoff moves down with it:
# at 0.96 of the calibrated cutoff the labelled pairs gained on precision and recall together.
CONTEXT_CUTOFF = 0.96
# Switches for the evaluation script's ablations; all on in normal use.
RULES = {"context": True, "prefix": True, "acronym": True, "site": True, "names": True}
CALIBRATION = (
    "how to bake sourdough bread", "python list comprehension syntax", "weather in tokyo tomorrow",
    "best budget running shoes", "who won the 2018 world cup", "symptoms of vitamin d deficiency",
    "cheap flights to lisbon", "how do black holes form", "replace a bike chain", "taylor swift tour dates",
    "mortgage interest rates today", "learn guitar chords for beginners", "history of the roman empire",
    "fix a leaking kitchen tap", "electric car battery lifespan", "meditation for anxiety",
    "dog food for puppies", "excel vlookup formula", "how volcanoes erupt", "wedding dress styles")
HALF_LIFE_DAYS = 30
# Site affinity was a weak predictor of the site actually opened in the evaluation, so it nudges
# rather than steers.
W_RANK, W_SEMANTIC, W_AFFINITY = 0.6, 0.25, 0.15
PREF_BONUS = {1: 0.35, -1: -0.35}
_DOC_CACHE_MAX = 4000


def threshold(model: str) -> float:
    return RELATED.get(model.split(":")[0], DEFAULT_RELATED)


def calibrated(vecs: np.ndarray) -> float:
    """The "related" cutoff for a model, from its vectors of the CALIBRATION searches."""
    sims = (vecs @ vecs.T)[np.triu_indices(len(vecs), 1)]
    return float(np.clip(sims.mean() + SIGMAS * sims.std(), 0.3, 0.75))


# "2 aed to inr", "$2.12 in rupees", "50000 mb in gb", "680 of 15000 in percentage"
_UNIT = (r"(?:usd|inr|eur|gbp|aed|jpy|cny|aud|cad|sgd|chf|btc|eth|dollars?|rupees?|euros?|pounds?|yen|dirhams?|"
         r"[kmgtp]?b|bytes?|bits?|[kmc]?m|km|cm|mm|kg|g|mg|lbs?|oz|mi|miles?|ft|feet|inch(?:es)?|in|yards?|"
         r"[cf]|celsius|fahrenheit|kelvin|l|ml|litres?|liters?|gallons?|sec(?:onds?)?|min(?:utes?)?|hours?|days?|weeks?|"
         r"mph|kmh|km/h|%|percent(?:age)?|lakhs?|crores?|million|billion)")
_CONVERSION = re.compile(r"[$€£₹(]*\s*[\d.,]+\s*(?:million|billion|k|m|bn)?\)?\s*" + _UNIT + r"?\s+(?:to|in|into|of)\s+"
                         r"(?:[\d.,]+\s*" + _UNIT + r"?|" + _UNIT + r")(?:\s+(?:in|to)\s+" + _UNIT + r")?[.?!]?")
# Pages that were never the content: challenge screens, errors, sign-ins, search pages.
_JUNK_TITLE = re.compile(r"^(?:(?:just a moment|attention required|404|not found|access denied|sign in|log ?in|"
                         r"startpage search results|error)\b|(?:google|bing|duckduckgo)$|.*\bat duckduckgo$)", re.I)
_UTILITY_KINDS = {"calc", "unit", "color", "timestamp", "random", "timer", "metronome", "finance", "solve", "graph",
                  "track", "currency", "time", "weather", "sun", "define", "translate", "crypto"}


@lru_cache(maxsize=8192)
def utility(q: str) -> bool:
    """Sums, conversions, timers, weather: instant answers, not interests. They never match."""
    text = q.strip().casefold()
    if re.fullmatch(r"[\d\s.,+\-*/x×%()=$€£₹^]+", text) or _CONVERSION.fullmatch(text):
        return True
    from archer.query import classify  # local import: query.py pulls in the utility parsers
    try:
        return classify(q, settings.home_currency).kind in _UTILITY_KINDS
    except Exception:
        return False


@lru_cache(maxsize=50000)
def compact(q: str) -> str:
    return re.sub(r"[^a-z0-9]", "", q.casefold())


@lru_cache(maxsize=50000)
def word_count(q: str) -> int:
    return len(words(q))


def fragment(q: str) -> bool:
    """A half-typed entry the browser recorded ("z", "ze")."""
    return len(compact(q)) < 3


def spelled_alike(a: str, b: str) -> bool:
    """The same search after removing spaces ("light house" / "lighthouse"), or one or two typos apart."""
    ca, cb = compact(a), compact(b)
    if ca == cb:
        return True
    if min(len(ca), len(cb)) < 5 or abs(len(ca) - len(cb)) > 2:
        return False
    m = SequenceMatcher(None, ca, cb)  # the cheap upper bounds first: most pairs stop here
    return m.real_quick_ratio() >= 0.85 and m.quick_ratio() >= 0.85 and m.ratio() >= 0.85


def started(q: str, past: str) -> bool:
    """q is the first part of past, as when a search went off half-typed ("ascii gener")."""
    cq, cp = compact(q), compact(past)
    return len(cq) >= 4 and len(cp) > len(cq) and cp.startswith(cq) and len(cq) >= 0.4 * len(cp)


def acronym(q: str, past: str) -> bool:
    """q is past's initials: "dwc" for "delhi watch company"."""
    cq = compact(q)
    ws = [w for w in re.findall(r"[a-z0-9]+", past.casefold()) if w not in ("the", "of", "and", "a")]
    return 2 <= len(cq) <= 5 and len(ws) == len(cq) and q.strip().isalnum() and "".join(w[0] for w in ws) == cq


_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")
_FILLER = {"the", "a", "an", "of", "in", "for", "vs", "and", "to", "on", "with", "best", "new", "how", "what", "is"}


def _shape(token: str) -> str:
    return re.sub(r"\d", "0", re.sub(r"[a-z]", "a", token))


def different_thing(query: str, text: str, known_word=None) -> bool:
    """True when text is about a different model, version or product of the same kind as the query:
    4060 for 4050, 2024 for 2021, g3 for c4, or another product name beside the same brand
    ("seltos" / "sonet"). Embeddings score these as near-identical, so pages about the other one
    must not be recalled. `known_word` (from the embedding model's vocabulary) tells product names
    from ordinary words; without it only numbers are compared."""
    mine, theirs = set(_TOKEN.findall(query.casefold())) - _FILLER, set(_TOKEN.findall(text.casefold())) - _FILLER
    if not mine & theirs:
        return False
    only_mine, only_theirs = mine - theirs, theirs - mine
    nums_m = {t for t in only_mine if re.search(r"\d", t)}
    nums_t = {t for t in only_theirs if re.search(r"\d", t)}
    if any(_shape(a) == _shape(b) for a in nums_m for b in nums_t):
        return True
    if known_word is None:
        return False
    names_m = [w for w in only_mine if w.isalpha() and len(w) >= 3 and known_word(w) is False]
    names_t = [w for w in only_theirs if w.isalpha() and len(w) >= 3 and known_word(w) is False]
    if not names_m or not names_t:
        return False
    alike = lambda a, b: a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.7
    # A misspelling looks like an unknown name too ("bangalorwe"): only distinct names count.
    return not any(alike(a, b) for a in names_m for b in only_theirs) and not any(alike(b, a) for b in names_t for a in only_mine)


def other_model(query: str, text: str) -> bool:
    """Numbers-only form of different_thing, kept for callers without a vocabulary."""
    return different_thing(query, text)


def site_label(host: str) -> str:
    """The name a site's address spells: platform.deepseek.com -> deepseek, mobbin.com -> mobbin."""
    labels = host.removeprefix("www.").split(".")
    while len(labels) > 1 and labels[-1] in ("com", "org", "net", "co", "in", "io", "ai", "app", "dev", "uk", "me", "tv", "gg", "so", "xyz"):
        labels.pop()
    return labels[-1] if labels else ""


def site_pref(host: str, prefs: dict[str, int]) -> int:
    """A preference for example.com also covers news.example.com."""
    host = host.removeprefix("www.")
    while host:
        if host in prefs:
            return prefs[host]
        _, _, host = host.partition(".")
        if "." not in host:
            return prefs.get(host, 0)
    return 0


def _decay(at: float, now: float) -> float:
    return 0.5 ** (max(0.0, now - at) / 86400 / HALF_LIFE_DAYS)


@dataclass
class Personal:
    results: list = field(default_factory=list)
    related: list[dict] = field(default_factory=list)   # {"q", "last"}
    recall: list[dict] = field(default_factory=list)    # {"url", "title", "host", "last", "q"}
    hidden: list[str] = field(default_factory=list)     # hosts of results removed by a block
    model: str = ""


class Personalizer:
    def __init__(self, history: History, emb: Embeddings = default_embeddings):
        self.history = history
        self.emb = emb
        self._docs: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()
        # History vectors by (kind, model), loaded from SQLite once and kept until something is deleted.
        self._stored: dict[tuple[str, str], tuple[int, dict[str, np.ndarray]]] = {}
        self._thresholds: dict[str, float] = {}
        self._contexts: tuple | None = None

    async def threshold(self, model: str) -> float:
        if model in RELATED:
            return RELATED[model]
        if model not in self._thresholds:
            used, vecs = await self._combine(list(CALIBRATION))
            self._thresholds[model] = calibrated(vecs) if used == model else threshold(model)
        return self._thresholds[model]

    async def _combine(self, texts: list[str]) -> tuple[str, np.ndarray]:
        """Blended vectors for texts: √0.7·embedding ⊕ √0.3·lexical, so a dot product is the blend."""
        used, vecs = await self.emb.encode(texts)
        if used == LEXICAL.name:
            return used, vecs
        lex = await asyncio.to_thread(LEXICAL.encode, texts) if len(texts) > 64 else LEXICAL.encode(texts)
        return f"{used}+lexical", np.hstack([np.sqrt(W_EMBED) * vecs, np.sqrt(W_LEXICAL) * lex]).astype(np.float32)

    def _load(self, kind: str, model: str) -> dict[str, np.ndarray]:
        hit = self._stored.get((kind, model))
        if hit is None or hit[0] != self.history.version:
            hit = (self.history.version, self.history.vectors(kind, model))
            self._stored[(kind, model)] = hit
        return hit[1]

    # ------------------------------------------------------------ vectors
    def _name(self) -> str:
        model = self.emb.active().name
        return model if model == LEXICAL.name else f"{model}+lexical"

    async def _vectors(self, kind: str, items: dict[str, str]) -> tuple[str, dict[str, np.ndarray]]:
        """Stored blended vectors for these keys (text by key), computing and saving the missing ones."""
        model = self._name()
        stored = self._load(kind, model)
        missing = [k for k in items if k not in stored]
        if missing:
            used, vecs = await self._combine([items[k] for k in missing])
            if used != model:  # the model failed mid-way: compare everything lexically this time
                model, stored = used, self._load(kind, used)
                missing = [k for k in items if k not in stored]
                used, vecs = await self._combine([items[k] for k in missing])
            fresh = dict(zip(missing, vecs))
            self.history.put_vectors(kind, model, fresh)
            stored.update(fresh)
        return model, {k: stored[k] for k in items if k in stored}

    async def _query(self, q: str) -> tuple[str, np.ndarray]:
        """The query's vector: from history when it was searched before, else computed and not stored
        (with history paused, nothing about this search may reach the disk)."""
        model = self._name()
        stored = self._load("q", model).get(norm(q))
        if stored is not None:
            return model, stored
        used, vecs = await self._combine([q])
        return used, vecs[0]

    async def _past_scores(self, q: str) -> tuple[str, np.ndarray, float, dict, dict[str, float]]:
        """(model, query vector, cutoff, past searches by norm, score by norm) after the matching rules:
        utility searches and fragments are left out, spelling variants always match, and a one-word
        search needs a stronger match."""
        model, qvec = await self._query(q)
        thr = await self.threshold(model) * (CONTEXT_CUTOFF if RULES["context"] and model != LEXICAL.name else 1.0)
        qn = norm(q)
        past = {e["norm"]: e for e in self.history.queries()
                if e["norm"] != qn and not fragment(e["q"]) and not utility(e["q"])}
        if utility(q) or fragment(q):
            return model, qvec, thr, past, {}
        qmodel, qvecs = await self._vectors("q", {k: e["q"] for k, e in past.items()})
        if qmodel != model or not qvecs:
            return model, qvec, thr, past, {}
        keys = list(qvecs)
        matrix = np.stack([qvecs[k] for k in keys])
        raw = matrix @ qvec
        if RULES["context"] and model != LEXICAL.name:  # word matching's cutoff was fitted without it
            raw = (1 - CONTEXT_MIX) * raw + CONTEXT_MIX * (self._context_matrix(model, keys, qvecs) @ qvec)
        short = word_count(q) <= 1
        cq = compact(q)
        initials = RULES["acronym"] and 2 <= len(cq) <= 5
        scores = {}
        for k, s in zip(keys, raw.tolist()):
            text = past[k]["q"]
            cp = compact(text)
            # The string rules only run where they can apply; most past searches stop at a length check.
            if abs(len(cp) - len(cq)) <= 2 and spelled_alike(q, text):
                s = max(s, 1.0 if cq == cp else thr + 0.1)
            elif (initials and acronym(q, text)) or (RULES["acronym"] and 2 <= len(cp) <= 5 and acronym(text, q)):
                s = max(s, thr + 0.1)
            elif RULES["prefix"] and len(cp) > len(cq) >= 4 and started(q, text):
                s = max(s, thr + 0.05)
            elif short or word_count(text) <= 1:
                if not (cq in cp or cp in cq):
                    s -= 0.1  # one shared word decides too much in a one-word search
            scores[k] = s
        return model, qvec, thr, past, scores

    def _context_matrix(self, model: str, keys: list[str], qvecs: dict[str, np.ndarray]) -> np.ndarray:
        """Each past search's vector blended with the searches made around it, cached until history changes."""
        # Rebuilt after deletions, or once 25 searches have been added since: a session's context
        # changes slowly, and rebuilding on every search doubled its cost.
        count = self.history.stats()["searches"]
        if self._contexts:
            (m, version, built, keyset), matrix = self._contexts
            if m == model and version == self.history.version and count - built < 25:
                if keyset == tuple(keys):
                    return matrix
                # Searches added since the last rebuild use their own vector until the next one.
                pos = {k: i for i, k in enumerate(keyset)}
                return np.stack([matrix[pos[k]] if k in pos else qvecs[k] / (np.linalg.norm(qvecs[k]) or 1) for k in keys])
        stamp = (model, self.history.version, count, tuple(keys))
        neighbours = self.history.sessions(SESSION)
        rows = []
        for k in keys:
            near = [qvecs[n] for n in neighbours.get(k, ()) if n in qvecs]
            v = qvecs[k]
            if near:
                m = np.mean(near, axis=0)
                v = v + m / (np.linalg.norm(m) or 1)
            rows.append(v / (np.linalg.norm(v) or 1))
        matrix = np.stack(rows).astype(np.float32)
        self._contexts = (stamp, matrix)
        return matrix

    def known_word(self):
        """The embedding model's vocabulary test (word -> True/False), or None without one."""
        return getattr(self.emb.active(), "known_word", None) if RULES["names"] else None

    async def _docs_for(self, model: str, results: list) -> np.ndarray:
        texts, keys, out = [], [], [None] * len(results)
        for i, r in enumerate(results):
            key = (model, r.url)
            if key in self._docs:
                out[i] = self._docs[key]
                self._docs.move_to_end(key)
            else:
                keys.append(i)
                texts.append(f"{r.title}. {r.content}")
        if texts:
            used, vecs = await self.emb.encode(texts)
            if used != model:
                return np.zeros((len(results), 1), dtype=np.float32)
            for i, vec in zip(keys, vecs):
                out[i] = vec
                self._docs[(model, results[i].url)] = vec
            while len(self._docs) > _DOC_CACHE_MAX:
                self._docs.popitem(last=False)
        return np.stack(out) if out else np.zeros((0, 1), dtype=np.float32)

    # ------------------------------------------------------------ results
    async def personalize(self, q: str, results: list, *, rerank: bool = True, exclude_search: int | None = None) -> Personal:
        """Rerank results and gather history for this query. Never mutates `results` (they are cached)."""
        now = time.time()
        out = Personal(results=list(results))
        prefs = self.history.sites()
        kept = []
        for r in results:
            if site_pref(r.host, prefs) == -2:
                out.hidden.append(r.host)
            else:
                kept.append(r)
        if not q.strip():
            out.results = kept
            return out
        model, qvec, thr, past, sims = await self._past_scores(q)
        out.model = model
        qn = norm(q)
        dismissed = self.history.unrelated_to(q)  # your own corrections beat every score
        mine = not (utility(q) or fragment(q))

        # Related: earlier searches that mean the same, freshest first among equals, then searches
        # from which you opened a page that is among these results (your clicks link them, even when
        # the words don't: an abbreviation, a product's other name).
        related = sorted(((s * (0.85 + 0.15 * _decay(past[k]["last"], now)), k) for k, s in sims.items() if s >= thr), reverse=True)
        q_words = set(words(q))
        picked = [k for _, k in related if set(words(past[k]["q"])) != q_words and k not in dismissed][:5]
        if mine and kept:
            for k in self.history.queries_that_opened([r.url for r in kept]):
                if k in past and k not in picked and k not in dismissed and len(picked) < 6:
                    picked.append(k)
        out.related = [{"q": past[k]["q"], "last": past[k]["last"]} for k in picked]

        # Visited pages, and site affinity from what was opened after similar searches.
        visits = self.history.visits([r.url for r in kept])
        affinity: Counter = Counter()
        opened: Counter = Counter()
        for c in self.history.host_clicks() if mine else ():
            s = 1.0 if c["norm"] == qn else sims.get(c["norm"], 0.0)
            if s >= thr:
                affinity[c["host"]] += s * _decay(c["at"], now)
                opened[c["host"]] += 1

        if rerank and len(kept) > 1:
            docs = await self._docs_for(self.emb.active().name, kept)
            sem = docs @ qvec if docs.shape[1] == qvec.shape[0] else np.zeros(len(kept))
            spread = float(sem.max() - sem.min()) if len(sem) else 0.0
            sem_n = (sem - sem.min()) / spread if spread > 0.05 else np.full(len(kept), 0.5)
            w_sem = W_SEMANTIC if model != "lexical" else W_SEMANTIC / 2
            scored = []
            for i, r in enumerate(kept):
                rank = 1 / (1 + 0.25 * i)  # the provider's order, independent of page length
                aff = 1 - np.exp(-affinity.get(r.host, 0.0)) if opened[r.host] >= 2 else 0.0
                pref = PREF_BONUS.get(site_pref(r.host, prefs), 0.0)
                revisit = 0.08 if r.url in visits else 0.0
                score = W_RANK * rank + w_sem * float(sem_n[i]) + W_AFFINITY * aff + pref + revisit
                scored.append((score, -i, r))
            scored.sort(reverse=True, key=lambda t: (t[0], t[1]))
            kept = [r for _, _, r in scored]

        tagged = []
        for r in kept:
            note = ""
            pref = site_pref(r.host, prefs)
            if pref == 1:
                note = "raised"
            elif pref == -1:
                note = "lowered"
            elif opened[r.host] >= 2 and affinity.get(r.host, 0) >= 0.8:
                note = "often"
            visit = visits.get(r.url)
            tagged.append(replace(r, visited=visit["last"] if visit else 0.0, personal=note))
        out.results = tagged

        # Recall: pages opened before for searches like this, not already on this page. One page per
        # site (three pages of one site crowded out the page you wanted), and never a page for a
        # different model or version than the one searched ("iphone 15 pro" for "iphone 16 pro").
        on_page = {r.url for r in kept}
        pages = [p for p in self.history.pages() if p["url"] not in on_page and p["url"] not in dismissed
                 and norm(p["q"] or "") not in dismissed] if mine else []
        known = self.known_word()
        if pages and RULES["site"]:
            # A search that names a site you use ("mobbin", "deepseek api") is often a way back to it:
            # its most visited page leads the box, whatever the words around it.
            named = {w for w in re.findall(r"[a-z0-9]+", q.casefold()) if len(w) >= 4} | {compact(q)}
            visited = [p for p in pages if site_label(p["host"]) in named]
            if visited:
                top = max(visited, key=lambda p: (p["n"], -len(p["url"]), p["last"]))
                if top["title"] and not _JUNK_TITLE.match(top["title"].strip()):
                    out.recall.append({"url": top["url"], "title": top["title"], "host": top["host"], "last": top["last"], "q": top["q"] or ""})
        if pages:
            pmodel, pvecs = await self._vectors("page", {p["url"]: f"{p['title']}. {p['snippet']}" for p in pages})
            picks = []
            for p in pages:
                s = float(pvecs[p["url"]] @ qvec) if pmodel == model and p["url"] in pvecs else 0.0
                pq = norm(p["q"] or "")
                via = 1.0 if pq == qn else sims.get(pq, 0.0)
                best = max(s, via * 0.95)
                if best < thr + 0.05:
                    continue
                if pq != qn and different_thing(q, f"{p['title']} {p['q'] or ''}", known):
                    continue
                if not p["title"] or _JUNK_TITLE.match(p["title"].strip()):
                    continue
                picks.append((best * (0.8 + 0.2 * _decay(p["last"], now)), p))
            picks.sort(key=lambda t: t[0], reverse=True)
            sites: set[str] = {r["host"] for r in out.recall}
            for _, p in picks:
                if p["host"] in sites:
                    continue
                sites.add(p["host"])
                out.recall.append({"url": p["url"], "title": p["title"] or p["host"], "host": p["host"], "last": p["last"], "q": p["q"] or ""})
                if len(out.recall) == 3:
                    break
        return out

    # ------------------------------------------------------------ history page
    async def search_history(self, q: str, limit: int = 60) -> list[int]:
        """Search ids whose query, or a page opened from it, matches `q` by meaning or by words."""
        q = q.strip()
        if not q:
            return []
        entries = self.history.queries()
        if not entries:
            return []
        model, qvec = await self._query(q)
        thr = await self.threshold(model) * 0.85
        qm, qvecs = await self._vectors("q", {e["norm"]: e["q"] for e in entries})
        scores: dict[str, float] = {}
        needle = norm(q)
        for e in entries:
            s = float(qvecs[e["norm"]] @ qvec) if qm == model and e["norm"] in qvecs else 0.0
            if needle in e["norm"] or spelled_alike(q, e["q"]):
                s = max(s, 1.0)
            scores[e["norm"]] = s
        pages = self.history.pages()
        if pages:
            pm, pvecs = await self._vectors("page", {p["url"]: f"{p['title']}. {p['snippet']}" for p in pages})
            for p in pages:
                if not p["q"]:
                    continue
                s = float(pvecs[p["url"]] @ qvec) * 0.95 if pm == model and p["url"] in pvecs else 0.0
                if needle in (p["title"] or "").casefold():
                    s = max(s, 0.95)
                key = norm(p["q"])
                scores[key] = max(scores.get(key, 0.0), s)
        ranked = sorted((s, k) for k, s in scores.items() if s >= thr)
        norms = [k for _, k in reversed(ranked)][:limit]
        if not norms:
            return []
        marks = ",".join("?" * len(norms))
        rows = self.history._q(f"SELECT id, norm FROM searches WHERE norm IN ({marks}) ORDER BY last_at DESC", tuple(norms))
        order = {k: i for i, k in enumerate(norms)}
        return [r["id"] for r in sorted(rows, key=lambda r: order[r["norm"]])][:limit]

    async def topics(self, limit: int = 8) -> list[dict]:
        """History grouped by meaning: greedy clustering on query vectors, freshest first."""
        entries = sorted((e for e in self.history.queries() if not fragment(e["q"]) and not utility(e["q"])),
                         key=lambda e: e["last"], reverse=True)[:1500]
        if len(entries) < 3:
            return []
        model, vecs = await self._vectors("q", {e["norm"]: e["q"] for e in entries})
        thr = await self.threshold(model)
        clusters: list[dict] = []
        for e in entries:
            v = vecs.get(e["norm"])
            if v is None:
                continue
            best, best_s = None, thr
            for c in clusters:
                s = float(c["centroid"] @ v) / (np.linalg.norm(c["centroid"]) or 1)
                if s >= best_s:
                    best, best_s = c, s
            if best is None:
                clusters.append({"centroid": v.copy(), "items": [e]})
            else:
                best["centroid"] += v
                best["items"].append(e)
        # Second pass: a member must still be close to its group's final centre, or the group chains
        # from one meaning to the next through shared words ("z" → "zen browser" → "Zendaya").
        for c in clusters:
            centroid = c["centroid"] / (np.linalg.norm(c["centroid"]) or 1)
            c["items"] = [e for e in c["items"] if float(vecs[e["norm"]] @ centroid) >= thr]
        out = []
        for c in clusters:
            if len(c["items"]) < 2:
                continue
            counts = Counter(w for e in c["items"] for w in set(words(e["q"])) if len(w) > 2)
            common = [w for w, n in counts.most_common(3) if n >= max(2, len(c["items"]) // 2)]
            centroid = c["centroid"] / (np.linalg.norm(c["centroid"]) or 1)
            central = max(c["items"], key=lambda e: float(vecs[e["norm"]] @ centroid))
            out.append({"label": " · ".join(common[:2]) if common else central["q"],
                        "queries": [e["q"] for e in c["items"][:6]], "size": len(c["items"]),
                        "searches": sum(e["n"] for e in c["items"]), "last": c["items"][0]["last"]})
        out.sort(key=lambda t: (t["size"], t["last"]), reverse=True)
        return out[:limit]
