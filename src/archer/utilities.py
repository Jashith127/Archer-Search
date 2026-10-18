"""Instant answers computed locally: no network, no LLM."""

import ast
import colorsys
import math
import operator
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------- calculator

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "log": math.log10, "ln": math.log, "abs": abs, "round": round}
_CONSTS = {"pi": math.pi, "e": math.e}
_CALC_CHARS = re.compile(r"^[\d\s+\-*/%^().,a-z]+$")


def _eval(node: ast.AST, env: dict[str, float] | None = None) -> float:
    env = env or {}
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and (node.id in env or node.id in _CONSTS):
        return env.get(node.id, _CONSTS.get(node.id))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left, env), _eval(node.right, env)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCS and len(node.args) == 1 and not node.keywords):
        return _FUNCS[node.func.id](_eval(node.args[0], env))
    raise ValueError("unsupported expression")


def calculate(q: str) -> dict | None:
    expr = q.lower().strip().rstrip("=").strip()
    expr = re.sub(r"^(calculate|calc|what is|what's|compute)\s+", "", expr).rstrip("?")
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    expr = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", expr)
    # "15% of 2400" -> (15/100*2400); a trailing "50%" -> (50/100)
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", expr)
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*\d)", r"(\1/100)", expr)
    if not _CALC_CHARS.match(expr) or not re.search(r"\d", expr):
        return None
    if not re.search(r"[+\-*/%]|\b(?:" + "|".join(_FUNCS) + r")\(", expr):
        return None  # a bare number is not a calculation
    try:
        value = _eval(ast.parse(expr, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError, RecursionError):
        return None
    if isinstance(value, complex) or not math.isfinite(value):
        return None
    return {"expression": q.strip().rstrip("="), "value": fmt_number(value)}


def fmt_number(value: float, sig: int = 10) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    text = f"{value:,.{sig}g}" if abs(value) >= 1e-4 else f"{value:.{sig}g}"
    if "e" not in text and "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# ------------------------------------------------- equations and graphs

_X_EXPR = re.compile(r"^[\dx+\-*/^().\s a-z]+$")


def _parse_x(expr: str) -> ast.Expression | None:
    """Parse an expression in x, allowing implicit multiplication (2x, 3(x+1), x(x-1))."""
    expr = expr.strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    if not expr or not _X_EXPR.match(expr) or "x" not in expr:
        return None
    expr = re.sub(r"(\d)\s*(x|\(|[a-z]{2,}\()", r"\1*\2", expr)
    expr = re.sub(r"(x|\))\s*(\d|x|\()", r"\1*\2", expr)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    if not names <= {"x", *_CONSTS, *_FUNCS}:
        return None
    return tree


def _f(tree: ast.Expression, x: float) -> float:
    return float(_eval(tree, {"x": x}))


def solve_equation(q: str) -> dict | None:
    """Linear and quadratic equations in x, e.g. "solve 2x+3=11", "x^2-5x+6=0"."""
    text = re.sub(r"^solve\s+(?:for x\s*:?\s*)?", "", q.strip().lower())
    if text.count("=") != 1:
        return None
    lhs, rhs = text.split("=")
    left, right = _parse_x(lhs + "+0*x"), _parse_x(rhs + "+0*x")
    if not left or not right:
        return None
    try:
        f = [(_f(left, x) - _f(right, x)) for x in range(4)]
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None
    d1, d2, d3 = f[1] - f[0], f[2] - 2 * f[1] + f[0], f[3] - 3 * f[2] + 3 * f[1] - f[0]
    if abs(d3) > 1e-9:
        return None  # beyond quadratic
    a, b, c = d2 / 2, d1 - d2 / 2, f[0]
    form = lambda v: fmt_number(round(v, 10), 8)

    def poly(*coeffs: float) -> str:
        """[1, -5, 6] -> "x² − 5x + 6"."""
        powers = ["x²", "x", ""][-len(coeffs):]
        out = ""
        for k, p in zip(coeffs, powers):
            if abs(k) < 1e-12:
                continue
            mag = form(abs(k)) if (abs(abs(k) - 1) > 1e-12 or not p) else ""
            sign = ("−" if k < 0 else "") if not out else (" − " if k < 0 else " + ")
            out += f"{sign}{mag}{p}"
        return out or "0"

    if abs(a) < 1e-12:
        if abs(b) < 1e-12:
            return {"equation": q.strip(), "kind": "none" if abs(c) > 1e-12 else "all",
                    "steps": [], "roots": []}
        return {"equation": text, "kind": "linear",
                "steps": [f"Rearrange to {poly(b, c)} = 0", f"x = −({form(c)}) ÷ {form(b)}"],
                "roots": [form(-c / b)]}
    disc = b * b - 4 * a * c
    steps = [f"Rearrange to {poly(a, b, c)} = 0",
             f"Discriminant b² − 4ac = {form(disc)}"]
    if disc < -1e-12:
        re_part, im_part = -b / (2 * a), math.sqrt(-disc) / (2 * abs(a))
        roots = [f"{form(re_part)} + {form(im_part)}i", f"{form(re_part)} − {form(im_part)}i"]
        steps.append("Negative discriminant: two complex roots")
    else:
        r = math.sqrt(max(disc, 0))
        roots = sorted({form((-b + r) / (2 * a)), form((-b - r) / (2 * a))}, key=lambda s: float(s.replace(",", "")))
        steps.append("x = (−b ± √disc) ÷ 2a")
    return {"equation": text, "kind": "quadratic", "steps": steps, "roots": roots}


_GRAPH = re.compile(r"^(?:graph|plot)\s+(?:of\s+)?(?:y\s*=\s*|f\(x\)\s*=\s*)?(?P<a>.+)$|^(?:y|f\(x\))\s*=\s*(?P<b>.+)$")


def graph(q: str, lo: float = -10, hi: float = 10, w: int = 800, h: int = 340) -> dict | None:
    """Sample y=f(x) and return an SVG path (rendered server-side; the CSP forbids eval in JS)."""
    m = _GRAPH.match(q.strip().lower())
    if not m:
        return None
    expr = (m.group("a") or m.group("b")).strip()
    tree = _parse_x(expr)
    if not tree:
        return None
    pts: list[tuple[float, float] | None] = []
    for i in range(401):
        x = lo + (hi - lo) * i / 400
        try:
            y = _f(tree, x)
            pts.append((x, y) if math.isfinite(y) and abs(y) < 1e6 else None)
        except (ValueError, ZeroDivisionError, OverflowError, TypeError):
            pts.append(None)
    ys = sorted(p[1] for p in pts if p)
    if len(ys) < 10:
        return None
    # Robust y-range: ignore the extreme 2% so asymptotes don't flatten the curve.
    y_lo, y_hi = ys[int(len(ys) * 0.02)], ys[int(len(ys) * 0.98) - 1]
    if y_hi - y_lo < 1e-9:
        y_lo, y_hi = y_lo - 1, y_hi + 1
    pad = (y_hi - y_lo) * 0.1
    y_lo, y_hi = y_lo - pad, y_hi + pad
    sx = lambda x: (x - lo) / (hi - lo) * w
    sy = lambda y: h - (y - y_lo) / (y_hi - y_lo) * h
    path, pen = [], False
    for p in pts:
        if p is None or not (y_lo - (y_hi - y_lo) <= p[1] <= y_hi + (y_hi - y_lo)):
            pen = False
            continue
        path.append(f"{'L' if pen else 'M'}{sx(p[0]):.1f},{sy(p[1]):.1f}")
        pen = True
    x_ticks = [{"pos": round(sx(v), 1), "label": str(v)} for v in range(int(lo), int(hi) + 1, 5)]
    step = 10 ** math.floor(math.log10((y_hi - y_lo) / 4))
    step *= 5 if (y_hi - y_lo) / step > 20 else 2 if (y_hi - y_lo) / step > 8 else 1
    y_ticks = [{"pos": round(sy(v), 1), "label": fmt_number(round(v, 6))}
               for v in (step * k for k in range(math.ceil(y_lo / step), math.floor(y_hi / step) + 1))]
    return {"expr": expr, "path": " ".join(path), "w": w, "h": h, "x_ticks": x_ticks, "y_ticks": y_ticks,
            "axis_x": round(sy(0), 1) if y_lo <= 0 <= y_hi else None, "axis_y": round(sx(0), 1)}


# --------------------------------------------------------- package tracking

_CARRIERS = [
    (re.compile(r"^1Z[0-9A-Z]{16}$"), "UPS", "https://www.ups.com/track?tracknum={}"),
    (re.compile(r"^(?:94|93|92|95)\d{20}$"), "USPS", "https://tools.usps.com/go/TrackConfirmAction?tLabels={}"),
    (re.compile(r"^[A-Z]{2}\d{9}IN$"), "India Post", "https://t.17track.net/en#nums={}"),
    (re.compile(r"^[A-Z]{2}\d{9}[A-Z]{2}$"), "Postal service (UPU)", "https://t.17track.net/en#nums={}"),
    (re.compile(r"^JD\d{18}$|^\d{10}$"), "DHL", "https://www.dhl.com/global-en/home/tracking.html?tracking-id={}"),
    (re.compile(r"^\d{12}$|^\d{15}$"), "FedEx", "https://www.fedex.com/fedextrack/?trknbr={}"),
]
_TRACK = re.compile(r"^(?:track(?:ing)?(?:\s+(?:number|no\.?|id|order|parcel|package))?\s*:?\s+)?(?P<n>[A-Za-z0-9]{10,26})$")


def tracking(q: str) -> dict | None:
    m = _TRACK.match(q.strip())
    explicit = q.strip().lower().startswith("track")
    if not m:
        return None
    number = m.group("n").upper()
    for pattern, carrier, url in _CARRIERS:
        if pattern.match(number):
            # Bare 10/12/15-digit numbers are too ambiguous (phone numbers, ids) without "track".
            if carrier in ("DHL", "FedEx") and not explicit:
                return None
            return {"number": number, "carrier": carrier, "url": url.format(number),
                    "universal": f"https://t.17track.net/en#nums={number}"}
    if explicit and re.search(r"\d", number):
        return {"number": number, "carrier": "", "url": "", "universal": f"https://t.17track.net/en#nums={number}"}
    return None


# ------------------------------------------------------------ unit converter

_UNITS: dict[str, dict[str, tuple[float, str]]] = {
    "Length": {
        "mm": (1e-3, "millimetre"), "cm": (1e-2, "centimetre"), "m": (1, "metre"),
        "km": (1e3, "kilometre"), "in": (0.0254, "inch"), "ft": (0.3048, "foot"),
        "yd": (0.9144, "yard"), "mi": (1609.344, "mile"), "nmi": (1852, "nautical mile"),
    },
    "Mass": {
        "mg": (1e-6, "milligram"), "g": (1e-3, "gram"), "kg": (1, "kilogram"),
        "t": (1e3, "tonne"), "lb": (0.45359237, "pound"), "oz": (0.028349523125, "ounce"),
        "st": (6.35029318, "stone"),
    },
    "Volume": {
        "ml": (1e-3, "millilitre"), "l": (1, "litre"), "m3": (1e3, "cubic metre"),
        "gal": (3.785411784, "US gallon"), "qt": (0.946352946, "US quart"),
        "pt": (0.473176473, "US pint"), "cup": (0.2365882365, "US cup"),
        "floz": (0.0295735295625, "US fluid ounce"), "tbsp": (0.01478676478125, "tablespoon"),
        "tsp": (0.00492892159375, "teaspoon"),
    },
    "Speed": {
        "m/s": (1, "metre per second"), "km/h": (1 / 3.6, "kilometre per hour"),
        "mph": (0.44704, "mile per hour"), "kn": (1852 / 3600, "knot"),
    },
    "Area": {
        "m2": (1, "square metre"), "km2": (1e6, "square kilometre"), "ft2": (0.09290304, "square foot"),
        "acre": (4046.8564224, "acre"), "ha": (1e4, "hectare"),
    },
    "Data": {
        "b": (1, "byte"), "kb": (1e3, "kilobyte"), "mb": (1e6, "megabyte"), "gb": (1e9, "gigabyte"),
        "tb": (1e12, "terabyte"), "kib": (1024, "kibibyte"), "mib": (1024**2, "mebibyte"),
        "gib": (1024**3, "gibibyte"), "tib": (1024**4, "tebibyte"),
    },
    "Time": {
        "ms": (1e-3, "millisecond"), "s": (1, "second"), "min": (60, "minute"), "h": (3600, "hour"),
        "day": (86400, "day"), "week": (604800, "week"), "year": (31557600, "year"),
    },
}
_ALIASES = {
    "millimeter": "mm", "millimetre": "mm", "centimeter": "cm", "centimetre": "cm", "meter": "m",
    "metre": "m", "kilometer": "km", "kilometre": "km", "inch": "in", "inches": "in", "\"": "in",
    "foot": "ft", "feet": "ft", "'": "ft", "yard": "yd", "mile": "mi", "nautical mile": "nmi",
    "milligram": "mg", "gram": "g", "kilogram": "kg", "kilo": "kg", "kgs": "kg", "tonne": "t",
    "ton": "t", "pound": "lb", "lbs": "lb", "ounce": "oz", "stone": "st",
    "milliliter": "ml", "millilitre": "ml", "liter": "l", "litre": "l", "cubic meter": "m3",
    "gallon": "gal", "quart": "qt", "pint": "pt", "fluid ounce": "floz", "fl oz": "floz",
    "tablespoon": "tbsp", "teaspoon": "tsp", "cups": "cup",
    "kmph": "km/h", "kph": "km/h", "kmh": "km/h", "knot": "kn", "knots": "kn", "mps": "m/s",
    "sq m": "m2", "square meter": "m2", "square metre": "m2", "sqm": "m2", "sq km": "km2",
    "square kilometer": "km2", "square kilometre": "km2", "sq ft": "ft2", "sqft": "ft2",
    "square foot": "ft2", "square feet": "ft2", "hectare": "ha", "acres": "acre",
    "byte": "b", "kilobyte": "kb", "megabyte": "mb", "gigabyte": "gb", "terabyte": "tb",
    "millisecond": "ms", "second": "s", "sec": "s", "secs": "s", "minute": "min", "mins": "min",
    "hour": "h", "hr": "h", "hrs": "h", "days": "day", "weeks": "week", "years": "year", "yr": "year",
}
_TEMPS = {"c": "°C", "celsius": "°C", "°c": "°C", "f": "°F", "fahrenheit": "°F", "°f": "°F",
          "k": "K", "kelvin": "K"}
_CONVERT = re.compile(
    r"^(?:convert\s+)?(-?\d+(?:[.,]\d+)?)\s*([a-z°'\"/0-9 ]+?)\s+(?:in|to|into|as)\s+([a-z°'\"/0-9 ]+?)\??$"
)


def _unit(name: str) -> tuple[str, str] | None:
    name = name.strip().lower().removeprefix("degrees ").removeprefix("degree ")
    for candidate in (name, name.rstrip("s") if len(name) > 3 else name):
        key = _ALIASES.get(candidate, candidate)
        for category, units in _UNITS.items():
            if key in units:
                return category, key
    return None


def convert_units(q: str) -> dict | None:
    m = _CONVERT.match(q.lower().strip())
    if not m:
        return None
    amount = float(m.group(1).replace(",", "."))
    src, dst = m.group(2).strip(), m.group(3).strip()
    if src.removeprefix("degrees ") in _TEMPS and dst.removeprefix("degrees ") in _TEMPS:
        a, b = _TEMPS[src.removeprefix("degrees ")], _TEMPS[dst.removeprefix("degrees ")]
        kelvin = {"°C": amount + 273.15, "°F": (amount - 32) * 5 / 9 + 273.15, "K": amount}[a]
        out = {"°C": kelvin - 273.15, "°F": (kelvin - 273.15) * 9 / 5 + 32, "K": kelvin}[b]
        return {"category": "Temperature", "amount": fmt_number(amount), "from": a, "to": b,
                "from_name": a, "to_name": b, "value": fmt_number(round(out, 6)), "factor": None}
    s, d = _unit(src), _unit(dst)
    if not s or not d or s[0] != d[0]:
        return None
    category = s[0]
    (fs, name_s), (fd, name_d) = _UNITS[category][s[1]], _UNITS[category][d[1]]
    factor = fs / fd
    return {"category": category, "amount": fmt_number(amount), "from": s[1], "to": d[1],
            "from_name": name_s, "to_name": name_d, "value": fmt_number(amount * factor, 8),
            "factor": fmt_number(factor, 8)}


# ------------------------------------------------------------------- colour

_HEX = re.compile(r"^(?:colou?r\s+)?#([0-9a-f]{6}|[0-9a-f]{3})$|^(?:colou?r|hex)\s+([0-9a-f]{6}|[0-9a-f]{3})$")
_RGB = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$")


def color(q: str) -> dict | None:
    text = q.lower().strip()
    if m := _HEX.match(text):
        h = m.group(1) or m.group(2)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    elif m := _RGB.match(text):
        r, g, b = (int(x) for x in m.groups())
        if max(r, g, b) > 255:
            return None
    else:
        return None
    hue, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    lum = _luminance(r, g, b)
    # The same hue and saturation from light to dark; the step nearest the colour itself is marked.
    scale = []
    for level in (0.95, 0.86, 0.76, 0.66, 0.56, 0.46, 0.36, 0.26, 0.16):
        sr, sg, sb = (round(x * 255) for x in colorsys.hls_to_rgb(hue, level, sat))
        scale.append({"hex": f"#{sr:02x}{sg:02x}{sb:02x}", "light": level > 0.6})
    nearest = min(range(len(scale)), key=lambda i: abs((0.95, 0.86, 0.76, 0.66, 0.56, 0.46, 0.36, 0.26, 0.16)[i] - light))
    scale[nearest]["self"] = True
    return {"hex": f"#{r:02x}{g:02x}{b:02x}", "rgb": f"rgb({r}, {g}, {b})",
            "hsl": f"hsl({round(hue * 360)}, {round(sat * 100)}%, {round(light * 100)}%)",
            "light": (0.299 * r + 0.587 * g + 0.114 * b) > 150, "scale": scale,
            # WCAG contrast of white and of black text on this colour, rounded down so 4.46 never reads as a pass.
            "on_white": int(1.05 / (lum + 0.05) * 10) / 10, "on_black": int((lum + 0.05) / 0.05 * 10) / 10}


def _luminance(r: int, g: int, b: int) -> float:
    """WCAG relative luminance."""
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


# ---------------------------------------------------------------- timestamp

def timestamp(q: str) -> dict | None:
    text = q.strip().lower()
    if text in ("unix time", "unix timestamp", "epoch", "timestamp now", "current timestamp"):
        now = datetime.now(timezone.utc)
        return {"input": "now", "epoch": int(now.timestamp()), "utc": now.strftime("%a, %d %b %Y %H:%M:%S UTC"),
                "local": now.astimezone().strftime("%a, %d %b %Y %H:%M:%S %Z")}
    if not re.fullmatch(r"\d{10}|\d{13}", text):
        return None
    seconds = int(text) / (1000 if len(text) == 13 else 1)
    if not 946684800 <= seconds <= 4102444800:  # 2000–2100; phone numbers fall outside
        return None
    when = datetime.fromtimestamp(seconds, timezone.utc)
    return {"input": text, "epoch": int(seconds), "utc": when.strftime("%a, %d %b %Y %H:%M:%S UTC"),
            "local": when.astimezone().strftime("%a, %d %b %Y %H:%M:%S %Z")}


# ------------------------------------------------------------------- random

_RANDOM_NUM = re.compile(r"^(?:random number|pick a number|rng)(?: (?:between|from) (-?\d+) (?:and|to) (-?\d+))?$")


def random_tool(q: str) -> dict | None:
    text = q.lower().strip()
    if re.fullmatch(r"(?:flip|toss) a coin|coin (?:flip|toss)|heads or tails", text):
        return {"tool": "coin"}
    if re.fullmatch(r"roll (?:a )?(?:die|dice|d6)|dice roll|roll the dice", text):
        return {"tool": "dice", "min": 1, "max": 6}
    if m := _RANDOM_NUM.match(text):
        lo, hi = (int(m.group(1)), int(m.group(2))) if m.group(1) else (1, 100)
        return {"tool": "number", "min": min(lo, hi), "max": max(lo, hi)}
    return None


# ----------------------------------------------------------------- currency

_CURRENCY = re.compile(
    r"([\d.]+) ([A-Z]{3}) = ([\d.]+) ([A-Z]{3}) \(1 (.+?) : ([\d.]+) (.+?)\)"
)


def parse_currency_answer(answer: str) -> dict | None:
    """Parse SearXNG's currency engine answer into a converter widget."""
    m = _CURRENCY.search(answer)
    if not m:
        return None
    amount, src, value, dst, src_name, rate, dst_name = m.groups()
    return {"amount": fmt_number(float(amount)), "amount_raw": float(amount), "from": src, "to": dst,
            "from_name": src_name, "to_name": dst_name, "value": fmt_number(round(float(value), 2)),
            "rate": float(rate)}
