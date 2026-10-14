"""Offline fixture server for suggestions.browser.cjs; no live provider requests."""
from contextlib import asynccontextmanager

import httpx
import uvicorn

from archer.app import app
from archer.searxng import SearXNG


def reply(request):
    if request.url.path == "/autocompleter":
        q = request.url.params["q"]
        return httpx.Response(200, json=[q, [q + " first", q + " second", "<b>literal text</b>"]])
    if request.url.path == "/search":
        q = request.url.params["q"]
        return httpx.Response(200, json={"results": [], "suggestions": [] if q == "fallback" else [q + " related"], "corrections": [q + " corrected"]})
    return httpx.Response(404, json={})


@asynccontextmanager
async def offline_lifespan(application):
    async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(reply)) as http:
        application.state.searx = SearXNG(http)
        application.state.ext = http
        yield


if __name__ == "__main__":
    app.router.lifespan_context = offline_lifespan
    uvicorn.run(app, host="127.0.0.1", port=4748, log_level="warning")
