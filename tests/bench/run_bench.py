"""Evaluation harness for Archer's AI answer path.

Drives a RUNNING Archer instance over HTTP and records, per case: per-stage
latency, model/search/page-read counts, cost, and the full event stream. Then
applies the deterministic checks listed in cases.json.

Two grades are reported and they are NOT interchangeable:

  automated   - mechanical checks. Proves a citation resolves and that a cited
                passage contains the literal numbers in a claim.
  human_review- NOT automated. Proves a cited passage actually supports the
                claim, that the answer addresses the question, and that no
                uncited factual claim is present. Must be filled in by a person
                reading the cited passages.

Usage:
    python tests/bench/run_bench.py --base http://127.0.0.1:4747 --out tests/bench/results
    python tests/bench/run_bench.py --list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.json"

# Mirrors archer.ai_service.check_numbers so the harness grades against the same
# rule the application applies.
NUM = re.compile(r"(?<!\w)\d[\d,]*(?:\.\d+)?")


def load_cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def session(base: str, query: str) -> tuple[httpx.Client, str, str]:
    """Return (client with cookie, snapshot id, csrf). The AI panel only renders
    on tab=all page=1 with a snapshot, so this is also a panel-availability check."""
    client = httpx.Client(base_url=base, timeout=120, follow_redirects=True)
    html = client.get("/search", params={"q": query}).text
    snap = re.search(r'data-snapshot="([^"]+)"', html)
    csrf = re.search(r'data-csrf="([^"]+)"', html)
    if not snap or not csrf:
        raise RuntimeError(
            "no AI snapshot/csrf in /search response - AI panel unavailable for "
            f"{query!r} (is the provider configured and consent given?)"
        )
    return client, snap.group(1), csrf.group(1)


def run_once(client: httpx.Client, base: str, snap: str, csrf: str,
             depth: str, *, question: str = "", parent: str = "",
             selected: list[str] | None = None, regenerate: bool = False,
             timeout: float = 240.0) -> dict:
    """One POST /api/ai/runs, timing each NDJSON event as it lands."""
    body: dict = {"snapshot": snap, "depth": depth}
    if question:
        body["question"] = question
    if parent:
        body["parent"] = parent
    if selected:
        body["selected"] = selected
    if regenerate:
        body["regenerate"] = True
    headers = {
        "Origin": base.rstrip("/"),
        "Sec-Fetch-Site": "same-origin",
        "X-Archer-CSRF": csrf,
        "Content-Type": "application/json",
    }
    t0 = time.perf_counter()
    marks: dict[str, float] = {}
    events: list[dict] = []
    run_id = ""
    with client.stream("POST", "/api/ai/runs", headers=headers, json=body,
                       timeout=timeout) as response:
        if response.status_code != 200:
            return {"ok": False, "http_status": response.status_code,
                    "error": response.read().decode("utf-8", "replace")[:500]}
        buf = ""
        for chunk in response.iter_text():
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if not line.strip():
                    continue
                now = time.perf_counter() - t0
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                events.append(entry)
                name = entry.get("event")
                marks.setdefault(f"t_{name}", now)
                if name == "run":
                    run_id = entry.get("data", {}).get("id", "")
    return {"ok": True, "run_id": run_id, "total_s": time.perf_counter() - t0,
            "marks": marks, "events": events}


def first(events: list[dict], name: str) -> dict | None:
    return next((e["data"] for e in events if e.get("event") == name), None)


def all_blocks(events: list[dict]) -> list[dict]:
    return [e["data"] for e in events if e.get("event") == "block"]


def last_sources(events: list[dict]) -> dict[str, dict]:
    """Prefer the sources event emitted just before generation (full text); fall
    back to the last event that carried any."""
    srcs = [e["data"]["sources"] for e in events
            if e.get("event") == "sources" and "sources" in e.get("data", {})]
    if not srcs:
        return {}
    return {s["id"]: s for s in srcs[-1]}


def check_numbers(text: str, cited: list[str], sources: dict[str, dict]) -> list[str]:
    """Numbers written in a cited claim that are absent from the cited sources."""
    blob = " ".join(sources[i]["text"] for i in cited if i in sources).replace(",", "")
    bad = []
    for raw in NUM.findall(text):
        norm = raw.replace(",", "")
        if not re.search(r"(?<![\d.])" + re.escape(norm) + r"(?!\d|\.\d)", blob):
            bad.append(raw)
    return bad


def collect_text(block: dict) -> str:
    parts = [block.get("heading", ""), block.get("text", "")]
    for key in ("items", "rows", "pros", "cons"):
        for item in block.get(key) or []:
            if isinstance(item, dict):
                parts += [item.get("label", ""), item.get("value", ""), item.get("text", "")]
            else:
                parts += [c.get("text", "") for c in item if isinstance(c, dict)]
    return " ".join(p for p in parts if p)


def collect_citations(block: dict) -> list[str]:
    """Flat list of citation ids across the block, its items and its table cells.

    `rows` is a list of rows, each a list of cells, so this must be flattened
    rather than concatenated."""
    cites: list[str] = list(block.get("citations") or [])

    def absorb(container) -> None:
        if isinstance(container, dict):
            cites.extend(container.get("citations") or [])
        elif isinstance(container, (list, tuple)):
            for entry in container:
                absorb(entry)

    for key in ("items", "rows", "pros", "cons"):
        absorb(block.get(key))
    return [c for c in cites if isinstance(c, str)]


def grade(case: dict, run: dict) -> dict:
    events = run.get("events") or []
    blocks = all_blocks(events)
    sources = last_sources(events)
    done = first(events, "complete") or {}
    err = first(events, "error")
    results: dict[str, object] = {}
    problems: list[str] = []

    if err is not None:
        results["terminal"] = "error"
        results["error_text"] = err.get("text", "")
    else:
        results["terminal"] = "complete"

    results["blocks"] = len(blocks)
    summary = next((b for b in blocks if b.get("kind") == "summary"), None)
    results["summary_is_first"] = bool(blocks) and blocks[0].get("kind") == "summary"
    results["summary_present"] = summary is not None

    # 1. every citation resolves
    unresolved = sorted({c for b in blocks for c in collect_citations(b)} - set(sources))
    results["citations_resolve"] = not unresolved
    if unresolved:
        problems.append(f"unresolved citation ids: {unresolved}")

    # 2. the summary block carries a citation
    results["summary_cited"] = bool(summary and summary.get("citations"))
    if summary and not results["summary_cited"]:
        problems.append("summary block has no citation")
    elif not summary:
        problems.append("no summary block in answer")

    # 3. every literal number in a cited block appears in the cited sources
    unsupported_nums: list[str] = []
    for b in blocks:
        cites = collect_citations(b)
        if not cites:
            continue
        unsupported_nums += check_numbers(collect_text(b), cites, sources)
    results["unsupported_numbers"] = sorted(set(unsupported_nums))
    results["numbers_supported"] = not unsupported_nums
    if unsupported_nums:
        problems.append(f"unsupported quantities: {sorted(set(unsupported_nums))}")

    # 4. summary not solely supported by unverified snippets
    if summary and summary.get("citations"):
        cited = [sources.get(c, {}) for c in summary["citations"]]
        only_snippets = cited and all(
            s.get("kind") in ("excerpt", "discussion") or "could not be read" in
            (s.get("limitation") or "") for s in cited)
        results["summary_not_snippet_only"] = not only_snippets
        if only_snippets:
            problems.append("summary rests only on unverified snippets")
    else:
        results["summary_not_snippet_only"] = None

    # 5/6. required facts present, and carried by a cited block
    props = case.get("properties", {})
    facts = []
    for fact in props.get("must_cite", []):
        toks = [t.lower() for t in fact.get("tokens", [])]
        hit_blocks = [b for b in blocks
                      if any(t in collect_text(b).lower() for t in toks)]
        facts.append({
            "label": fact.get("label"),
            "present": bool(hit_blocks),
            "cited": any(collect_citations(b) for b in hit_blocks),
        })
    results["facts"] = facts
    missing = [f["label"] for f in facts if not f["present"]]
    uncited = [f["label"] for f in facts if f["present"] and not f["cited"]]
    if missing:
        problems.append(f"required facts absent: {missing}")
    if uncited:
        problems.append(f"required facts present but uncited: {uncited}")

    # metrics
    results["model_calls"] = done.get("model_calls")
    results["searches"] = done.get("searches")
    results["pages_read"] = done.get("pages_read")
    results["cost_usd"] = done.get("cost_usd")
    results["cost_is_ceiling"] = done.get("cost_is_ceiling")
    results["evidence_label"] = done.get("evidence_label")
    results["input_tokens"] = done.get("input_tokens")
    results["output_tokens"] = done.get("output_tokens")
    # A replayed answer costs no model call and returns the previous run's
    # evidence. It must be visible in results, never mistaken for a fresh run.
    results["replayed"] = bool(done.get("saved"))
    results["t_first_block_s"] = run.get("marks", {}).get("t_block")
    results["t_complete_s"] = run.get("total_s")
    results["sources_count"] = len(sources)
    results["notices"] = [e["data"].get("text") for e in events
                          if e.get("event") == "notice"]

    # Separate infrastructure failure from answer quality. A provider outage or a
    # rate-limited engine makes a case fail for reasons unrelated to citation
    # grounding; folding those into an answer-quality pass rate would misreport
    # what changed.
    provider_markers = ("returned HTTP", "unreachable", "didn't answer", "rate-limit",
                        "isn't set up", "is unavailable", "bot-check")
    infra = [n for n in results["notices"] if any(m in n for m in provider_markers)]
    results["infra_degraded"] = bool(infra) or (not blocks and err is not None)
    results["infra_notices"] = infra

    results["human_review"] = {
        "citation_support": None,
        "addresses_question": None,
        "unsupported_claims": None,
        "notes": None,
    }
    results["problems"] = problems
    results["pass"] = not problems
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:4747")
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--depth", default="", help="override all cases to this depth")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="send regenerate=true so the saved-answer replay cache is "
                         "bypassed and every case runs a real model call")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--pause", type=float, default=6.0,
                    help="seconds between cases, so the benchmark does not exhaust "
                         "the rate-limited engines it is measuring")
    args = ap.parse_args()

    cases = load_cases()
    if args.only:
        wanted = {c.strip() for c in args.only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    if args.list:
        for c in cases:
            print(f"{c['id']:<18} {c['depth']:<9} {c['category']:<38} {c['query']}")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    meta = {"base": args.base, "stamp": stamp, "python": sys.version.split()[0],
            "cases": len(cases)}

    rows = []
    for index, case in enumerate(cases):
        if index and args.pause:
            time.sleep(args.pause)
        depth = args.depth or case["depth"]
        entry: dict = {"id": case["id"], "query": case["query"],
                       "depth": depth, "category": case["category"]}
        try:
            client, snap, csrf = session(args.base, case["query"])
        except Exception as exc:
            entry["error"] = f"setup: {exc}"
            rows.append(entry)
            print(f"{case['id']:<18} SETUP FAILED: {exc}")
            continue

        run = run_once(client, args.base, snap, csrf, depth,
                       regenerate=args.fresh, timeout=args.timeout)
        entry["run"] = {k: v for k, v in run.items() if k != "events"}
        if not run.get("ok"):
            entry["error"] = run.get("error")
            rows.append(entry)
            print(f"{case['id']:<18} HTTP {run.get('http_status')} {run.get('error','')[:120]}")
            continue

        entry["grade"] = grade(case, run)

        # follow-up pair
        if case.get("followup"):
            fu = run_once(client, args.base, snap, csrf, depth,
                          question=case["followup"], parent=run.get("run_id", ""),
                          timeout=args.timeout)
            if fu.get("ok"):
                entry["followup"] = {"run": {k: v for k, v in fu.items() if k != "events"}}
                entry["followup"]["grade"] = grade(case.get("followup_properties", {}), fu)
            else:
                entry["followup"] = {"error": fu.get("error")}

        (outdir / f"case-{case['id']}.json").write_text(
            json.dumps({"case": case, **entry}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        rows.append(entry)

        g = entry["grade"]
        flag = "PASS" if g.get("pass") else ("DEGR" if g.get("infra_degraded") else "FAIL")
        print(f"{case['id']:<18} {flag}  blocks={g.get('blocks'):<2} "
              f"calls={g.get('model_calls')} searches={g.get('searches')} "
              f"pages={g.get('pages_read')} "
              f"t1={g.get('t_first_block_s') or 0:.2f}s "
              f"tot={g.get('t_complete_s') or 0:.2f}s "
              f"{'; '.join(g.get('problems', [])[:2])}")
        client.close()

    ok = [r for r in rows if r.get("grade", {}).get("pass")]
    measured = [r for r in rows if r.get("grade")]
    degraded = [r for r in measured if r["grade"].get("infra_degraded")]
    clean = [r for r in measured if not r["grade"].get("infra_degraded")]
    summary = {
        **meta,
        "passed": len(ok),
        "total": len(rows),
        "graded": len(measured),
        "infra_degraded": len(degraded),
        "degraded_ids": [r["id"] for r in degraded],
        "clean_graded": len(clean),
        "clean_passed": sum(1 for r in clean if r["grade"].get("pass")),
        "median_first_block_s": _median([r["grade"].get("t_first_block_s") for r in measured]),
        "median_total_s": _median([r["grade"].get("t_complete_s") for r in measured]),
        "total_model_calls": sum(r["grade"].get("model_calls") or 0 for r in measured),
        "total_cost_usd": round(sum(r["grade"].get("cost_usd") or 0 for r in measured), 6),
        "citation_failures": sum(
            1 for r in measured if r["grade"].get("citations_resolve") is False),
        "unsupported_number_failures": sum(
            1 for r in measured if r["grade"].get("numbers_supported") is False),
        "snippet_only_failures": sum(
            1 for r in measured if r["grade"].get("summary_not_snippet_only") is False),
        "median_pages_read": _median([r["grade"].get("pages_read") for r in measured]),
        "sample_size_note": (
            f"n={len(measured)} is a small benchmark. Descriptive counts only, not "
            "statistically significant. infra_degraded cases failed for provider or "
            "network reasons, not answer quality; read clean_* for the quality view. "
            "Semantic quality requires the human_review fields, which are unfilled."),
    }
    (outdir / f"summary-{stamp}.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=2))
    return 0


def _median(values):
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    mid = len(vals) // 2
    return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 3)


if __name__ == "__main__":
    raise SystemExit(main())