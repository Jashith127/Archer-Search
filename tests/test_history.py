"""Search history and personalization: storage, ranking, recall and the HTTP surface. All local, all mocked."""
import asyncio
import time
import zlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from archer import views
from archer.app import app
from archer.config import settings
from archer.embed import LEXICAL, words
from archer.history import History
from archer.personalize import Personalizer, closeness, site_pref
from archer.searxng import Result, SearXNG

from test_app import BASE, fake, offline, searx_handler


def result(url, title, content=""):
    host = url.split("/")[2].removeprefix("www.")
    return Result(title=title, url=url, host=host, crumbs=[], content=content, engines=["duckduckgo"])


class Concepts:
    """A stand-in embedding model: each concept is an axis, so 'meaning' is fully predictable. Words
    outside the concepts add a little hashed noise, so unrelated text spreads out as with real models."""
    name = "concepts"
    ready = True
    AXES = [{"monitor", "display", "screen", "resolution", "1440p", "pixel", "density"},
            {"laptop", "rtx", "gpu", "4050", "gaming"},
            {"pasta", "recipe", "carbonara", "cook"}]

    def active(self):
        return self

    async def encode(self, texts):
        out = []
        for text in texts:
            ws = set(words(text))
            noise = np.random.default_rng(zlib.crc32(" ".join(sorted(ws)).encode())).normal(size=256)
            noise /= np.linalg.norm(noise)
            concept = np.array([3.0 * len(ws & axis) for axis in self.AXES], dtype=np.float32)
            vec = np.concatenate([concept, noise * 0.6]).astype(np.float32)
            out.append(vec / np.linalg.norm(vec))
        return self.name, np.stack(out)


@pytest.fixture
def store(tmp_path):
    h = History(tmp_path / "h.sqlite3")
    yield h
    h.close()


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ storage

def test_repeat_search_within_window_is_one_entry(store):
    a = store.record_search("RTX 4050 monitor", now=1000)
    b = store.record_search("rtx  4050 MONITOR", now=1500)
    c = store.record_search("rtx 4050 monitor", now=1000 + 4000)
    assert a == b != c
    assert store.search(c)["count"] == 2  # the count carries over for suggestion ranking
    assert store.record_search("rtx 4050 monitor", tab="images", now=5100) not in (a, c)


def test_recent_prefix_and_frecency(store):
    now = time.time()
    store.record_search("python asyncio tutorial", now=now - 86400 * 20)
    for i in range(4):
        store.record_search("python dataclasses", now=now - 86400 * 2 - i * 4000)
    store.record_search("best pasta recipe", now=now - 60)
    assert store.recent(3, prefix="pyth") == ["python dataclasses", "python asyncio tutorial"]
    assert store.recent(3, prefix="dataclass") == ["python dataclasses"]  # a later word matches too
    assert store.recent(1) == ["best pasta recipe"]
    assert store.recent(5, prefix="100%_") == []  # LIKE wildcards are literal


def test_clicks_belong_to_their_search_and_dedupe(store):
    sid = store.record_search("dune movie")
    store.record_click(sid, "https://www.imdb.com/title/tt1", "Dune (2021)")
    store.record_click(sid, "https://www.imdb.com/title/tt1", "Dune (2021)")
    store.record_click(9999, "https://example.com/x", "orphan")  # unknown search id is kept unlinked
    entry = store.entries()[0]
    assert [c["host"] for c in entry["clicks"]] == ["imdb.com"]
    assert store.stats()["clicks"] == 2


def test_delete_clear_and_prune(store):
    old = store.record_search("old thing", now=time.time() - 86400 * 400)
    sid = store.record_search("new thing")
    store.record_click(sid, "https://a.com/1", "A")
    store.set_site("example.com", "raise")
    store.remember_meaning("apple", "Apple Inc.")
    store.prune(365)
    assert store.search(old) is None and store.search(sid)
    store.clear(since=time.time() - 3600)
    assert store.stats()["searches"] == 0 and store.stats()["clicks"] == 0
    assert store.meaning("apple") == ""
    assert store.sites() == {"example.com": 1}  # preferences are settings, not history
    sid = store.record_search("again")
    assert store.delete_search(sid) and store.entries() == []
    assert store.delete_query("nothing") == 0


def test_site_pref_covers_subdomains():
    prefs = {"example.com": 1, "pinterest.com": -2}
    assert site_pref("news.example.com", prefs) == 1
    assert site_pref("in.pinterest.com", prefs) == -2
    assert site_pref("example.org", prefs) == 0


def test_lexical_embedder_matches_variants():
    a, b, c = LEXICAL.encode(["gaming monitors 1440p", "1440p gaming monitor", "carbonara recipe"])
    assert a @ b > 0.8 > 0.2 > a @ c


# ------------------------------------------------------------ personalization

def test_blocked_sites_disappear_and_raised_move_up(store):
    store.set_site("pinterest.com", "block")
    store.set_site("rtings.com", "raise")
    results = [result("https://www.pinterest.com/p/1", "Monitor ideas"), result("https://a.com/1", "Monitor guide"),
               result("https://b.com/1", "Monitor list"), result("https://www.rtings.com/monitor", "Monitor reviews")]
    out = run(Personalizer(store, Concepts()).personalize("best monitor", results))
    hosts = [r.host for r in out.results]
    assert "pinterest.com" not in hosts and out.hidden == ["pinterest.com"]
    assert hosts.index("rtings.com") < 2
    assert next(r for r in out.results if r.host == "rtings.com").personal == "raised"
    assert results[0].host == "pinterest.com" and not results[3].personal  # cached originals untouched


def test_sites_opened_after_similar_searches_rise(store):
    for q in ("27 inch monitor pixel density", "best display resolution", "1440p screen"):
        sid = store.record_search(q)
        store.record_click(sid, f"https://www.rtings.com/{q[:5]}", "RTINGS review")
    results = [result(f"https://site{i}.com/x", "Monitor buying guide", "monitor display") for i in range(5)]
    results.append(result("https://www.rtings.com/monitor/best", "Best monitors", "monitor display"))
    out = run(Personalizer(store, Concepts()).personalize("which monitor resolution", results))
    ranked = [r.host for r in out.results]
    assert ranked.index("rtings.com") <= 2  # from last place
    assert next(r for r in out.results if r.host == "rtings.com").personal == "often"
    # An unrelated search gets no such lift.
    other = run(Personalizer(store, Concepts()).personalize("pasta carbonara recipe", results))
    assert other.results[0].host == "site0.com"


def test_related_searches_and_recall_by_meaning(store):
    sid = store.record_search("RTX 4050 laptop 1440p gaming monitor")
    store.record_click(sid, "https://forum.example.com/t/1", "Can a 4050 drive a 1440p screen?", "gpu monitor")
    store.record_search("best pasta recipe")
    out = run(Personalizer(store, Concepts()).personalize("will my rtx laptop handle a high resolution screen",
                                                          [result("https://x.com/1", "Laptop screens")]))
    assert [r["q"] for r in out.related] == ["RTX 4050 laptop 1440p gaming monitor"]
    assert [p["url"] for p in out.recall] == ["https://forum.example.com/t/1"]
    assert out.recall[0]["q"] == "RTX 4050 laptop 1440p gaming monitor"


def test_visited_results_are_tagged(store):
    sid = store.record_search("dune")
    store.record_click(sid, "https://en.wikipedia.org/wiki/Dune_(novel)", "Dune (novel)")
    out = run(Personalizer(store, Concepts()).personalize("dune", [result("https://en.wikipedia.org/wiki/Dune_(novel)", "Dune")]))
    assert out.results[0].visited > 0
    assert out.recall == []  # already on the page


def test_history_search_by_meaning_and_words(store):
    a = store.record_search("27 inch monitor pixel density")
    b = store.record_search("carbonara recipe")
    c = store.record_search("unrelated words here")
    store.record_click(c, "https://cook.example.com/1", "How to cook pasta")
    p = Personalizer(store, Concepts())
    assert run(p.search_history("display for my screen")) == [a]
    assert set(run(p.search_history("pasta"))) == {b, c}  # by meaning, and by an opened page's title
    assert run(p.search_history("unrelated")) == [c]
    # The history page shows how each matched: the words, the meaning, or a page opened from it.
    how = {m["id"]: m["how"] for m in run(p.match_history("pasta"))}
    assert how == {b: "meaning", c: "page"}
    assert run(p.match_history("unrelated"))[0] | {"dots": 5} == {"id": c, "dots": 5, "how": "words", "page": ""}


def test_closeness_dots():
    assert closeness(1.0, 0.5) == 5                  # same words
    assert closeness(0.5, 0.5) == 1                  # just clears the cutoff
    assert 1 < closeness(0.75, 0.5) < 5
    assert closeness(0.99, 0.5) == 4                 # close in meaning never reads as the same words


def test_topics_group_history(store):
    for q in ("1440p monitor", "monitor pixel density", "display resolution", "pasta recipe", "carbonara cook"):
        store.record_search(q)
    topics = run(Personalizer(store, Concepts()).topics())
    sizes = sorted(t["size"] for t in topics)
    assert sizes == [2, 3]


# ------------------------------------------------------------------ HTTP

@pytest.fixture
def client():
    with TestClient(app, base_url=BASE) as c:
        app.state.searxng = fake(searx_handler)
        app.state.searx = SearXNG(app.state.searxng)
        app.state.ext = fake(offline)
        yield c


def csrf(client):
    html = client.get("/history").text
    return html.split('data-csrf="')[1].split('"')[0]


SAME = {"Sec-Fetch-Site": "same-origin", "Origin": BASE}


def test_search_is_recorded_once_and_prefetch_is_not(client):
    client.get("/search", params={"q": "zendaya"})
    client.get("/search", params={"q": "zendaya"})
    client.get("/search", params={"q": "speculative"}, headers={"Sec-Purpose": "prefetch"})
    client.get("/search", params={"q": "zendaya", "p": 2})
    stats = app.state.history.stats()
    assert stats["searches"] == 1
    assert 'data-history="' in client.get("/search", params={"q": "zendaya"}).text


def test_paused_history_records_nothing(client, monkeypatch):
    monkeypatch.setattr(settings, "history", False)
    html = client.get("/search", params={"q": "zendaya"}).text
    assert app.state.history.stats()["searches"] == 0 and "data-history=" not in html
    assert client.post("/api/history/click", json={"url": "https://a.com/x"}, headers=SAME).json() == {"recorded": False}


def test_click_needs_same_origin(client):
    client.get("/search", params={"q": "zendaya"})
    sid = app.state.history.entries()[0]["id"]
    body = {"search": sid, "url": "https://en.wikipedia.org/wiki/Zendaya", "title": "Zendaya", "position": 0}
    assert client.post("/api/history/click", json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/history/click", json={**body, "url": "javascript:alert(1)"}, headers=SAME).status_code == 422
    assert client.post("/api/history/click", json=body, headers=SAME).json() == {"recorded": True}
    assert app.state.history.entries()[0]["clicks"][0]["host"] == "en.wikipedia.org"
    html = client.get("/search", params={"q": "zendaya"}).text
    assert "Visited" in html


def test_suggest_puts_past_searches_first_in_opensearch_format(client):
    client.get("/search", params={"q": "zendaya euphoria"})
    data = client.get("/suggest", params={"q": "zen"}).json()
    assert data[0] == "zen" and data[1][0] == "zendaya euphoria" and "zendaya movies" in data[1]
    recent = client.get("/api/history/recent", params={"q": "zen"}, headers={"Sec-Fetch-Site": "same-origin"}).json()
    assert recent == {"items": ["zendaya euphoria"]}
    assert client.get("/api/history/recent").status_code == 403


def test_history_page_and_guarded_changes(client):
    client.get("/search", params={"q": "zendaya"})
    html = client.get("/history").text
    assert "zendaya" in html and "Today" in html
    sid = app.state.history.entries()[0]["id"]
    assert client.post("/api/history/delete", json={"searches": [sid]}, headers=SAME).status_code == 403  # no CSRF
    token = csrf(client)
    headers = {**SAME, "X-Archer-CSRF": token}
    assert client.post("/api/history/site", json={"host": "reddit.com", "pref": "block"}, headers=headers).status_code == 200
    assert "Discussions" not in client.get("/search", params={"q": "zendaya"}).text  # reddit threads hidden
    assert client.post("/api/history/delete", json={"searches": [sid]}, headers=headers).json()["removed"] == 1
    assert client.post("/api/history/clear", json={"range": "all"}, headers=headers).json()["stats"]["searches"] == 0
    assert client.post("/api/history/forget", json={"q": "zendaya"}, headers=headers).status_code == 200


def test_history_page_search(client):
    client.get("/search", params={"q": "zendaya"})
    client.get("/search", params={"q": "weather tomorrow"})
    html = client.get("/history", params={"q": "zendaya"}).text
    assert "Searches like" in html and "pxmeter" in html and "/search?q=zendaya" in html and "weather+tomorrow" not in html


def test_chosen_meaning_is_remembered(client, monkeypatch):
    seen = []
    real = views.build

    async def spy(searx, ext, q, tab, page, about=""):
        seen.append(about)
        return await real(searx, ext, q, tab, page, about)

    monkeypatch.setattr(views, "build", spy)
    client.get("/search", params={"q": "apple", "about": "Apple Inc."})
    client.get("/search", params={"q": "Apple"})
    assert seen == ["Apple Inc.", "Apple Inc."]
    monkeypatch.setattr(settings, "personalize", False)
    client.get("/search", params={"q": "apple"})
    assert seen[-1] == ""


def test_home_lists_recent_searches(client):
    client.get("/search", params={"q": "zendaya"})
    html = client.get("/").text
    assert "home-recent" in html and "zendaya" in html


def test_static_model_loads_from_the_data_folder(tmp_path):
    """The Model2Vec path, offline: a tiny model saved where the download would put it."""
    from model2vec import StaticModel
    from tokenizers import Tokenizer, models, pre_tokenizers
    from archer.embed import StaticEmbedder
    tok = Tokenizer(models.WordLevel({"[UNK]": 0, "[PAD]": 1, "monitor": 2, "display": 3, "pasta": 4}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    vectors = np.array([[0, 0, 0], [0, 0, 0], [1, 0, 0], [0.9, 0.1, 0], [0, 0, 1]], dtype=np.float32)
    StaticModel(vectors=vectors, tokenizer=tok, normalize=True).save_pretrained(tmp_path / "potion-base-8M")
    model = StaticEmbedder("potion-base-8M", tmp_path)
    assert model.load() and model.ready
    a, b, c = model.encode(["monitor", "display", "pasta"])
    assert a @ b > 0.9 and abs(a @ c) < 0.01


def test_related_cutoff_is_calibrated_per_model():
    """A model that spreads unrelated text widely gets a higher cutoff than one that keeps it near 0."""
    from archer.personalize import calibrated
    rng = np.random.default_rng(0)
    tight = rng.normal(size=(20, 256)).astype(np.float32)
    tight /= np.linalg.norm(tight, axis=1, keepdims=True)
    loose = tight + 1.2  # everything shares a large common direction, as some models do
    loose /= np.linalg.norm(loose, axis=1, keepdims=True)
    assert 0.3 <= calibrated(tight) < calibrated(loose) <= 0.75  # raised with the model's baseline
    assert calibrated(loose) - calibrated(tight) > 0.1


# ------------------------------------------------ rules from the history evaluation

def test_calculator_searches_and_fragments_never_match(store):
    for q in ("18,500 - 3500", "680 of 15000 in percentage", "z", "50000 mb in gb"):
        store.record_search(q)
    p = Personalizer(store, Concepts())
    assert run(p.personalize("56000 x0.3", [])).related == []      # a sum is not an interest
    assert run(p.personalize("zendaya", [])).related == []          # "z" was a half-typed entry
    assert run(p.topics()) == []


def test_spacing_and_typo_variants_match(store):
    store.record_search("light house audit")
    store.record_search("pixel art generator")
    p = Personalizer(store, Concepts())
    assert [r["q"] for r in run(p.personalize("lighthouse audit", [])).related] == ["light house audit"]
    assert [r["q"] for r in run(p.personalize("pixelart generater", [])).related] == ["pixel art generator"]


def test_recall_shows_one_page_per_site_and_respects_model_numbers(store):
    sid = store.record_search("rtx 4050 laptop monitor")
    for path in ("a", "b", "c"):
        store.record_click(sid, f"https://www.rtings.com/{path}", "RTX 4050 laptop monitor guide")
    sid = store.record_search("1440p monitor for gaming laptop")
    store.record_click(sid, "https://forum.example.com/t", "1440p monitor for a gaming laptop")
    p = Personalizer(store, Concepts())
    recall = run(p.personalize("rtx 4050 gaming laptop screen", [])).recall
    assert len({r["host"] for r in recall}) == len(recall) >= 2
    # Same words, a different GPU: pages about the 4050 aren't recalled for a 4060.
    assert all("rtings" not in r["host"] for r in run(p.personalize("rtx 4060 gaming laptop screen", [])).recall)


def test_results_you_opened_before_link_their_searches(store):
    sid = store.record_search("github design system")
    store.record_click(sid, "https://primer.style/", "Primer")
    p = Personalizer(store, Concepts())
    out = run(p.personalize("primer", [result("https://primer.style/", "Primer")]))
    assert [r["q"] for r in out.related] == ["github design system"]  # no shared words; your click links them


def test_half_typed_searches_and_initials_find_the_full_search(store):
    store.record_search("ascii art generator")
    store.record_search("delhi watch company")
    p = Personalizer(store, Concepts())
    assert [r["q"] for r in run(p.personalize("ascii art gen", [])).related] == ["ascii art generator"]
    assert [r["q"] for r in run(p.personalize("dwc", [])).related] == ["delhi watch company"]


def test_a_search_naming_a_site_you_use_recalls_it_first(store):
    sid = store.record_search("ui references for apps")
    store.record_click(sid, "https://mobbin.com/", "Mobbin: UI and UX design references")
    store.record_click(sid, "https://mobbin.com/pricing", "Pricing")
    p = Personalizer(store, Concepts())
    recall = run(p.personalize("mobbin login", [])).recall
    assert recall and recall[0]["url"] == "https://mobbin.com/"


def test_recall_skips_another_product_of_the_same_brand(store):
    sid = store.record_search("kia sonet price")
    store.record_click(sid, "https://cars.example.com/kia-sonet", "Kia Sonet price in India")
    emb = Concepts()
    emb.known_word = lambda w: w in {"kia", "price", "india", "car", "review"}  # model names are not words
    p = Personalizer(store, emb)
    emb.active = lambda: emb
    assert run(p.personalize("kia seltos price", [])).recall == []
    assert run(p.personalize("kia sonet price", [])).recall  # the same product still is


def test_searches_around_a_search_shape_its_meaning(store):
    t = time.time() - 3600
    store.record_search("dune", now=t)  # no concept words of its own...
    store.record_search("1440p monitor pixel density", now=t + 60)  # ...but searched among monitor searches
    store.record_search("display resolution", now=t + 120)
    p = Personalizer(store, Concepts())
    from archer import personalize as P
    with_context = run(p._past_scores("screen resolution"))[4]["dune"]
    P.RULES["context"] = False
    try:
        without = run(Personalizer(store, Concepts())._past_scores("screen resolution"))[4]["dune"]
    finally:
        P.RULES["context"] = True
    assert with_context > without + 0.2


def test_not_related_is_remembered_both_ways(client):
    store = app.state.history if hasattr(app.state, "history") else None
    client.get("/search", params={"q": "zendaya euphoria"})
    client.get("/search", params={"q": "zendaya movies"})
    token = csrf(client)
    headers = {**SAME, "X-Archer-CSRF": token}
    assert client.post("/api/history/unrelated", json={"q": "zendaya movies", "other": "zendaya euphoria"}).status_code == 403
    assert client.post("/api/history/unrelated", json={"q": "zendaya movies", "other": "zendaya euphoria"}, headers=headers).json() == {"ok": True}
    assert "zendaya movies" in app.state.history.unrelated_to("zendaya euphoria")
    html = client.get("/search", params={"q": "zendaya movies"}).text
    assert 'data-unrelate="zendaya euphoria"' not in html
