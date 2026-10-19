"""Regression tests for Deliverable 1.

Each test pins a behaviour this deliverable changed, so a later change cannot
quietly undo it. Covers:
  - evidence provenance (verified vs snippet-only)
  - U3: a snippet alone cannot carry a summary, but can still be cited
  - D-2 scope: validation uses the evidence the model was actually sent
  - D-A: dates are grounded like quantities; recoverable vs integrity failures
  - U2: one bad section drops, it does not end the answer
  - F-4: an uncited comparison cell drops the table, not the answer
  - D-C: retrieval budget exhaustion and generation are separate failures
  - answer-cache versioning
  - passage sizing matches the prompt budget
"""
import asyncio
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from archer.app import app
from archer.ai_service import (
    AIError, AIService, ANSWER_CACHE_VERSION, RETRIEVAL_BUDGET,
    check_numbers, validate_block,
)
from archer.ai_provider import Provider
from archer.ai_tools import Toolbox
from archer.evidence import Evidence, add, preview, sent_ids, snippet_only

from tests.test_ai import ai_client, enable, events, prepare, search_reply  # noqa: F401


# --------------------------------------------------------------- evidence provenance

def test_snippets_are_unverified_and_read_pages_are_verified():
    bundle = []
    add(bundle, "A snippet", "https://example.com/a", "Snippet text about the topic.")
    add(bundle, "Structured data", "", '{"x": 1}', kind="tool")
    assert bundle[0].verified is False
    assert bundle[1].verified is True


def test_read_url_marks_the_page_verified():
    bundle = []
    item = add(bundle, "A page", "https://example.com/p", "Fetched page body.", kind="page")
    assert item.verified is True


def test_snippet_only_is_true_only_when_every_citation_is_unverified():
    excerpt = Evidence("S1", "s", "https://e.com/", "text")
    page = Evidence("S2", "p", "https://p.com/", "text", kind="page", verified=True)
    bundle = [excerpt, page]
    assert snippet_only(["S1"], bundle) is True              # sequence form
    assert snippet_only(["S1", "S2"], bundle) is False
    assert snippet_only([], bundle) is False
    assert snippet_only(["S9"], bundle) is False
    index = {e.id: e for e in bundle}
    assert snippet_only(["S1"], index) is True               # mapping form
    assert snippet_only(["S2"], index) is False


def test_sent_ids_limits_the_validation_universe():
    bundle = [Evidence("S1", "a", "https://a/", "t"), Evidence("S2", "b", "https://b/", "t")]
    assert sent_ids(bundle) == {"S1", "S2"}
    assert sent_ids(bundle[:1]) == {"S1"}


# --------------------------------------------------------------- U3 snippet-only rule

def test_summary_cannot_rest_only_on_unverified_snippets():
    evidence = [Evidence("S1", "Title", "https://example.com/", "The temperature is 24 degrees.")]
    raw = {"kind": "summary", "text": "The temperature is 24 degrees.", "citations": ["S1"]}
    with pytest.raises(AIError, match="unsupported"):
        validate_block(raw, evidence, first=True)


def test_summary_is_allowed_once_one_cited_page_was_read():
    evidence = [
        Evidence("S1", "Snippet", "https://example.com/s", "The temperature is 24 degrees."),
        Evidence("S2", "Page", "https://example.com/p", "The temperature is 24 degrees.",
                 kind="page", verified=True),
    ]
    raw = {"kind": "summary", "text": "The temperature is 24 degrees.", "citations": ["S1", "S2"]}
    assert validate_block(raw, evidence, first=True)["kind"] == "summary"


def test_snippet_remains_usable_as_cited_prose():
    """The restriction is on summaries only: a snippet can still support a paragraph."""
    evidence = [Evidence("S1", "Title", "https://example.com/", "The temperature is 24 degrees.")]
    raw = {"kind": "paragraph", "text": "The temperature is 24 degrees.", "citations": ["S1"]}
    assert validate_block(raw, evidence, first=True)["kind"] == "paragraph"


def test_snippet_only_summary_is_recoverable_not_fatal():
    """It must be droppable, so one such block cannot end an answer."""
    from archer.ai_service import _recoverable
    evidence = [Evidence("S1", "Title", "https://example.com/", "The temperature is 24 degrees.")]
    try:
        validate_block({"kind": "summary", "text": "The temperature is 24 degrees.",
                        "citations": ["S1"]}, evidence, first=True)
    except AIError as exc:
        assert _recoverable(exc) == "unsupported"
    else:
        raise AssertionError("expected a failure")


# --------------------------------------------------------------- evidence scope (D-2)

def test_citation_to_an_unsent_but_existing_source_is_rejected():
    """The regression the old code could not catch: the id existed in the bundle but
    was never serialised into the prompt."""
    evidence = [
        Evidence("S1", "Sent", "https://a/", "The temperature is 24 degrees."),
        Evidence("S2", "Never sent", "https://b/", "The temperature is 24 degrees."),
    ]
    raw = {"kind": "paragraph", "text": "The temperature is 24 degrees.", "citations": ["S2"]}
    with pytest.raises(AIError, match="didn't retrieve"):
        validate_block(raw, evidence, first=True, allowed={"S1"})


def test_allowed_defaults_to_the_whole_bundle():
    evidence = [Evidence("S1", "Title", "https://a/", "The temperature is 24 degrees.",
                         kind="page", verified=True)]
    raw = {"kind": "paragraph", "text": "The temperature is 24 degrees.", "citations": ["S1"]}
    assert validate_block(raw, evidence, first=True)["kind"] == "paragraph"


# --------------------------------------------------------------- D-A grounding

def test_dates_are_grounded_like_quantities():
    """A year is a quantity: it is matched by the same rule, so a completion date
    cannot be invented any more than a price can."""
    evidence = [Evidence("S1", "Guide", "https://a/", "Construction finished in 1887.")]
    with pytest.raises(AIError, match="quantity or date"):
        check_numbers("It opened in 1889.", ["S1"], evidence)
    check_numbers("It opened in 1887.", ["S1"], evidence)


def test_a_year_inside_the_cited_source_passes():
    evidence = [Evidence("S1", "Guide", "https://a/", "Built between 1887 and 1889.")]
    check_numbers("Completed in 1889.", ["S1"], evidence)


def test_a_quantity_absent_from_the_cited_source_is_still_rejected():
    evidence = [Evidence("S1", "Guide", "https://a/", "Usual price 9999 INR.")]
    with pytest.raises(AIError, match="quantity"):
        check_numbers("It costs 7000.", ["S1"], evidence)


def test_no_cited_evidence_means_no_grounding_check_runs():
    """An empty citation set is caught elsewhere (uncited factual claim); it must not
    raise a second, confusing error here."""
    check_numbers("Any number at all 1234.", [], [])


def test_inference_and_limitation_remain_citation_free():
    evidence = [Evidence("S1", "Title", "https://a/", "text", kind="page", verified=True)]
    assert validate_block({"kind": "inference", "text": "So probably worth it.",
                           "citations": []}, evidence, first=False)["kind"] == "inference"
    assert validate_block({"kind": "limitation", "text": "Could not verify pricing."},
                          evidence, first=False)["kind"] == "limitation"


# --------------------------------------------------------------- U2 / F-4 recoverability

def test_uncited_comparison_cell_is_a_layout_failure_not_a_stop():
    evidence = [Evidence("S1", "Guide", "https://a/", "text", kind="page", verified=True)]
    with pytest.raises(AIError) as caught:
        validate_block({"kind": "table", "columns": ["Criterion", "A"], "rows": [
            [{"text": "Score"}, {"text": "Good"}]]}, evidence, first=True)
    assert "layout" in str(caught.value)


def test_not_found_cells_still_pass_without_citation():
    evidence = [Evidence("S1", "Guide", "https://a/", "text", kind="page", verified=True)]
    assert validate_block({"kind": "table", "columns": ["Criterion", "A"], "rows": [
        [{"text": "Score"}, {"text": "Not verified"}]]}, evidence, first=True)["kind"] == "table"


def test_n_a_counts_as_a_declined_comparison_cell():
    evidence = [Evidence("S1", "Guide", "https://a/", "text", kind="page", verified=True)]
    assert validate_block({"kind": "table", "columns": ["Criterion", "A"], "rows": [
        [{"text": "Score"}, {"text": "N/A"}]]}, evidence, first=True)["kind"] == "table"


# --------------------------------------------------------------- stream trimming

def test_preview_trims_wire_text_but_keeps_identity_fields():
    item = Evidence("S1", "Title", "https://a/", "x" * 5000, kind="page", verified=True)
    data = preview(item)
    assert len(data["text"]) <= 250
    assert data["id"] == "S1" and data["url"] == "https://a/" and data["kind"] == "page"
    assert data["verified"] is True
    assert len(item.text) == 5000  # the retained evidence itself is untouched


def test_preview_does_not_truncate_short_text():
    assert preview(Evidence("S1", "t", "https://a/", "short"))["text"] == "short"


# --------------------------------------------------------------- D-C deadline split

def test_retrieval_budgets_leave_room_for_generation():
    """Generation runs outside the retrieval budget, so the budget must not be the
    whole wall-clock allowance a reader experiences."""
    assert RETRIEVAL_BUDGET["compact"] < 12.0
    assert RETRIEVAL_BUDGET["research"] < 75.0


def test_retrieval_exhaustion_is_reported_distinctly_from_provider_failure(ai_client):
    """A retrieval overrun must not read like the provider failed."""
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)

    class DeadReader:
        async def read(self, url):
            await asyncio.sleep(30)
            return "never"

    app.state.ai.reader = DeadReader()
    result = events(ai_client.post("/api/ai/runs", headers=headers,
                                   json={"snapshot": sid, "depth": "compact"}))
    assert result[-1]["event"] == "error"
    data = result[-1]["data"]
    assert data["retrieval_exhausted"] is True
    assert "before answering began" in data["text"]
    assert "timings" in data


def test_complete_event_carries_timings_and_evidence_counters(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    result = events(ai_client.post("/api/ai/runs", headers=headers,
                                   json={"snapshot": sid, "depth": "compact"}))
    assert result[-1]["event"] == "complete", result[-1]
    data = result[-1]["data"]
    for key in ("timings", "blocks_emitted", "blocks_omitted", "evidence_sent",
                "evidence_total", "evidence_verified"):
        assert key in data, key
    marks = data["timings"]
    assert marks["t_generate_start"] >= 0
    assert marks["t_generate_done"] >= marks["t_generate_start"]
    assert marks["t_first_block"] >= marks["t_generate_start"]
    assert data["evidence_sent"] <= data["evidence_total"]


def test_evidence_sent_never_exceeds_the_bundle(ai_client):
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    result = events(ai_client.post("/api/ai/runs", headers=headers,
                                   json={"snapshot": sid, "depth": "research"}))
    data = result[-1]["data"]
    assert data["evidence_sent"] <= data["evidence_total"]


# --------------------------------------------------------------- answer cache versioning

def test_answer_cache_key_carries_a_behaviour_version(ai_client):
    """An answer produced by an older validation path must not be replayed as current."""
    assert ANSWER_CACHE_VERSION >= 2
    app.state.ai.saved.clear()
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    events(ai_client.post("/api/ai/runs", headers=headers,
                          json={"snapshot": sid, "depth": "research"}))
    assert app.state.ai.saved, "expected a cached answer"
    for key in app.state.ai.saved:
        assert str(ANSWER_CACHE_VERSION) in key


def test_regenerate_bypasses_the_cached_answer(ai_client):
    app.state.ai.saved.clear()
    sid, headers = prepare(ai_client)
    enable(ai_client, headers)
    first = events(ai_client.post("/api/ai/runs", headers=headers,
                                  json={"snapshot": sid, "depth": "research"}))
    assert first[-1]["event"] == "complete"
    assert first[-1]["data"].get("saved") is not True
    assert first[-1]["data"]["model_calls"] >= 1

    # A different session and snapshot for the same question hits the saved-answer
    # cache: identical blocks, zero model calls.
    app.state.ai.sessions.clear()
    ai_client.cookies.clear()
    sid2, headers2 = prepare(ai_client)
    again = events(ai_client.post("/api/ai/runs", headers=headers2,
                                  json={"snapshot": sid2, "depth": "research"}))
    assert again[-1]["event"] == "complete"
    assert again[-1]["data"].get("saved") is True
    assert again[-1]["data"]["model_calls"] == 0

    # regenerate must bypass it and pay for a real call.
    fresh = events(ai_client.post("/api/ai/runs", headers=headers2,
                                  json={"snapshot": sid2, "depth": "research", "regenerate": True}))
    assert fresh[-1]["event"] == "complete"
    assert fresh[-1]["data"].get("saved") is not True
    assert fresh[-1]["data"]["model_calls"] >= 1


# --------------------------------------------------------------- passage sizing

def test_page_reads_size_passages_to_the_prompt_budget():
    """Selection must not build 4x1800 chars that the prompt then throws away."""
    long_text = (" ".join(f"sentence{i} about loan repayments" for i in range(400)))

    async def scenario():
        bundle = []
        item = add(bundle, "Page", "https://a/", "short", kind="excerpt")
        box = Toolbox(None, None, None, None, bundle, lambda *a: None)
        box.question = "how long does a loan take"
        box.depth = "research"
        box.text_budget = 900

        class Reader:
            async def read(self, url):
                return long_text
        box.reader = Reader()
        await box.read(["S1"])
        return item.text

    text = asyncio.run(scenario())
    assert len(text) <= 1100, len(text)
    assert text.strip()


def test_full_budget_allows_more_than_the_trimmed_budget():
    long_text = " ".join(f"sentence{i} about loan repayments" for i in range(400))

    async def scenario(budget):
        bundle = []
        item = add(bundle, "Page", "https://a/", "short", kind="excerpt")
        box = Toolbox(None, None, None, None, bundle, lambda *a: None)
        box.question = "how long does a loan take"
        box.depth = "research"
        box.text_budget = budget

        class Reader:
            async def read(self, url):
                return long_text
        box.reader = Reader()
        await box.read(["S1"])
        return item.text

    assert len(asyncio.run(scenario(2800))) > len(asyncio.run(scenario(900)))


def test_failed_read_keeps_the_excerpt_but_marks_it_unverified():
    async def scenario():
        bundle = []
        item = add(bundle, "Page", "https://a/", "snippet text", kind="excerpt")
        box = Toolbox(None, None, None, None, bundle, lambda *a: None)
        box.question = "q"
        box.depth = "compact"

        class Reader:
            async def read(self, url):
                raise httpx.ConnectError("down")
        box.reader = Reader()
        await box.read(["S1"])
        return item

    item = asyncio.run(scenario())
    assert item.text == "snippet text"          # the evidence survives
    assert item.verified is False               # but is not treated as inspected
    assert "could not be read" in item.limitation


# --------------------------------------------------------------- provider pooling

def test_provider_reuses_one_client_across_calls():
    provider = Provider(app.state.ai.settings)
    assert provider._shared is None

    async def scenario():
        async def work(client):
            return client
        await provider._with_client(30, work)
        first = provider._shared
        await provider._with_client(45, work)
        second = provider._shared
        await provider.aclose()
        return first, second

    first, second = asyncio.run(scenario())
    assert first is not None and first is second, "a new client was built per call"


def test_shared_client_has_no_default_timeout():
    """A shared client must not inherit the first call's timeout."""
    provider = Provider(app.state.ai.settings)
    client = provider._new_client()
    assert client.timeout.connect is None