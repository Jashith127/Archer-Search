import pytest

from archer import utilities
from archer.query import classify
from archer.sources.wiki import _matches


@pytest.mark.parametrize("q, kind", [
    ("15% of 2400", "calc"),
    ("2*(3+4)", "calc"),
    ("sqrt(16) + 1", "calc"),
    ("5 feet in cm", "unit"),
    ("100 f to c", "unit"),
    ("#1a73e8", "color"),
    ("1760000000", "timestamp"),
    ("flip a coin", "random"),
    ("weather bengaluru", "weather"),
    ("weather", "weather"),
    ("bengaluru weather today", "weather"),
    ("time in london", "time"),
    ("define serendipity", "define"),
    ("serendipity meaning", "define"),
    ("how to tie a tie", "howto"),
    ("why do cats knead", "question"),
    ("zendaya", "general"),
    ("100 usd to inr", "general"),   # currency comes from SearXNG's answer
    ("iphone 15 - 128gb", "general"),
    ("covid 19", "general"),
    ("solve 2x+3=11", "solve"),
    ("x^2-5x+6=0", "solve"),
    ("y=x^2", "graph"),
    ("graph sin(x)", "graph"),
    ("track EE123456789IN", "track"),
    ("1Z999AA10123456784", "track"),
    ("9876543210", "general"),          # a phone number, not a timestamp
    ("timer 10 min", "timer"),
    ("stopwatch", "timer"),
    ("metronome 120", "metronome"),
    ("emi calculator", "finance"),
    ("bmi calculator", "finance"),
    ("bitcoin price", "crypto"),
    ("btc to usd", "crypto"),
    ("tron", "general"),                # the film, not the coin
    ("sunset mumbai", "sun"),
    ("thank you in kannada", "translate"),
    ("movies in hindi", "general"),
    ("cafes in indiranagar", "places"),
    ("atm near me", "places"),
])
def test_classify(q, kind):
    assert classify(q).kind == kind


def test_new_kind_payloads():
    assert classify("solve 2x+3=11").data["roots"] == ["4"]
    assert classify("x^2-5x+6=0").data["roots"] == ["2", "3"]
    assert classify("x^2-5x+6=0").data["steps"][0] == "Rearrange to x² − 5x + 6 = 0"
    assert classify("solve 2x+3=11").data["steps"][0] == "Rearrange to 2x − 8 = 0"
    assert classify("timer 10 min").data == {"mode": "timer", "seconds": 600}
    assert classify("btc to usd").data == {"coin": "bitcoin", "fiat": "usd"}
    assert classify("Thank You in kannada").data["text"] == "Thank You"
    places = classify("atm near me").data
    assert places["value"] == "atm" and places["near_me"]
    assert classify("track EE123456789IN").data["carrier"] == "India Post"
    assert "M" in classify("y=x^2").data["path"]


def test_entity_candidates():
    assert classify("zendaya").entity_candidate == "zendaya"
    assert classify("who is fitgirl").entity_candidate == "fitgirl"
    assert classify("why do cats knead").entity_candidate is None
    assert classify("how to tie a tie").entity_candidate is None


def test_calc_values():
    assert utilities.calculate("15% of 2400")["value"] == "360"
    assert utilities.calculate("2^10")["value"] == "1,024"
    assert utilities.calculate("1/0") is None
    assert utilities.calculate("9**99999") is None
    assert utilities.calculate("__import__('os')") is None


def test_unit_values():
    assert utilities.convert_units("5 feet in cm")["value"] == "152.4"
    assert utilities.convert_units("100 f to c")["value"] == "37.777778"
    assert utilities.convert_units("5 kg in cm") is None


def test_currency_parse():
    c = utilities.parse_currency_answer("100.0 USD = 9500.44928252 INR (1 United States dollar : 95.0044928252 Indian rupee)")
    assert c["value"] == "9,500.45" and c["to_name"] == "Indian rupee" and c["rate"] == pytest.approx(95.0045, 1e-4)


def test_current_role_prefers_latest_start():
    from archer.sources.wiki import _current_ids

    def job(qid, start=None, end=None):
        q = {}
        if start:
            q["P580"] = [{"snaktype": "value", "datavalue": {"value": {"time": start}}}]
        if end:
            q["P582"] = [{"snaktype": "value", "datavalue": {"value": {"time": end}}}]
        return {"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": qid}}}, "rank": "normal", "qualifiers": q}

    entity = {"claims": {"P108": [job("Q_sun"), job("Q_microsoft", start="+1992-01-01T00:00:00Z"),
                                  job("Q_old", start="+1988-01-01T00:00:00Z", end="+1992-01-01T00:00:00Z")]}}
    assert _current_ids(entity, "P108", 1) == ["Q_microsoft"]


@pytest.mark.parametrize("q, title, ok", [
    ("dune part two", "Dune: Part Two", True),
    ("the bear tv series", "The Bear (TV series)", True),
    ("fitgirl", "FitGirl Repacks", True),
    ("best laptop 2026", "Laptop", False),
    ("zendaya", "Zendaya", True),
])
def test_entity_title_match(q, title, ok):
    assert _matches(q, title) is ok
