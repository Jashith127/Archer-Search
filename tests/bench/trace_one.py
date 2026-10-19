"""Capture the exact NDJSON trace for one query against the running Archer."""
import json
import re
import sys
import time

import httpx

BASE = "http://127.0.0.1:4747"
QUERY = sys.argv[1] if len(sys.argv) > 1 else "riot vs valve"
DEPTH = sys.argv[2] if len(sys.argv) > 2 else "research"
FRESH = "--replay" not in sys.argv

client = httpx.Client(base_url=BASE, timeout=240, follow_redirects=True)
html = client.get("/search", params={"q": QUERY}).text
snap = re.search(r'data-snapshot="([^"]+)"', html)
csrf = re.search(r'data-csrf="([^"]+)"', html)
if not snap or not csrf:
    sys.exit("no AI panel: snapshot/csrf missing")
sid, token = snap.group(1), csrf.group(1)

print(f"query        : {QUERY!r}")
print(f"depth        : {DEPTH}")
print(f"snapshot     : {sid[:16]}")
full = re.findall(r'<div class="ai-source" data-source-id="(S\d+)"[^>]*>(.*?)</div>\s*(?=<div class="ai-source"|</div>\s*<div class="ai-compare-bar")', html, re.S)
if full:
    print(f"snapshot evidence (server-rendered panel): {len(full)} items")
    for s_id, blob in full:
        title = re.sub(r"<[^>]+>", " ", blob)
        title = " ".join(title.split())[:110]
        print(f"   {s_id}  {title}")

body = {"snapshot": sid, "depth": DEPTH}
if FRESH:
    body["regenerate"] = True
headers = {"Origin": BASE, "Sec-Fetch-Site": "same-origin",
           "X-Archer-CSRF": token, "Content-Type": "application/json"}

print("\n--- event stream ---")
t0 = time.perf_counter()
events, sources = [], {}
with client.stream("POST", "/api/ai/runs", headers=headers, json=body) as r:
    buf = ""
    for chunk in r.iter_text():
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            if not line.strip():
                continue
            e = json.loads(line)
            events.append(e)
            dt = time.perf_counter() - t0
            name, data = e["event"], e["data"]
            if name == "sources":
                for s in data["sources"]:
                    sources[s["id"]] = s
                print(f"  [{dt:5.2f}s] sources: {len(data['sources'])}")
            elif name == "status":
                print(f"  [{dt:5.2f}s] status  [{data['stage']}] {data['text']}")
            elif name == "notice":
                print(f"  [{dt:5.2f}s] NOTICE  {data['text']}")
            elif name == "card":
                print(f"  [{dt:5.2f}s] card    kind={data.get('kind')} src={data.get('source_id')}")
            elif name == "block":
                cites = data.get("citations") or []
                print(f"  [{dt:5.2f}s] BLOCK   kind={data.get('kind'):<10} cites={cites}")
                for it in data.get("items") or []:
                    if isinstance(it, dict):
                        print(f"                    item cites={it.get('citations')} {str(it.get('text'))[:70]!r}")
            elif name in ("complete", "error"):
                print(f"  [{dt:5.2f}s] {name.upper()}: {json.dumps(data)[:400]}")

print("\n--- final sources actually shown to the model ---")
for s in sources.values():
    print(f"  {s['id']}  kind={s['kind']:<10} verified={s.get('verified')} "
          f"{s.get('title','')[:70]!r}")

blocks = [e["data"] for e in events if e["event"] == "block"]
if blocks:
    print("\n--- answer text ---")
    for b in blocks:
        if b.get("text"):
            print(f"[{b['kind']}] {b['text']}")
        for it in b.get("items") or []:
            if isinstance(it, dict) and it.get("text"):
                print(f"   - {it.get('label')}: {it['text']}  {it.get('citations')}")
client.close()