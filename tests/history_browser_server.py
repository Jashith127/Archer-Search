"""Offline fixture server for the history page and personal results, on :4752.

Uses a throwaway history database seeded with a few searches and visits, word matching (no model
download) and canned search results; nothing goes to a live provider.
"""
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn

from archer import history
from archer.app import app
from archer.config import settings
from archer.history import History
from archer.personalize import Personalizer
from archer.searxng import SearXNG

RESULTS = [
    ("https://www.pinterest.com/pin/1", "1440p monitor ideas", "Pins about monitors."),
    ("https://www.example-shop.com/monitors", "Buy 1440p gaming monitors", "Monitors for every budget, 27 inch 1440p."),
    ("https://www.tomshardware.com/best-gaming-monitors", "Best gaming monitors 2026", "We tested 1440p and 4K gaming monitors."),
    ("https://www.reddit.com/r/buildapc/comments/1", "Is 1440p worth it on an RTX 4050 laptop?", "Discussion about laptop GPUs driving 1440p."),
    ("https://www.reddit.com/r/Monitors/comments/2", "1440p vs 4K at 27 inch", "Pixel density thread."),
    ("https://www.rtings.com/monitor/reviews/best/by-usage/gaming", "The best gaming monitors", "Lab-tested gaming monitor picks."),
]


def reply(request):
    if request.url.path == "/autocompleter":
        q = request.url.params["q"]
        return httpx.Response(200, json=[q, [q + " 27 inch", q + " 144hz"]])
    if request.url.path == "/search":
        return httpx.Response(200, json={"results": [{"url": u, "title": t, "content": c, "engines": ["duckduckgo"]} for u, t, c in RESULTS],
                                         "suggestions": ["1440p monitor 27 inch"], "corrections": []})
    return httpx.Response(404, json={})


def seed(store: History) -> None:
    now = time.time()
    visits = [
        ("RTX 4050 laptop 1440p gaming monitor", 3, [("https://www.rtings.com/monitor/reviews/lg-27gp850", "LG 27GP850 review", "rtings")]),
        ("best 1440p gaming monitor", 4, [("https://www.rtings.com/monitor/reviews/best/by-resolution/1440p", "Best 1440p monitors", "")]),
        ("27-inch monitor pixel density", 2, [("https://www.rtings.com/monitor/learn/ppi", "Pixel density explained", "rtings")]),
        ("best display resolution for gaming", 1, [("https://www.rtings.com/monitor/tests/resolution", "Monitor resolution test", "")]),
        ("MSI vs Lenovo monitor warranty India", 0.2, []),
        ("pasta carbonara recipe", 0.1, [("https://www.seriouseats.com/carbonara", "The best carbonara", "")]),
        ("carbonara without cream", 0.05, []),
    ]
    for q, days, clicks in visits:
        sid = store.record_search(q, now=now - days * 86400)
        for url, title, snippet in clicks:
            store.record_click(sid, url, title, snippet, now=now - days * 86400 + 30)
    store.set_site("pinterest.com", "block")


@asynccontextmanager
async def offline_lifespan(application):
    folder = Path(tempfile.mkdtemp(prefix="archer-history-"))
    history.DB_PATH = folder / "history.sqlite3"
    settings.semantic_model = "lexical"
    settings.search_provider = "searxng"
    settings.search_fallback = False
    application.state.history = History()
    application.state.personal = Personalizer(application.state.history)
    seed(application.state.history)
    async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(reply)) as http:
        application.state.searx = SearXNG(http)
        application.state.ext = http
        yield
    application.state.history.close()


if __name__ == "__main__":
    app.router.lifespan_context = offline_lifespan
    uvicorn.run(app, host="127.0.0.1", port=4752, log_level="warning")
