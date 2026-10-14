"""Weather and local time from Open-Meteo (free, no key)."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from archer.sources import TTLCache

_geo_cache = TTLCache(ttl=7 * 86400)
_wx_cache = TTLCache(ttl=600)

# WMO weather codes -> (label, icon name)
WMO = {
    0: ("Clear", "sun"), 1: ("Mostly clear", "sun"), 2: ("Partly cloudy", "cloud-sun"),
    3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "drizzle"), 57: ("Freezing drizzle", "drizzle"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Light showers", "rain"), 81: ("Showers", "rain"), 82: ("Violent showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Heavy snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm, hail", "storm"), 99: ("Thunderstorm, hail", "storm"),
}


async def geocode(client: httpx.AsyncClient, place: str) -> dict | None:
    key = place.lower()
    if (hit := _geo_cache.get(key)) is not None:
        return hit or None
    resp = await client.get("https://geocoding-api.open-meteo.com/v1/search",
                            params={"name": place, "count": 1, "language": "en", "format": "json"})
    resp.raise_for_status()
    results = resp.json().get("results") or []
    loc = results[0] if results else None
    # Reject hamlets that happen to share a common word ("screen time", "body temperature").
    if loc and not (loc.get("population", 0) >= 5000 or str(loc.get("feature_code", "")).startswith(("PCL", "ADM", "PPLC", "PPLA"))):
        loc = None
    _geo_cache.put(key, loc or {})
    return loc


def place_label(loc: dict) -> str:
    parts = [loc.get("name"), loc.get("admin1"), loc.get("country")]
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return ", ".join(seen)


async def forecast(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    key = (round(lat, 2), round(lon, 2))
    if hit := _wx_cache.get(key):
        return hit
    resp = await client.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,"
                   "weather_code,wind_speed_10m,is_day",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
    })
    resp.raise_for_status()
    data = resp.json()
    cur, hourly, daily = data["current"], data["hourly"], data["daily"]

    now_idx = next((i for i, t in enumerate(hourly["time"]) if t >= cur["time"][:13]), 0)
    hours = []
    for i in range(now_idx, min(now_idx + 24, len(hourly["time"]))):
        hours.append({"hour": hourly["time"][i][11:13], "temp": round(hourly["temperature_2m"][i]),
                      "rain": hourly["precipitation_probability"][i] or 0})
    days = []
    for i, day in enumerate(daily["time"]):
        label, icon = WMO.get(daily["weather_code"][i], ("", "cloud"))
        days.append({"name": "Today" if i == 0 else datetime.fromisoformat(day).strftime("%a"),
                     "max": round(daily["temperature_2m_max"][i]), "min": round(daily["temperature_2m_min"][i]),
                     "rain": daily["precipitation_probability_max"][i] or 0, "label": label, "icon": icon})
    label, icon = WMO.get(cur["weather_code"], ("", "cloud"))
    if icon == "sun" and not cur.get("is_day", 1):
        icon = "moon"
    out = {
        "temp": round(cur["temperature_2m"]), "feels": round(cur["apparent_temperature"]),
        "humidity": cur["relative_humidity_2m"], "wind": round(cur["wind_speed_10m"]),
        "precip": cur["precipitation"], "label": label, "icon": icon,
        "sunrise": daily["sunrise"][0][11:16], "sunset": daily["sunset"][0][11:16],
        "hours": hours, "days": days, "timezone": data.get("timezone", ""),
        "updated": cur["time"][11:16],
    }
    return _wx_cache.put(key, out)


async def sun(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    key = ("sun", round(lat, 2), round(lon, 2))
    if hit := _wx_cache.get(key):
        return hit
    resp = await client.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
        "daily": "sunrise,sunset,daylight_duration"})
    resp.raise_for_status()
    data = resp.json()
    daily = data["daily"]
    days = []
    for i, day in enumerate(daily["time"]):
        hours, rem = divmod(int(daily["daylight_duration"][i]), 3600)
        days.append({"name": "Today" if i == 0 else "Tomorrow", "date": datetime.fromisoformat(day).strftime("%a, %d %b"),
                     "sunrise": daily["sunrise"][i][11:16], "sunset": daily["sunset"][i][11:16],
                     "daylight": f"{hours} h {rem // 60} min"})
    return _wx_cache.put(key, {"days": days, "timezone": data.get("timezone", "")})


def local_time(tz_name: str) -> dict | None:
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    now = datetime.now(tz)
    mine = datetime.now().astimezone()
    diff_min = round((now.utcoffset().total_seconds() - mine.utcoffset().total_seconds()) / 60)
    if diff_min == 0:
        diff = "Same time as you"
    else:
        h, m = divmod(abs(diff_min), 60)
        span = f"{h} h" + (f" {m} min" if m else "")
        diff = f"{span} {'ahead of' if diff_min > 0 else 'behind'} you"
    offset = now.strftime("%z")
    return {"time": now.strftime("%H:%M"), "date": now.strftime("%A, %d %B %Y"),
            "zone": now.tzname() or tz_name, "offset": f"UTC{offset[:3]}:{offset[3:]}",
            "diff": diff, "tz": tz_name, "epoch_ms": int(now.timestamp() * 1000)}
