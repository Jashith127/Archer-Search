"""Isolated AI browser fixture. Uses temporary preferences and zero external calls."""
import asyncio
from contextlib import asynccontextmanager
import json
from tempfile import TemporaryDirectory
from pathlib import Path

import struct
import zlib

import httpx
import uvicorn
from fastapi import Response

from archer.app import app
from archer.ai_service import AIService
from archer.config import Settings
from archer.searxng import SearXNG
from archer.settings_service import SettingsService


class BrowserProvider:
    async def plan(self, system, payload, tools, max_output):
        await asyncio.sleep(.3)
        outcomes = payload.get("tool_outcomes", [])
        if not outcomes:
            calls = [{"name": "loan_payment", "arguments": json.dumps({"principal": 1000000, "annual_rate": 10, "months": months})} for months in (60, 48)]
        elif not any(o["tool"] == "show_cards" for o in outcomes):
            ids = [e["id"] for e in payload["evidence"] if e["kind"] == "tool"]
            calls = [{"name": "show_cards", "arguments": json.dumps({"source_ids": ids})}]
        else:
            calls = []
        return {"tool_calls": [{"function": call} for call in calls]}, {"prompt_tokens": 300, "completion_tokens": 100}

    async def generate(self, system, payload, max_output, on_block):
        tool = [e for e in payload["evidence"] if e["kind"] == "tool"]
        blocks = [{"kind": "paragraph", "text": "A shorter loan term changes both your monthly payment and the total interest. Archer's calculator keeps those tradeoffs visible, with editable inputs below.",
                   "citations": [e["id"] for e in tool] or ["S1"]}]
        if len(tool) >= 2:
            parsed = [(e["id"], json.loads(e["text"])) for e in tool[:2]]
            rows = []
            for label, key in (("Monthly payment", "monthly_payment"), ("Total interest", "total_interest"), ("Total repaid", "total_payment")):
                rows.append([{"text": label}, *[{"text": str(data[key]), "citations": [sid]} for sid, data in parsed]])
            blocks.append({"kind": "table", "heading": "The repayment tradeoff", "columns": ["For a ₹10 lakh loan", "5 years", "4 years"], "rows": rows})
            blocks.append({"kind": "list", "heading": "What the comparison means for you", "items": [
                {"text": "**Monthly flexibility:** A longer term spreads the principal across more payments. Use the monthly payment alongside your regular expenses when comparing the scenarios.", "citations": [parsed[0][0]]},
                {"text": "**Total borrowing cost:** The shorter schedule pays down the balance sooner. The calculator separates total interest from the principal so that the tradeoff is visible.", "citations": [parsed[1][0]]},
                {"text": "**Keep the assumptions consistent:** Both scenarios use the same principal and fixed annual rate. Changing several inputs at once can make it harder to understand which choice is responsible for the difference.", "citations": [p[0] for p in parsed]}]})
            blocks.append({"kind": "inference", "heading": "Choosing a term", "text": "If the higher monthly payment fits comfortably within your budget, the shorter schedule may be worth considering. If it leaves too little room for unexpected expenses, compare a longer term and a realistic prepayment plan. The editable cards let you explore those changes locally without another AI call."})
        blocks.append({"kind": "limitation", "text": "Offline demonstration using fixture sources. The calculation assumes a fixed rate and excludes lender fees and prepayment charges."})
        for block in blocks:
            await asyncio.sleep(.35)
            on_block(block)
        return {"blocks": blocks, "followups": ["What if the interest rate changes?", "Compare a smaller loan amount"]}, {"prompt_tokens": 500, "completion_tokens": 250}


def fixture_png(n):
    """A small generated gradient, so image rows render without any external request."""
    width, height = 160, 110
    hue = [(64, 92, 120), (120, 96, 64), (70, 110, 84), (110, 70, 104), (90, 90, 96), (52, 84, 110)][n % 6]
    rows = b"".join(b"\x00" + bytes(c for x in range(width) for c in (
        min(255, hue[0] + x // 3 + y // 4), min(255, hue[1] + y // 2), min(255, hue[2] + x // 4))) for y in range(height))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


@app.get("/fixture/img/{n}.png")
async def fixture_image(n: int):
    return Response(fixture_png(n), media_type="image/png")


def reply(request):
    if request.url.path == "/search" and request.url.params.get("categories") == "images":
        return httpx.Response(200, json={"results": [
            {"title": f"Loan repayment chart {n}", "url": f"https://example.com/chart-{n}", "img_src": f"http://127.0.0.1:4749/fixture/img/{n}.png",
             "thumbnail_src": f"http://127.0.0.1:4749/fixture/img/{n}.png", "resolution": "1200x800", "engines": ["fixture images"]} for n in range(8)]})
    if request.url.path == "/search":
        return httpx.Response(200, json={"results": [
            {"title": "How monthly loan repayments work", "url": "https://example.com/repayments", "content": "A shorter loan term increases monthly repayments while reducing the total interest when the rate and principal remain the same.", "engines": ["fixture search", "fixture index"]},
            {"title": "Understanding loan prepayment conditions", "url": "https://example.org/prepayment", "content": "Review your lender's terms for fees and prepayment conditions before changing the repayment schedule.", "engines": ["fixture search"]},
            {"title": "A guide to comparing loan terms", "url": "https://example.net/loans", "content": "Compare the monthly payment, total interest and total amount repaid under the same assumptions.", "engines": ["fixture index"]}], "suggestions": []})
    if request.url.path == "/autocompleter":
        return httpx.Response(200, json=[request.url.params["q"], []])
    return httpx.Response(404, json={})


@asynccontextmanager
async def lifespan(application):
    with TemporaryDirectory(prefix="archer-ai-browser-") as directory:
        settings = Settings(_env_file=None, DEEPSEEK_API_KEY="offline-fixture")
        ai = AIService(settings, Path(directory))
        ai.provider = BrowserProvider()
        async def read(url):
            await asyncio.sleep(.25)
            return "This fixture explains loan repayments, total interest, monthly payments and the need to check lender fees. " * 8
        ai.reader.read = read
        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(reply)) as client:
            application.state.ai = ai
            application.state.searx = SearXNG(client)
            application.state.ext = client
            # Settings edits go to a throwaway .env, never the real one.
            application.state.settings_service = SettingsService(Path(directory) / ".env")
            yield
        await ai.close()


if __name__ == "__main__":
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=4749, log_level="warning")
