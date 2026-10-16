"""Search history: searches, the results you opened, site preferences and chosen meanings.

Everything lives in one SQLite file, `data/history.sqlite3`, on this computer. Nothing here is
sent anywhere; `personalize.py` reads it to rank results and recall what you found before.

A search repeated within half an hour (a reload, back/forward, a tab switch) updates the
existing entry instead of adding another. Clicks belong to the search they came from. Embedding
vectors for past queries and opened pages are cached here with the model that made them.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

from archer.config import DATA_DIR

DB_PATH = DATA_DIR / "history.sqlite3"
REPEAT_WINDOW = 1800  # seconds: the same search again within this updates one entry
SITE_PREFS = {"raise": 1, "lower": -1, "block": -2}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY, q TEXT NOT NULL, norm TEXT NOT NULL, tab TEXT NOT NULL DEFAULT 'all',
    mode TEXT NOT NULL DEFAULT 'search', about TEXT NOT NULL DEFAULT '', hero TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL, last_at REAL NOT NULL, count INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS searches_last ON searches(last_at);
CREATE INDEX IF NOT EXISTS searches_norm ON searches(norm);
CREATE TABLE IF NOT EXISTS clicks (
    id INTEGER PRIMARY KEY, search_id INTEGER REFERENCES searches(id) ON DELETE CASCADE,
    url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS clicks_search ON clicks(search_id);
CREATE INDEX IF NOT EXISTS clicks_url ON clicks(url);
CREATE INDEX IF NOT EXISTS clicks_host ON clicks(host);
CREATE TABLE IF NOT EXISTS vectors (
    kind TEXT NOT NULL, key TEXT NOT NULL, model TEXT NOT NULL, vec BLOB NOT NULL,
    PRIMARY KEY (kind, key, model));
CREATE TABLE IF NOT EXISTS sites (host TEXT PRIMARY KEY, pref INTEGER NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meanings (norm TEXT PRIMARY KEY, about TEXT NOT NULL, at REAL NOT NULL);
"""


def norm(q: str) -> str:
    return re.sub(r"\s+", " ", q).strip().casefold()


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


class History:
    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        # Bumped whenever something is removed, so in-memory vector caches know to reload.
        self.version = 0

    # ------------------------------------------------------------ plumbing
    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(_SCHEMA)
            self._db = db
        return self._db

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.execute(sql, args)

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    # ------------------------------------------------------------ searches
    def record_search(self, q: str, tab: str = "all", mode: str = "search", about: str = "", hero: str = "",
                      now: float | None = None) -> int:
        now = time.time() if now is None else now
        key = norm(q)[:300]
        with self._lock:
            row = self.db.execute("SELECT id FROM searches WHERE norm=? AND tab=? AND mode=? AND last_at>? ORDER BY last_at DESC LIMIT 1",
                                  (key, tab, mode, now - REPEAT_WINDOW)).fetchone()
            if row:
                self.db.execute("UPDATE searches SET last_at=?, about=CASE WHEN ?<>'' THEN ? ELSE about END, "
                                "hero=CASE WHEN ?<>'' THEN ? ELSE hero END WHERE id=?",
                                (now, about, about, hero, hero, row["id"]))
                return row["id"]
            # How often this exact search was made before carries over, for suggestion ranking.
            prior = self.db.execute("SELECT COALESCE(MAX(count),0) FROM searches WHERE norm=?", (key,)).fetchone()[0]
            cur = self.db.execute("INSERT INTO searches(q, norm, tab, mode, about, hero, at, last_at, count) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (q.strip()[:300], key, tab, mode, about, hero, now, now, prior + 1))
            return cur.lastrowid

    def search(self, search_id: int) -> dict | None:
        rows = self._q("SELECT * FROM searches WHERE id=?", (search_id,))
        return dict(rows[0]) if rows else None

    def record_click(self, search_id: int | None, url: str, title: str = "", snippet: str = "", position: int = 0,
                     now: float | None = None) -> int | None:
        now = time.time() if now is None else now
        if search_id is not None and not self.search(search_id):
            search_id = None
        with self._lock:
            # Opening the same result twice from one search is one visit.
            if search_id is not None:
                row = self.db.execute("SELECT id FROM clicks WHERE search_id=? AND url=?", (search_id, url)).fetchone()
                if row:
                    self.db.execute("UPDATE clicks SET at=? WHERE id=?", (now, row["id"]))
                    return row["id"]
            cur = self.db.execute("INSERT INTO clicks(search_id, url, title, host, snippet, position, at) VALUES (?,?,?,?,?,?,?)",
                                  (search_id, url[:2000], title[:300], host_of(url), snippet[:500], position, now))
            return cur.lastrowid

    def entries(self, before: float | None = None, limit: int = 50, ids: list[int] | None = None) -> list[dict]:
        """Searches newest first, each with the results opened from it."""
        if ids is not None:
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            rows = self._q(f"SELECT * FROM searches WHERE id IN ({marks}) ORDER BY last_at DESC", tuple(ids))
        else:
            rows = self._q("SELECT * FROM searches WHERE last_at<? ORDER BY last_at DESC LIMIT ?",
                           (before if before is not None else time.time() + 1, limit))
        out = [dict(r) | {"clicks": []} for r in rows]
        if out:
            by_id = {e["id"]: e for e in out}
            marks = ",".join("?" * len(by_id))
            for c in self._q(f"SELECT * FROM clicks WHERE search_id IN ({marks}) ORDER BY at", tuple(by_id)):
                by_id[c["search_id"]]["clicks"].append(dict(c))
        return out

    def recent(self, limit: int = 8, prefix: str = "") -> list[str]:
        """Distinct past searches by frecency (how often, how lately), optionally starting with `prefix`
        or having a word that does."""
        now = time.time()
        p = norm(prefix)
        rows = self._q("SELECT q, norm, MAX(last_at) AS last, MAX(count) AS n FROM searches "
                       "WHERE mode='search' AND (?='' OR norm LIKE ? ESCAPE '\\' OR norm LIKE ? ESCAPE '\\') "
                       "GROUP BY norm ORDER BY last DESC LIMIT 400",
                       (p, _like(p) + "%", "% " + _like(p) + "%"))
        def score(r):
            days = (now - r["last"]) / 86400
            starts = 1.5 if p and r["norm"].startswith(p) else 1.0
            return starts * (1 + np.log1p(r["n"])) / (1 + days / 7)
        ranked = sorted(rows, key=score, reverse=True) if p else rows
        return [r["q"] for r in ranked[:limit] if r["norm"] != p]

    def delete_search(self, search_id: int) -> bool:
        self.version += 1
        return self._x("DELETE FROM searches WHERE id=?", (search_id,)).rowcount > 0

    def delete_query(self, q: str) -> int:
        """Forget every search for this text (the suggestion list's remove button)."""
        self.version += 1
        removed = self._x("DELETE FROM searches WHERE norm=?", (norm(q),)).rowcount
        self.drop_orphan_vectors()
        return removed

    def delete_click(self, click_id: int) -> bool:
        self.version += 1
        return self._x("DELETE FROM clicks WHERE id=?", (click_id,)).rowcount > 0

    def clear(self, since: float | None = None) -> None:
        """Clear everything, or only what happened after `since`. Site preferences stay; they are settings."""
        with self._lock:
            if since is None:
                for table in ("clicks", "searches", "meanings"):
                    self.db.execute(f"DELETE FROM {table}")
                self.db.execute("DELETE FROM vectors WHERE kind IN ('q','page')")
                self.db.execute("VACUUM")
            else:
                self.db.execute("DELETE FROM clicks WHERE at>=?", (since,))
                self.db.execute("DELETE FROM searches WHERE at>=?", (since,))
                self.db.execute("DELETE FROM meanings WHERE at>=?", (since,))
        self.drop_orphan_vectors()

    def prune(self, days: int) -> None:
        if days <= 0:
            return
        cutoff = time.time() - days * 86400
        with self._lock:
            self.db.execute("DELETE FROM clicks WHERE at<?", (cutoff,))
            self.db.execute("DELETE FROM searches WHERE last_at<?", (cutoff,))
            self.db.execute("DELETE FROM meanings WHERE at<?", (cutoff,))
        self.drop_orphan_vectors()

    def drop_orphan_vectors(self, keep_models: set[str] | None = None) -> None:
        """Vectors of forgotten searches and pages, and (given `keep_models`) of models no longer used."""
        self.version += 1
        with self._lock:
            if keep_models:
                marks = ",".join("?" * len(keep_models))
                self.db.execute(f"DELETE FROM vectors WHERE model NOT IN ({marks})", tuple(keep_models))
            self.db.execute("DELETE FROM vectors WHERE kind='q' AND key NOT IN (SELECT norm FROM searches)")
            self.db.execute("DELETE FROM vectors WHERE kind='page' AND key NOT IN (SELECT url FROM clicks)")

    def stats(self) -> dict:
        rows = self._q("SELECT (SELECT COUNT(*) FROM searches) AS searches, (SELECT COUNT(*) FROM clicks) AS clicks, "
                       "(SELECT MIN(at) FROM searches) AS since, (SELECT COUNT(*) FROM sites) AS sites")
        return dict(rows[0])

    # -------------------------------------------------- personalization data
    def queries(self) -> list[dict]:
        """Every distinct past query with its latest time, count and opened results."""
        rows = self._q("SELECT norm, q, MAX(last_at) AS last, SUM(count) AS n, MAX(id) AS id FROM searches GROUP BY norm")
        return [dict(r) for r in rows]

    def pages(self) -> list[dict]:
        """Every distinct opened page: latest visit, visit count, and the search it was last opened from."""
        rows = self._q("SELECT c.url, c.title, c.host, c.snippet, MAX(c.at) AS last, COUNT(*) AS n, "
                       "(SELECT s.q FROM clicks c2 JOIN searches s ON s.id=c2.search_id WHERE c2.url=c.url ORDER BY c2.at DESC LIMIT 1) AS q "
                       "FROM clicks c GROUP BY c.url")
        return [dict(r) for r in rows]

    def visits(self, urls: list[str]) -> dict[str, dict]:
        if not urls:
            return {}
        marks = ",".join("?" * len(urls))
        rows = self._q(f"SELECT url, MAX(at) AS last, COUNT(*) AS n FROM clicks WHERE url IN ({marks}) GROUP BY url", tuple(urls))
        return {r["url"]: dict(r) for r in rows}

    def queries_that_opened(self, urls: list[str]) -> list[str]:
        """Norms of past searches from which any of these exact pages was opened, most recent first."""
        if not urls:
            return []
        marks = ",".join("?" * len(urls))
        rows = self._q(f"SELECT s.norm, MAX(c.at) AS last FROM clicks c JOIN searches s ON s.id=c.search_id "
                       f"WHERE c.url IN ({marks}) GROUP BY s.norm ORDER BY last DESC LIMIT 10", tuple(urls))
        return [r["norm"] for r in rows]

    def host_clicks(self) -> list[dict]:
        """(host, query norm, time) for every click that came from a search: the raw site affinity data."""
        rows = self._q("SELECT c.host, s.norm, c.at FROM clicks c JOIN searches s ON s.id=c.search_id")
        return [dict(r) for r in rows]

    def top_hosts(self, limit: int = 12) -> list[dict]:
        rows = self._q("SELECT host, COUNT(*) AS n, MAX(at) AS last FROM clicks WHERE host<>'' GROUP BY host ORDER BY n DESC, last DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ vectors
    def vectors(self, kind: str, model: str) -> dict[str, np.ndarray]:
        rows = self._q("SELECT key, vec FROM vectors WHERE kind=? AND model=?", (kind, model))
        return {r["key"]: np.frombuffer(r["vec"], dtype=np.float32) for r in rows}

    def put_vectors(self, kind: str, model: str, items: dict[str, np.ndarray]) -> None:
        if not items:
            return
        with self._lock:
            self.db.executemany("INSERT OR REPLACE INTO vectors(kind, key, model, vec) VALUES (?,?,?,?)",
                                [(kind, key, model, np.asarray(vec, dtype=np.float32).tobytes()) for key, vec in items.items()])

    # -------------------------------------------------------- site prefs
    def sites(self) -> dict[str, int]:
        return {r["host"]: r["pref"] for r in self._q("SELECT host, pref FROM sites")}

    def set_site(self, host: str, pref: str | None) -> None:
        host = host.lower().removeprefix("www.")
        if pref is None:
            self._x("DELETE FROM sites WHERE host=?", (host,))
        else:
            self._x("INSERT OR REPLACE INTO sites(host, pref, at) VALUES (?,?,?)", (host, SITE_PREFS[pref], time.time()))

    # ----------------------------------------------------------- meanings
    def meaning(self, q: str) -> str:
        rows = self._q("SELECT about FROM meanings WHERE norm=?", (norm(q),))
        return rows[0]["about"] if rows else ""

    def remember_meaning(self, q: str, about: str) -> None:
        self._x("INSERT OR REPLACE INTO meanings(norm, about, at) VALUES (?,?,?)", (norm(q), about[:200], time.time()))


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
