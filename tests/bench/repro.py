"""Reproduce one offline AI run and print every event, for diagnosing failures."""
import asyncio
import json
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from archer.app import app
from archer.ai_service import AIService
from archer.config import Settings
from archer.searxng import SearXNG

from tests.test_ai import FakeProvider, search_reply

QUERY = sys.argv[1] if len(sys.argv) > 1 else "emi for 10 lakh loan at 10% for 5 years"
DEPTH = sys.argv[2] if len(sys.argv) > 2 else "research"

original = app.router.lifespan_context


@asynccontextmanager
async def lifespan(application):
    settings = Settings(_env_file=None, DEEPSEEK_API_KEY="fixture-key")
    ai = AIService(settings, Path(TemporaryDirectory().name))
    ai.provider = FakeProvider()

    async def read(url):
        return "A source-backed explanation from the public page. " * 10
    ai.reader.read = read

    async with httpx.AsyncClient(base_url="http://fixture",
                                 transport=httpx.MockTransport(search_reply)) as http:
        application.state.ai = ai
        application.state.searx = SearXNG(http)
        application.state.ext = http
        yield
    await ai.close()


app.router.lifespan_context = lifespan
BASE = "http://archer.localhost:4747"
with TestClient(app, base_url=BASE) as client:
    html = client.get("/search", params={"q": QUERY}).text
    sid = re.search(r'data-snapshot="([^"]+)"', html).group(1)
    csrf = re.search(r'data-csrf="([^"]+)"', html).group(1)
    headers = {"Origin": BASE, "Sec-Fetch-Site": "same-origin", "X-Archer-CSRF": csrf}
    client.post("/api/ai/preferences", headers=headers,
                json={"consent": True, "mode": "classic"})
    resp = client.post("/api/ai/runs", headers=headers,
                       json={"snapshot": sid, "depth": DEPTH})
    events = [json.loads(line) for line in resp.text.splitlines()]
    for e in events:
        name = e["event"]
        data = e["data"]
        if name == "sources":
            print(f"  sources: {len(data.get('sources', []))} items")
            for s in data["sources"]:
                print(f"      {s['id']} kind={s['kind']:<10} verified={s.get('verified')} "
                      f"lim={(s.get('limitation') or '')[:40]!r}")
        elif name == "block":
            print(f"  BLOCK {data.get('kind')}: cites={data.get('citations')} "
                  f"text={(data.get('text') or '')[:80]!r}")
        elif name == "card":
            print(f"  CARD {data.get('kind')} {data.get('source_id')}")
        else:
            print(f"  {name}: {json.dumps(data, ensure_ascii=False)[:900]}")
    print("\nplans:", getattr(app.state.ai.provider, "plans", None),
          "answers:", getattr(app.state.ai.provider, "answers", None))
app.router.lifespan_context = original