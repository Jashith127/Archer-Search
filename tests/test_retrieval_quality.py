"""Regression tests for the retrieval-quality fixes found by diagnosing "riot vs valve".

Five fixes, one per test group, plus an end-to-end property suite for the case:

  A  a bare-entity comparison must not preempt the planner
  B  comparison questions can reach the entity-lookup tool
  C  forum hosts stay discussion sources and are never promoted to read pages
  D  DuckDuckGo's HTTP 202 bot-check is a rate limit, so cooldown engages
  E  navigation lists and link farms do not dominate extracted article text

Everything here is deterministic: no live search, no model call, no network.
"""
import asyncio
import re

import httpx
import pytest

from archer.ai_service import (
    AIService, direct_tools, identifiable, needs_planning, relevant_tools,
)
from archer.evidence import Evidence, add, discussion_host, from_view
from archer.ai_tools import Toolbox
from archer.fetcher import TextReader
from archer.searxng import RATE_LIMITED, Result, SearchResponse
from archer.sources import TTLCache
from archer import websearch
from archer.app import app

from tests.test_ai import ai_client, enable, events, prepare, search_reply  # noqa: F401


def clean(html: str) -> str:
    reader = TextReader()
    reader.feed(html)
    return " ".join(" ".join(reader.parts).split())


# ============================================================ A. planner reachability

@pytest.mark.parametrize("q,expected_plans", [
    ("riot vs valve", 1),
    ("dune vs blade runner", 1),
    ("apple vs pear", 1),
    ("rtx 4070 vs rx 7800 xt for 1440p gaming", 0),   # self-identifying: direct tools still win
])
def test_bare_entity_comparison_defers_to_the_planner(q, expected_plans):
    """A single-word side is an entity stem, not a query. Searching it verbatim
    returns whatever the engine associates with the word, and because direct_tools
    had claimed the question it also set planning = 0 — the planner became
    structurally unreachable."""
    direct = direct_tools(q)
    for _, args in direct:
        query = args.get("query", "")
        assert not (query.strip() and len(query.split()) == 1), (
            f"{q!r} issued a bare-word query {query!r}")
    # Mirror execute(): the planner is suppressed only when direct_tools produced work.
    planning = 0 if direct else (1 if needs_planning(q) else 0)
    assert planning == expected_plans, f"{q!r}: direct={direct} planning={planning}"


def test_identifiable_requires_a_model_number_or_two_words():
    assert identifiable("rtx 4070") is True
    assert identifiable("sony wh-1000xm5") is True
    assert identifiable("valve") is False
    assert identifiable("riot") is False


def test_self_identifying_comparison_still_uses_direct_tools():
    """Fix A must not cost us the fast path we already had."""
    direct = direct_tools("rtx 4070 vs rx 7800 xt for 1440p gaming")
    assert [a["query"] for _, a in direct] == ["rtx 4070 1440p gaming", "rx 7800 xt 1440p gaming"]


def test_loan_and_weather_fast_paths_are_untouched():
    assert direct_tools("emi for 10 lakh loan at 9% for 5 years or 4 years")[:1] == [
        ("loan_payment", {"principal": 1000000.0, "annual_rate": 9.0, "months": 60})]
    assert direct_tools("will it rain in Pune tomorrow") == [("weather", {"place": "Pune"})]


# ============================================================ B. entity lookup reachable

@pytest.mark.parametrize("q", [
    "riot vs valve",
    "riot games versus valve corporation",
    "compare diesel vs petrol engines",
])
def test_comparison_questions_can_reach_entity_lookup(q):
    """Ownership and corporate structure live in Wikidata. lookup_entity is the only
    route to them, and it was previously offered solely for media questions."""
    assert "lookup_entity" in relevant_tools(q)


def test_plain_questions_do_not_gain_entity_lookup():
    assert "lookup_entity" not in relevant_tools("why do cats knead")
    assert "lookup_entity" not in relevant_tools("latest python version")


# ============================================================ C. discussion classification

@pytest.mark.parametrize("host", [
    "vlr.gg", "www.vlr.gg", "steamcommunity.com", "reddit.com", "liquipedia.net",
    "resetera.com", "news.ycombinator.com", "discussions.apple.com",
])
def test_forum_hosts_are_discussion_sources(host):
    assert discussion_host(host) is True


@pytest.mark.parametrize("host", [
    "en.wikipedia.org", "reuters.com", "theverge.com", "riotgames.com",
    "valvesoftware.com", "searx.be", "essentiallysports.com",
])
def test_editorial_hosts_are_not_discussion(host):
    assert discussion_host(host) is False


def test_from_view_marks_a_forum_result_as_discussion():
    response = SearchResponse(results=[
        Result(title="Riot vs Valve", url="https://www.vlr.gg/187060/riot-vs-valve",
               host="vlr.gg", crumbs=[], content="forum thread", engines=["fixture"]),
        Result(title="Valve", url="https://en.wikipedia.org/wiki/Valve",
               host="en.wikipedia.org", crumbs=[], content="encyclopaedia", engines=["fixture"]),
    ])

    class V:
        q, tab, page = "riot vs valve", "all", 1
        hero = None
        response = None

    v = V()
    v.response = response
    bundle = from_view(v)
    assert bundle[0].kind == "discussion"
    assert bundle[1].kind == "excerpt"


def test_reading_a_forum_page_does_not_promote_it_to_a_verified_page():
    """Fetchable is not editorial. A forum thread that Archer managed to read must
    stay a discussion, or it inherits the +4 kind bonus in ranked_evidence and
    outranks real reporting."""

    async def scenario():
        bundle = []
        item = add(bundle, "Riot vs Valve", "https://www.vlr.gg/187060/riot-vs-valve",
                   "forum excerpt", kind="discussion")
        box = Toolbox(None, None, None, None, bundle, lambda *a: None)
        box.question, box.depth, box.text_budget = "riot vs valve", "research", 2800

        class Reader:
            async def read(self, url):
                return "Riot Games was founded in 2006. " * 30
        box.reader = Reader()
        await box.read(["S1"])
        return item

    item = asyncio.run(scenario())
    assert item.kind == "discussion"
    assert item.verified is False
    assert "opinion" in item.limitation


# ============================================================ D. provider fallback

def test_duckduckgo_bot_check_is_recognised_as_a_rate_limit():
    """DDG answers a bot check with 202, not 4xx. Reported as "HTTP 202" it matched
    no rate-limit pattern, so no cooldown engaged and every later search retried it."""
    assert RATE_LIMITED.search("DuckDuckGo is bot-checking this computer (HTTP 202)")
    assert RATE_LIMITED.search("SearXNG returned HTTP 429")


@pytest.mark.parametrize("status", [202])
def test_duckduckgo_202_raises_a_rate_limited_provider_error(status):
    class Client:
        async def post(self, *a, **k):
            return httpx.Response(status, text="challenge")

        async def get(self, *a, **k):
            return httpx.Response(status, text="challenge")

    async def scenario():
        try:
            await websearch.duckduckgo(Client(), "riot vs valve", "general", 1)
        except websearch.ProviderError as exc:
            return str(exc)
        return ""

    message = asyncio.run(scenario())
    assert "bot-check" in message
    assert RATE_LIMITED.search(message), "the message must trip the rate-limit tracker"


def test_a_normal_duckduckgo_page_still_parses():
    html = ('<div class="result results_links">'
            '<a class="result__a" href="https://example.com/a">Title A</a>'
            '<a class="result__snippet">Snippet A</a>'
            '<div class="clear"></div></div>')
    results = websearch.parse_ddg_html(html)
    assert [r.url for r in results] == ["https://example.com/a"]


# ============================================================ E. extraction quality

def test_sidebar_navigation_lists_are_dropped_but_the_article_is_kept():
    html = """<html><body>
    <div class="sidebar"><ul>
      <li><a href="/1">Riot vs Valve post</a></li>
      <li><a href="/2">Pickems Champions</a></li>
      <li><a href="/3">Writers Wanted</a></li>
      <li><a href="/4">Recent Discussion</a></li>
      <li><a href="/5">PRX and TH fans</a></li>
      <li><a href="/6">Tenz</a></li>
    </ul></div>
    <p>Riot Games was founded in 2006 and is majority owned by Tencent.</p>
    </body></html>"""
    out = clean(html)
    assert "majority owned by Tencent" in out
    for chrome in ("Writers Wanted", "PRX and TH fans", "Recent Discussion"):
        assert chrome not in out, f"navigation survived: {chrome}"


def test_article_lists_are_preserved():
    """Do not blindly remove lists: specifications and findings are evidence."""
    html = ("<html><body><p>Specifications:</p><ul>"
            "<li>Battery capacity is 5000 mAh</li>"
            "<li>Charging takes about 45 W</li></ul></body></html>")
    out = clean(html)
    assert "5000 mAh" in out and "45 W" in out


def test_link_farm_lists_are_dropped():
    html = ("<html><body><ul>"
            "<li><a href='/a'>Valve Corporation is an American video game developer</a></li>"
            "<li><a href='/b'>Riot Games publishes League of Legends</a></li>"
            "<li><a href='/c'>Steam is a digital distribution platform</a></li>"
            "<li><a href='/d'>Counter-Strike was developed by Valve</a></li></ul>"
            "<p>Both companies operate in the games market.</p></body></html>")
    out = clean(html)
    assert "Both companies operate in the games market." in out
    assert "American video game developer" not in out


def test_short_link_lists_are_kept():
    html = ("<html><body><ul><li><a href='/x'>See the docs</a></li>"
            "<li><a href='/y'>Read more</a></li></ul>"
            "<p>Valve is privately held.</p></body></html>")
    out = clean(html)
    assert "See the docs" in out and "privately held" in out


def test_extraction_survives_unclosed_tags():
    """Counters must stay balanced when a page omits optional end tags."""
    html = ("<html><body><div class='sidebar'><ul><li><a href='/1'>Chrome item one</a></li>"
            "<li><a href='/2'>Chrome item two</a></li><li><a href='/3'>Chrome three</a></li>"
            "<li><a href='/4'>Chrome four</a></li></ul>"
            "<p>Real sentence about Valve ownership.</p></body></html>")
    out = clean(html)
    assert "Real sentence about Valve ownership." in out


# ================================================== end-to-end: the reported case

def test_riot_vs_valve_issues_real_search_queries_not_bare_words(ai_client):
    """The end-to-end property for the reported failure: whatever queries the run
    issues must be specific. No assertion on prose — the model may word things
    differently each run."""
    sid, headers = prepare(ai_client, "riot vs valve")
    enable(ai_client, headers)

    issued = []

    class RecordingSearch:
        def __init__(self, inner):
            self.inner = inner

        async def search(self, q, category="general", page=1):
            issued.append((q, category))
            return await self.inner.search(q, category, page)

    original = app.state.ai.searx
    app.state.ai.searx = RecordingSearch(original)
    try:
        result = events(ai_client.post("/api/ai/runs", headers=headers,
                                       json={"snapshot": sid, "depth": "research"}))
    finally:
        app.state.ai.searx = original

    assert result[-1]["event"] in ("complete", "error"), result[-1]
    for query, _category in issued:
        assert len(query.strip()) > 1, f"degenerate query issued: {query!r}"


def test_comparison_answer_cites_no_known_forum_as_its_only_support(ai_client):
    """A forum opinion may be cited, but an answer whose every cited source is a
    forum cannot also claim verified fact. Provenance is enforced upstream by the
    snippet-only rule; this pins the classification that feeds it."""
    bundle = []
    add(bundle, "VLR thread", "https://www.vlr.gg/187060/riot-vs-valve", "opinion", kind="discussion")
    add(bundle, "Valve Corp", "https://en.wikipedia.org/wiki/Valve_Corporation",
        "Valve Corporation is privately held.", kind="page", verified=True)
    from archer.evidence import snippet_only
    assert snippet_only(["S1"], bundle) is True
    assert snippet_only(["S1", "S2"], bundle) is False


def test_forum_only_evidence_cannot_carry_a_summary(ai_client):
    """Drives the real run path with forum-only fixture evidence."""
    sid, headers = prepare(ai_client, "riot vs valve")
    enable(ai_client, headers)

    class ForumOnlySearch:
        async def search(self, q, category="general", page=1):
            return SearchResponse(provider="fixture", results=[
                Result(title=f"thread {n}", url=f"https://www.vlr.gg/t/{n}",
                       host="www.vlr.gg", crumbs=[], content="forum opinion",
                       engines=["fixture"])
                for n in range(4)])

    class ForumReader:
        async def read(self, url):
            return "Forum discussion text. " * 40

    original_search, original_reader = app.state.ai.searx, app.state.ai.reader
    app.state.ai.searx, app.state.ai.reader = ForumOnlySearch(), ForumReader()
    try:
        result = events(ai_client.post("/api/ai/runs", headers=headers,
                                       json={"snapshot": sid, "depth": "compact"}))
    finally:
        app.state.ai.searx, app.state.ai.reader = original_search, original_reader

    blocks = [e["data"] for e in result if e["event"] == "block"]
    for block in blocks:
        if block.get("kind") == "summary":
            notice_seen = any(e["event"] == "notice" and "unsupported" in e["data"]["text"]
                              for e in result)
            assert notice_seen, "a summary rested only on forum sources without a notice"