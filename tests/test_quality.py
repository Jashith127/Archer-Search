from datetime import datetime, timezone

from archer.quality import canonical, signals, wants_fresh
from archer.searxng import Result

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def res(url, engines=("a",), date=""):
    return Result(title="t", url=url, host="h", crumbs=[], content="", engines=list(engines), date=date)


def test_canonical_folds_noise():
    assert canonical("https://www.example.com/a/?utm=1") == canonical("https://example.com/a")
    assert canonical("https://example.com/a/amp/") == canonical("https://example.com/a")


def test_duplicate_demoted_and_agreement_rewarded():
    adj = signals("python", [res("https://x.com/a", ["a", "b"]), res("https://www.x.com/a/")], now=NOW)
    assert adj[0] > 0 > adj[1]


def test_freshness_only_when_asked():
    new = res("https://x.com/n", date="2026-10-18T00:00:00Z")
    old = res("https://x.com/o", date="2020-01-01T00:00:00Z")
    assert not wants_fresh("python list syntax")
    assert signals("python list syntax", [new, old], now=NOW) == [0.0, 0.0]
    fresh = signals("latest python news", [new, old], now=NOW)
    assert fresh[0] > fresh[1] > 0
