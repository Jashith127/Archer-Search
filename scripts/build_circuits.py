"""Build src/archer/sources/circuits.json from bacinger/f1-circuits (MIT, (c) Tomislav Bacinger).

    curl -sL https://raw.githubusercontent.com/bacinger/f1-circuits/master/f1-circuits.geojson -o f1-circuits.geojson
    uv run python scripts/build_circuits.py f1-circuits.geojson

Each circuit is one LineString drawn in the racing direction from the start/finish line. It is projected
to metres around its own centre (so it isn't stretched east-west), kept north-up, and scaled so its longer
side is 1000 units. The page draws it as a dot matrix, so a point every few metres is plenty.
"""

import json
import math
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "archer" / "sources" / "circuits.json"
EARTH = 6371000.0


def project(coords: list[list[float]]) -> tuple[list[tuple[float, float]], float, float]:
    lat0 = math.radians(sum(c[1] for c in coords) / len(coords))
    pts = [(math.radians(lon) * math.cos(lat0) * EARTH, math.radians(lat) * EARTH) for lon, lat, *_ in coords]
    return pts, math.degrees(lat0), sum(c[0] for c in coords) / len(coords)


def main(src: str) -> None:
    data = json.loads(Path(src).read_bytes().decode("utf-8", "replace"))
    out = []
    for feature in data["features"]:
        props, geom = feature["properties"], feature["geometry"]
        if geom.get("type") != "LineString" or len(geom["coordinates"]) < 10:
            continue
        pts, lat, lon = project(geom["coordinates"])
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        k = 1000 / span
        flat = []
        for x, y in pts:
            flat += [round((x - min(xs)) * k), round((max(ys) - y) * k)]   # SVG y runs down
        drawn = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        out.append({
            "id": props.get("id", ""), "name": props.get("Name", ""), "location": props.get("Location", ""),
            "length": int(props.get("length") or round(drawn)), "opened": props.get("opened") or 0,
            "first_gp": props.get("firstgp") or 0, "altitude": props.get("altitude"),
            "lat": round(lat, 5), "lon": round(lon, 5),
            "w": round((max(xs) - min(xs)) * k), "h": round((max(ys) - min(ys)) * k), "points": flat,
        })
    OUT.write_text(json.dumps({"source": "bacinger/f1-circuits (MIT)", "circuits": out}, separators=(",", ":")),
                   encoding="utf-8")
    print(f"{len(out)} circuits -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
