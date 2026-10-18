"""How a panel fact is drawn. The facts arrive as label + text (wiki.py's tiles); the label says what
kind of thing the value is, and each kind has its own form: a date is a year with how long ago it
was, a span of years is a dot rail, a measurement keeps its number large and its unit small, people
and places are links, and categories are chips. Anything else stays as text."""

import re
import zlib
from datetime import date

WHEN = {"Age", "Died", "Released", "Aired", "Published", "Formed", "Founded", "First released", "First held",
        "Last held", "Since", "Built", "Date", "Production"}
MEASURE = {"Height", "Runtime", "Pages", "Seasons", "Episodes", "Population", "Area", "Elevation", "Employees",
           "Revenue", "Net worth", "Molar mass", "Atomic mass", "Melting point", "Boiling point", "Density",
           "Units sold", "Box office", "Visitors / year", "Weight", "Launch price", "Top speed", "Units built",
           "Rounds", "Number"}
PEOPLE = {"Parents", "Spouse", "Director", "CEO", "Leader", "Author", "Artist", "Members", "Head of government",
          "Developer", "Composer", "Architect", "Created by", "Played by", "Champion"}
PLACE = {"Born in", "Headquarters", "Country", "Capital", "Location", "Observed in"}
TAGS = {"Genre", "Occupation", "Position", "Symptoms", "Specialty", "Treatment", "Label", "License", "Conservation",
        "Platforms", "Game modes", "Type", "Spread by", "Religion", "Class"}

_YEAR = re.compile(r"\b(\d{4})\b")
_SPAN = re.compile(r"^(\d{4})\s*[–-]\s*(\d{4}|present)$")
# A leading figure (optionally behind a currency sign) and whatever unit follows it.
_MEASURE = re.compile(r"^(\D{0,4}?[\d][\d.,]*(?:\s?(?:[KMBT]|L|Cr|L Cr|million|billion|trillion|lakh|crore)\b)?)\s*(.*)$")
RAIL_MAX = 30  # cells in a span rail; longer spans step in whole years per cell


def _ago(year: int, today: date) -> str:
    n = today.year - year
    return "This year" if n == 0 else f"{n} year{'s' if n != 1 else ''} ago" if n > 0 else f"In {-n} year{'s' if n != -1 else ''}"


def person(name: str) -> dict:
    """A name as a monogram: two initials on a hue picked from the name, like the site badges."""
    words = [w for w in re.split(r"[\s.\-]+", name) if w[:1].isalpha()]
    initials = (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper() if words else "?"
    return {"name": name, "initials": initials, "hue": zlib.crc32(name.encode()) % 360}


def shape(tile: dict, today: date | None = None) -> dict:
    """The tile plus `shape` and the parts that shape draws."""
    today = today or date.today()
    label, value, sub = tile.get("label", ""), (tile.get("value") or "").strip(), tile.get("sub", "")
    out = dict(tile, shape="text")
    if label in WHEN:
        if label == "Age" and (m := re.match(r"^(\d+)\s*(.*)$", value)):
            return dict(out, shape="measure", num=m[1], unit=m[2] or "years", sub=sub and f"Born {sub}")
        if m := _SPAN.match(value):
            start = int(m[1])
            end = today.year if m[2] == "present" else int(m[2])
            years = max(end - start, 0) + 1
            step = -(-years // RAIL_MAX)  # ceil: years per cell
            return dict(out, shape="span", start=m[1], end=m[2], ongoing=m[2] == "present",
                        length=f"{years} year{'s' if years != 1 else ''}", cells=-(-years // step))
        if m := _YEAR.search(value) or _YEAR.search(sub):
            year = int(m[1])
            detail = sub if value == m[1] else value if value != m[1] else ""
            if label == "Died":
                detail = " · ".join(x for x in (value, sub) if x)
            return dict(out, shape="when", year=m[1], detail=detail, ago=_ago(year, today))
        return out
    if label in MEASURE and (m := _MEASURE.match(value)):
        return dict(out, shape="measure", num=m[1].strip(), unit=m[2].strip())
    if label in PEOPLE:
        names = [n.strip() for n in value.split(",") if n.strip()]
        return dict(out, shape="people", people=[person(n) for n in names[:3]], more=max(len(names) - 3, 0))
    if label in PLACE:
        return dict(out, shape="place")
    if label in TAGS:
        tags = [t.strip() for t in value.split(",") if t.strip()]
        return dict(out, shape="tags", tags=[t[:1].upper() + t[1:] for t in tags])
    return out
