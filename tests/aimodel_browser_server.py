"""Isolated AI-model card fixture: a synthetic Epoch zip and OpenRouter price list, zero external calls.

Run `uv run python tests/aimodel_browser_server.py`, then open
http://127.0.0.1:4750/search?q=claude+opus+4.5. The numbers are made up; only the file shapes are real.
"""
import csv
import io
import os
import random
import zipfile
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import uvicorn

from archer.app import app
from archer.searxng import SearXNG
from archer.sources import aimodels, modelprices

# name, org, release, eci, GPQA, SWE-bench, (input $/M, output $/M) or None
MODELS = [
    ("Claude Opus 4.5", "Anthropic", "2025-11-24", 150, .87, .80, (5, 25)),
    ("Claude Opus 4.1", "Anthropic", "2025-08-05", 144, .80, .74, (15, 75)),
    ("Claude Opus 4", "Anthropic", "2025-05-22", 142, .79, .72, (15, 75)),
    ("Claude Sonnet 4.5", "Anthropic", "2025-09-29", 146, .83, .77, (3, 15)),
    ("Claude Haiku 4.5", "Anthropic", "2025-10-15", 136, .73, .73, (1, 5)),
    ("Gemini 3 Pro", "Google DeepMind", "2025-11-18", 153, .91, .76, (2, 12)),
    ("Gemini 2.5 Pro", "Google DeepMind", "2025-06-17", 141, .84, .63, (1.25, 10)),
    ("Gemini 2.5 Flash", "Google DeepMind", "2025-06-17", 132, .78, .55, (.3, 2.5)),
    ("GPT-5.1", "OpenAI", "2025-11-12", 149, .88, .76, (1.25, 10)),
    ("GPT-5", "OpenAI", "2025-08-07", 147, .86, .73, (1.25, 10)),
    ("GPT-5 mini", "OpenAI", "2025-08-07", 138, .80, .65, (.25, 2)),
    ("o3", "OpenAI", "2025-04-16", 143, .83, .69, (2, 8)),
    ("Grok 4", "xAI", "2025-07-09", 145, .87, .70, (3, 15)),
    ("DeepSeek-V3.2", "DeepSeek", "2025-12-01", 146, .82, .67, (.28, .42)),
    ("Kimi K2 Thinking", "Moonshot", "2025-11-06", 144, .84, .71, (.6, 2.5)),
    ("Qwen3 Max", "Alibaba", "2025-09-05", 140, .80, .62, (1.2, 6)),
    ("GLM-4.6", "Z.ai (Zhipu AI)", "2025-09-30", 139, .78, .64, (.6, 2.2)),
    ("MiniMax-M2", "MiniMax", "2025-10-27", 137, .76, .63, (.3, 1.2)),
    ("Mistral Medium 3", "Mistral AI", "2025-05-07", 128, .62, .40, (.4, 2)),
    ("Llama 4 Maverick", "Meta AI", "2025-04-05", 126, .67, .30, (.15, .6)),
]


def epoch_zip() -> bytes:
    rnd = random.Random(7)
    eci, scores, versions, gpqa, swe = io.StringIO(), io.StringIO(), io.StringIO(), io.StringIO(), io.StringIO()
    eci.write("Model,Display name,eci,date,Organization,Accessibility group\n")
    scores.write("Model,benchmark,performance\n")
    versions.write("model_version,model_group,display_name\n")
    gpqa.write("Model version,Best score (across scorers),Release date\n")
    swe.write("Model version,Best score (across scorers),Release date\n")
    for name, org, released, index, g, s, _ in MODELS:
        weights = "Open weights" if org in ("DeepSeek", "Moonshot", "Alibaba", "Z.ai (Zhipu AI)", "MiniMax", "Meta AI") else "Closed weights"
        csv.writer(eci).writerow([name, name, index, released, org, weights])
        for effort, scale in (("low", .92), ("high", 1)):
            version = f"{name.lower().replace(' ', '-')}_{effort}"
            csv.writer(versions).writerow([version, name, f"{name} ({effort})"])
            csv.writer(gpqa).writerow([version, round(g * scale, 3), released])
            csv.writer(swe).writerow([version, round(s * scale, 3), released])
    # Background population: older and unlinked results, as Epoch's files carry them.
    start = date(2023, 3, 1)
    for i in range(70):
        d = start + timedelta(days=rnd.randrange(0, 950))
        progress = (d - start).days / 950
        csv.writer(eci).writerow([f"Model {i}", f"Model {i}", round(105 + 35 * progress + rnd.uniform(-8, 6)), d, "Other", ""])
        csv.writer(gpqa).writerow([f"other-{i}", round(min(.9, .3 + .5 * progress + rnd.uniform(-.12, .08)), 3), d])
        csv.writer(swe).writerow([f"other-{i}", round(max(.02, .05 + .6 * progress + rnd.uniform(-.15, .05)), 3), d])
    meta = ("benchmark,source_file,score_column,scale,random_baseline\n"
            "GPQA diamond,gpqa_diamond.csv,Best score (across scorers),1.0,0.25\n"
            "SWE-bench Verified (Bash Only),swe_bench_verified.csv,Best score (across scorers),1.0,0\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(aimodels._ECI, eci.getvalue())
        z.writestr(aimodels._SCORES, scores.getvalue())
        z.writestr(aimodels._BENCH_META, meta)
        z.writestr(aimodels._VERSIONS, versions.getvalue())
        z.writestr("gpqa_diamond.csv", gpqa.getvalue())
        z.writestr("swe_bench_verified.csv", swe.getvalue())
    return buf.getvalue()


def openrouter() -> dict:
    data = []
    for name, org, *_, price in MODELS:
        if price:
            slug = name.lower().replace(" ", "-")
            data.append({"id": f"{org.split()[0].lower()}/{slug}", "name": f"{org.split()[0]}: {name}",
                         "pricing": {"prompt": str(price[0] / 1e6), "completion": str(price[1] / 1e6),
                                     "input_cache_read": str(price[0] / 1e7) if org == "Anthropic" else "0"}})
    return {"data": data}


def reply(request: httpx.Request) -> httpx.Response:
    if request.url.host == "epoch.ai":
        return httpx.Response(200, content=epoch_zip())
    if request.url.host == "openrouter.ai" and not os.environ.get("NO_PRICES"):  # NO_PRICES=1: date-axis charts
        return httpx.Response(200, json=openrouter())
    if request.url.host == "api.frankfurter.app":
        return httpx.Response(200, json={"amount": 1.0, "base": "USD", "date": "2026-10-13", "rates": {"INR": 88.21}})
    if request.url.path == "/search":
        return httpx.Response(200, json={"results": [], "suggestions": []})
    return httpx.Response(404)


@asynccontextmanager
async def lifespan(application):
    with TemporaryDirectory(prefix="archer-aimodel-browser-") as directory:
        aimodels.CACHE_FILE = Path(directory) / "epoch.zip"
        modelprices.CACHE_FILE = Path(directory) / "prices.json"
        async with httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(reply)) as client:
            application.state.searx = SearXNG(client)
            application.state.ext = client
            yield


if __name__ == "__main__":
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=4750, log_level="warning")
