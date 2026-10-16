"""Export your searches (and the pages you opened from them) from a Chromium browser's history.

Reads a copy of the browser's History database, never the live file, and writes one JSON file
for testing Archer's history features against real behaviour. Only searches leave the browser's
database: a visit is included only as a search, or as a page opened within a few minutes of one.

    python scripts/export_history.py                      # Helium, else Chrome / Edge / Brave
    python scripts/export_history.py --browser chrome --days 180 --out my-searches.json
    python scripts/export_history.py --history "C:/path/to/History" --exclude bank --exclude mail

Standard library only; Windows, macOS and Linux profile paths are known. Close the browser first
if the copy fails (Chromium locks the file while it runs).
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)  # Chromium stores microseconds since 1601
OPEN_WINDOW = 180  # seconds after a search in which a visited page counts as opened from it

# Where each browser keeps its default profile.
LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))
HOME = Path.home()
PROFILES = {
    "helium": [LOCAL / "imput/Helium/User Data", HOME / "Library/Application Support/net.imput.helium", HOME / ".config/net.imput.helium"],
    "chrome": [LOCAL / "Google/Chrome/User Data", HOME / "Library/Application Support/Google/Chrome", HOME / ".config/google-chrome"],
    "edge": [LOCAL / "Microsoft/Edge/User Data", HOME / "Library/Application Support/Microsoft Edge", HOME / ".config/microsoft-edge"],
    "brave": [LOCAL / "BraveSoftware/Brave-Browser/User Data", HOME / "Library/Application Support/BraveSoftware/Brave-Browser",
              HOME / ".config/BraveSoftware/Brave-Browser"],
}

# Search result pages: host pattern -> query parameter. Archer's own pages count too.
ENGINES = [
    (re.compile(r"(^|\.)google\.[a-z.]+$"), "q", "/search"),
    (re.compile(r"(^|\.)bing\.com$"), "q", "/search"),
    (re.compile(r"(^|\.)duckduckgo\.com$"), "q", "/"),
    (re.compile(r"(^|\.)search\.brave\.com$"), "q", "/search"),
    (re.compile(r"(^|\.)search\.yahoo\.com$"), "p", "/search"),
    (re.compile(r"(^|\.)ecosia\.org$"), "q", "/search"),
    (re.compile(r"(^|\.)startpage\.com$"), "query", "/"),
    (re.compile(r"(^|\.)perplexity\.ai$"), "q", "/search"),
    (re.compile(r"^(archer\.localhost|localhost|127\.0\.0\.1)$"), "q", "/search"),
    (re.compile(r"(^|\.)youtube\.com$"), "search_query", "/results"),
]
SKIP_OPENED = re.compile(r"^(chrome|chrome-extension|edge|brave|about|file|data|javascript|devtools):|accounts\.google|/signin|/login|/oauth", re.I)
# Chromium also records site shortcuts and navigation as keyword "searches" (a GitHub repo path, an
# X profile, a YouTube video ID) and in-mail searches: none of these are web searches.
NOT_SEARCH_HOSTS = re.compile(r"(^|\.)(github\.com|x\.com|twitter\.com|mail\.google\.com|outlook\.(live|office)\.com|web\.whatsapp\.com)$")
NAVIGATION = re.compile(r"^[\w.-]+(/[\w.-]+)+/?$|^https?://")


def video_id(q: str) -> bool:
    """A YouTube video ID ("JepcA5TOAo0"), not an 11-letter word ("Shakespeare", "TRANSFORMER")."""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", q)) and (bool(re.search(r"[\d_-]", q)) or not (q.istitle() or q.isupper() or q.islower()))


def find_history(browser: str | None) -> Path:
    for name in ([browser] if browser else ["helium", "chrome", "edge", "brave"]):
        for root in PROFILES[name]:
            path = root / "Default" / "History"
            if path.exists():
                return path
    sys.exit("No browser history found. Pass --history with the path to a profile's History file.")


def when(micro: int) -> datetime:
    return EPOCH + timedelta(microseconds=micro)


def search_of(url: str) -> tuple[str, str] | None:
    """(engine host, query) when this URL is a search results page."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for pattern, key, path in ENGINES:
        if pattern.search(host) and parts.path.startswith(path):
            q = parse_qs(parts.query).get(key, [""])[0].strip()
            if q:
                return host, q
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--browser", choices=sorted(PROFILES))
    ap.add_argument("--history", type=Path, help="path to a profile's History file")
    ap.add_argument("--days", type=int, default=365, help="how far back (default 365)")
    ap.add_argument("--exclude", action="append", default=[], help="drop searches and pages containing this text (repeatable)")
    ap.add_argument("--no-pages", action="store_true", help="export searches only, not the pages opened from them")
    ap.add_argument("--out", type=Path, default=Path("archer-history-export.json"))
    args = ap.parse_args()

    source = args.history or find_history(args.browser)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "History"
        try:
            shutil.copy2(source, copy)
        except OSError:
            sys.exit(f"Couldn't copy {source}. Close the browser and try again.")
        db = sqlite3.connect(copy)
        since = int((datetime.now(timezone.utc) - timedelta(days=args.days) - EPOCH).total_seconds() * 1e6)
        rows = db.execute("SELECT v.id, v.visit_time, v.from_visit, u.url, u.title FROM visits v JOIN urls u ON u.id = v.url "
                          "WHERE v.visit_time >= ? ORDER BY v.visit_time", (since,)).fetchall()
        # Searches typed into the address bar, as Chromium recorded them.
        typed = {url_id: term for url_id, term in db.execute("SELECT url_id, term FROM keyword_search_terms")}
        url_ids = dict(db.execute("SELECT url, id FROM urls"))
        db.close()

    excluded = [e.lower() for e in args.exclude]
    blocked = lambda *texts: any(e in (t or "").lower() for e in excluded for t in texts)
    searches, by_visit = [], {}
    for visit_id, micro, _from, url, title in rows:
        found = search_of(url)
        term = typed.get(url_ids.get(url))
        if not found and not term:
            continue
        engine, q = found if found else ((urlsplit(url).hostname or ""), term)
        q = (term or q).strip()
        if NOT_SEARCH_HOSTS.search(engine) or NAVIGATION.match(q) or video_id(q):
            continue
        if blocked(q) or blocked(url):
            continue
        at = when(micro)
        last = searches[-1] if searches else None
        if last and last["q"].casefold() == q.casefold() and (at - datetime.fromisoformat(last["at"])).total_seconds() < 1800:
            by_visit[visit_id] = last  # a reload or tab switch of the same search
            continue
        entry = {"q": q, "at": at.isoformat(), "engine": engine, "opened": []}
        searches.append(entry)
        by_visit[visit_id] = entry

    if not args.no_pages:
        # A page counts as opened from a search when the browser linked it to the search visit, or,
        # since many engines (Archer included) send no referrer, when it was the next pages visited
        # within a few minutes and before the next search.
        times = [datetime.fromisoformat(s["at"]) for s in searches]
        idx = 0
        for visit_id, micro, from_visit, url, title in rows:
            if visit_id in by_visit or search_of(url) or SKIP_OPENED.search(url) or blocked(url, title):
                continue
            at = when(micro)
            entry, how = by_visit.get(from_visit), "link"
            if entry is None:
                while idx + 1 < len(times) and times[idx + 1] <= at:
                    idx += 1
                if not times or times[idx] > at or (at - times[idx]).total_seconds() > OPEN_WINDOW:
                    continue
                entry, how = searches[idx], "time"
            if len(entry["opened"]) < 8 and all(o["url"] != url for o in entry["opened"]):
                entry["opened"].append({"url": url, "title": title or "", "at": at.isoformat(), "linked_by": how})

    out = {"exported": datetime.now(timezone.utc).isoformat(), "source": source.parent.parent.parent.name or str(source), "days": args.days,
           "searches": searches}
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    opened = sum(len(s["opened"]) for s in searches)
    print(f"Wrote {len(searches)} searches and {opened} opened pages to {args.out.resolve()}")
    print("Open it and skim before sharing: anything you'd rather keep private can be deleted or excluded with --exclude.")


if __name__ == "__main__":
    main()
