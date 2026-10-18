"""Every hero on canned upstream data (the replies tests/test_heroes.py uses), zero external calls.

Run `uv run python tests/heroes_browser_server.py`, then open http://127.0.0.1:4754/gallery for a list of
queries, one per hero: weather, time, sunrise, dictionary, crypto, translate, places, a person, and every
local tool (calculator, units, colour, timestamp, random, timer, metronome, EMI/BMI/tip, solver, graph,
tracking). Pictures are generated placeholders served by this script.
"""
import hashlib
import json
from contextlib import asynccontextmanager
from html import escape
from urllib.parse import quote_plus

import httpx
import uvicorn
from fastapi import Response
from fastapi.responses import HTMLResponse

import test_heroes as fx
from archer.app import app
from archer.searxng import SearXNG

PORT = 4754
ART = f"http://localhost:{PORT}/fixture-art/"
QUERIES = ["weather bengaluru", "time in tokyo", "sunrise bengaluru", "define serendipity", "bitcoin price",
           "thank you in kannada", "cafes in indiranagar", "zendaya", "2^10*3", "100 f to c", "5 km to miles",
           "#1a73e8", "1760000000", "flip a coin", "roll a dice", "random number 1 to 100", "timer 5 min",
           "stopwatch", "metronome 90", "emi calculator", "bmi", "tip calculator", "solve x^2-5x+6=0",
           "solve 2x+3=11", "y=x^2", "track EE123456789IN"]


@app.get("/fixture-art/{name}.jpg")
def fixture_art(name: str) -> Response:
    hue = int(hashlib.md5(name.encode()).hexdigest()[:2], 16) * 360 // 256
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="hsl({hue},55%,42%)"/><stop offset="1" stop-color="hsl({(hue + 60) % 360},45%,14%)"/></linearGradient></defs>'
           f'<rect width="200" height="300" fill="url(#g)"/><circle cx="100" cy="110" r="46" fill="rgba(255,255,255,.18)"/></svg>')
    return Response(svg, media_type="image/svg+xml")


@app.get("/gallery", response_class=HTMLResponse)
def gallery() -> str:
    links = "".join(f'<li><a href="/search?q={quote_plus(q)}">{escape(q)}</a></li>' for q in QUERIES)
    return f"<!doctype html><meta charset=utf-8><title>Heroes</title><body style='background:#0a0a0a;color:#eee;font:15px system-ui'><ul>{links}</ul>"


def reply(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/search" and request.url.host == "fixture":
        return httpx.Response(200, json=fx.EMPTY_SEARCH)
    response = fx.upstream(request)
    if request.url.host == "en.wikipedia.org":   # a picture the browser can load
        body = json.loads(response.content.decode().replace("https://upload.wikimedia.org/z.jpg", ART + "zendaya.jpg"))
        return httpx.Response(200, json=body)
    return response


@asynccontextmanager
async def lifespan(application):
    async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(reply)) as client:
        application.state.searx = SearXNG(client)
        application.state.ext = client
        yield


if __name__ == "__main__":
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
