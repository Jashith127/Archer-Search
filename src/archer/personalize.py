"""Personal search: what your history and site preferences change about a results page.

Everything is computed locally from `history.py` and `embed.py`; no model call, no network.

- **Ranking** blends the provider's order with meaning and with you:
  `score = 0.5·rank + 0.2·semantic + 0.3·affinity (+ site preference, + revisit)`, where
  *rank* is the provider's position (1, 0.8, 0.67, …), *semantic* is how close the result's title and snippet are to
  the query (normalised across the page), and *affinity* is how often you opened this site after
  searches like this one, fading over 30 days. Raised sites move up, lowered ones down, blocked
  ones disappear. The provider's order still carries the most weight, so exact matches (model
  numbers, names) stay near the top; meaning and habit reorder close calls, and a site you keep
  opening for a topic climbs a few places for that topic only.
- **Recall**: pages you opened before for searches like this one ("You found these before").
- **Related**: your earlier searches that mean the same thing, worded differently.
- **Suggestions**: your past searches first in the address bar, by how often and how lately.
- **Meanings**: an "Other meanings" choice is remembered for the next search of that name.
- **Topics**: history grouped by meaning for the history page.
"""
from __future__ import annotations

import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field, replace

import numpy as np

from archer.config import settings
from archer.embed import Embeddings, embeddings as default_embeddings, words
from archer.history import History, host_of, norm

# Cosine similarity above which two texts count as "about the same thing", per backend. Static
# models spread unrelated text around 0.1-0.3; Ollama's larger models sit higher overall.
RELATED = {"lexical": 0.34, "ollama": 0.62}
DEFAULT_RELATED = 0.5
HALF_LIFE_DAYS = 30
W_RANK, W_SEMANTIC, W_AFFINITY = 0.5, 0.2, 0.3
PREF_BONUS = {1: 0.35, -1: -0.35}
_DOC_CACHE_MAX = 4000


def threshold(model: str) -> float:
    return RELATED.get(model.split(":")[0], DEFAULT_RELATED)


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

    def _load(self, kind: str, model: str) -> dict[str, np.ndarray]:
        hit = self._stored.get((kind, model))
        if hit is None or hit[0] != self.history.version:
            hit = (self.history.version, self.history.vectors(kind, model))
            self._stored[(kind, model)] = hit
        return hit[1]

    # ------------------------------------------------------------ vectors
    async def _vectors(self, kind: str, items: dict[str, str]) -> tuple[str, dict[str, np.ndarray]]:
        """Stored vectors for these keys (text by key), embedding and saving the missing ones."""
        model = self.emb.active().name
        stored = self._load(kind, model)
        missing = [k for k in items if k not in stored]
        if missing:
            used, vecs = await self.emb.encode([items[k] for k in missing])
            if used != model:  # the model failed mid-way: compare everything lexically this time
                model, stored = used, self._load(kind, used)
                missing = [k for k in items if k not in stored]
                used, vecs = await self.emb.encode([items[k] for k in missing])
            fresh = dict(zip(missing, vecs))
            self.history.put_vectors(kind, model, fresh)
            stored.update(fresh)
        return model, {k: stored[k] for k in items if k in stored}

    async def _query(self, q: str) -> tuple[str, np.ndarray]:
        """The query's vector: from history when it was searched before, else computed and not stored
        (with history paused, nothing about this search may reach the disk)."""
        model = self.emb.active().name
        stored = self._load("q", model).get(norm(q))
        if stored is not None:
            return model, stored
        used, vecs = await self.emb.encode([q])
        return used, vecs[0]

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
        model, qvec = await self._query(q)
        out.model = model
        thr = threshold(model)
        qn = norm(q)
        past = {e["norm"]: e for e in self.history.queries() if e["norm"] != qn}
        qmodel, qvecs = await self._vectors("q", {k: e["q"] for k, e in past.items()})
        sims = {k: float(v @ qvec) for k, v in qvecs.items()} if qmodel == model else {}

        # Related: earlier searches that mean the same, freshest first among equals.
        related = sorted(((s * (0.85 + 0.15 * _decay(past[k]["last"], now)), k) for k, s in sims.items() if s >= thr), reverse=True)
        q_words = set(words(q))
        out.related = [{"q": past[k]["q"], "last": past[k]["last"]} for _, k in related
                       if set(words(past[k]["q"])) != q_words][:6]

        # Visited pages, and site affinity from what was opened after similar searches.
        visits = self.history.visits([r.url for r in kept])
        affinity: Counter = Counter()
        opened: Counter = Counter()
        for c in self.history.host_clicks():
            s = 1.0 if c["norm"] == qn else sims.get(c["norm"], 0.0)
            if s >= thr:
                affinity[c["host"]] += s * _decay(c["at"], now)
                opened[c["host"]] += 1

        if rerank and len(kept) > 1:
            docs = await self._docs_for(model, kept)
            sem = docs @ qvec if docs.shape[1] == qvec.shape[0] else np.zeros(len(kept))
            spread = float(sem.max() - sem.min()) if len(sem) else 0.0
            sem_n = (sem - sem.min()) / spread if spread > 0.05 else np.full(len(kept), 0.5)
            w_sem = W_SEMANTIC if model != "lexical" else W_SEMANTIC / 2
            scored = []
            for i, r in enumerate(kept):
                rank = 1 / (1 + 0.25 * i)  # the provider's order, independent of page length
                aff = 1 - np.exp(-affinity.get(r.host, 0.0))
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

        # Recall: pages opened before for searches like this, not already on this page.
        on_page = {r.url for r in kept}
        pages = [p for p in self.history.pages() if p["url"] not in on_page]
        if pages:
            pmodel, pvecs = await self._vectors("page", {p["url"]: f"{p['title']}. {p['snippet']}" for p in pages})
            picks = []
            for p in pages:
                s = float(pvecs[p["url"]] @ qvec) if pmodel == model and p["url"] in pvecs else 0.0
                pq = norm(p["q"] or "")
                via = 1.0 if pq == qn else sims.get(pq, 0.0)
                best = max(s, via * 0.95)
                if best >= thr + 0.08:
                    picks.append((best * (0.8 + 0.2 * _decay(p["last"], now)), p))
            picks.sort(key=lambda t: t[0], reverse=True)
            out.recall = [{"url": p["url"], "title": p["title"] or p["host"], "host": p["host"], "last": p["last"], "q": p["q"] or ""}
                          for _, p in picks[:3]]
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
        thr = threshold(model) * 0.85
        qm, qvecs = await self._vectors("q", {e["norm"]: e["q"] for e in entries})
        scores: dict[str, float] = {}
        needle = norm(q)
        for e in entries:
            s = float(qvecs[e["norm"]] @ qvec) if qm == model and e["norm"] in qvecs else 0.0
            if needle in e["norm"]:
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
        entries = sorted(self.history.queries(), key=lambda e: e["last"], reverse=True)[:1500]
        if len(entries) < 3:
            return []
        model, vecs = await self._vectors("q", {e["norm"]: e["q"] for e in entries})
        thr = threshold(model) * 0.9
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
