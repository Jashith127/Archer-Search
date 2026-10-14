"""The Settings app's model: which fields exist, how they are shown, and how edits are applied.

Edits are validated by loading a candidate .env through the same `Settings` class Archer uses
at startup, then written atomically and copied onto the live `settings` object. Every module
reads that shared object, so most changes apply without a restart. Secrets never leave the
server in full: the page sees whether a key is set and its last four characters.
"""
from dataclasses import dataclass, field
import os
import re
import tempfile
from pathlib import Path

import httpx
from pydantic import ValidationError

from archer import envfile, sources
from archer.config import DATA_DIR, ENV_FILE, Settings, settings


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class Spec:
    name: str
    section: str
    label: str
    kind: str = "text"  # text | number | select | secret | url
    help: str = ""
    options: tuple[Option, ...] = ()
    restart: bool = False
    advanced: bool = False
    placeholder: str = ""
    unit: str = ""
    step: str = ""
    minimum: str = ""
    maximum: str = ""
    test: str = ""
    optional: bool = False
    datalist: tuple[str, ...] = field(default_factory=tuple)


COUNTRIES = (("IN", "India"), ("US", "United States"), ("GB", "United Kingdom"), ("CA", "Canada"),
    ("AU", "Australia"), ("NZ", "New Zealand"), ("IE", "Ireland"), ("SG", "Singapore"), ("AE", "United Arab Emirates"),
    ("DE", "Germany"), ("FR", "France"), ("ES", "Spain"), ("IT", "Italy"), ("NL", "Netherlands"), ("SE", "Sweden"),
    ("NO", "Norway"), ("DK", "Denmark"), ("FI", "Finland"), ("CH", "Switzerland"), ("AT", "Austria"), ("BE", "Belgium"),
    ("PL", "Poland"), ("PT", "Portugal"), ("BR", "Brazil"), ("MX", "Mexico"), ("AR", "Argentina"), ("JP", "Japan"),
    ("KR", "South Korea"), ("CN", "China"), ("HK", "Hong Kong"), ("TW", "Taiwan"), ("TH", "Thailand"), ("MY", "Malaysia"),
    ("ID", "Indonesia"), ("PH", "Philippines"), ("PK", "Pakistan"), ("BD", "Bangladesh"), ("LK", "Sri Lanka"),
    ("NP", "Nepal"), ("SA", "Saudi Arabia"), ("TR", "Türkiye"), ("ZA", "South Africa"), ("NG", "Nigeria"), ("KE", "Kenya"))
CURRENCIES = (("inr", "INR · Indian rupee"), ("usd", "USD · US dollar"), ("eur", "EUR · Euro"), ("gbp", "GBP · Pound sterling"),
    ("jpy", "JPY · Japanese yen"), ("aud", "AUD · Australian dollar"), ("cad", "CAD · Canadian dollar"), ("sgd", "SGD · Singapore dollar"),
    ("aed", "AED · UAE dirham"), ("chf", "CHF · Swiss franc"), ("cny", "CNY · Chinese yuan"), ("hkd", "HKD · Hong Kong dollar"),
    ("nzd", "NZD · New Zealand dollar"), ("sek", "SEK · Swedish krona"), ("krw", "KRW · South Korean won"), ("brl", "BRL · Brazilian real"),
    ("zar", "ZAR · South African rand"), ("mxn", "MXN · Mexican peso"), ("try", "TRY · Turkish lira"), ("pln", "PLN · Polish złoty"))
LANGUAGES = (("", "Instance default"), ("all", "All languages"), ("en", "English"), ("en-IN", "English (India)"),
    ("en-US", "English (US)"), ("en-GB", "English (UK)"), ("hi", "Hindi"), ("ta", "Tamil"), ("te", "Telugu"), ("kn", "Kannada"),
    ("ml", "Malayalam"), ("bn", "Bengali"), ("mr", "Marathi"), ("de", "German"), ("fr", "French"), ("es", "Spanish"),
    ("pt", "Portuguese"), ("it", "Italian"), ("nl", "Dutch"), ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"))


def opts(pairs):
    return tuple(Option(value, label) for value, label in pairs)


SPECS = (
    Spec("home_location", "general", "Home location", help="Used for weather, time and nearby places when a search names no place.", placeholder="Bengaluru"),
    Spec("home_country", "general", "Country", "select", help="Where-to-watch listings and music embeds use this region.", options=opts(COUNTRIES)),
    Spec("home_currency", "general", "Currency", "select", help="Crypto prices and currency answers default to this.", options=opts(CURRENCIES)),
    Spec("contact", "general", "Contact for Wikipedia", help="Wikimedia asks automated clients for an email or URL. Without one, entity cards may be refused.", placeholder="you@example.com"),

    Spec("safesearch", "search", "SafeSearch", "select", help="Filtering requested from your SearXNG engines.",
         options=opts((("", "Instance default"), ("0", "Off"), ("1", "Moderate"), ("2", "Strict"))), optional=True),
    Spec("search_language", "search", "Results language", "select", help="Language hint sent with every search.", options=opts(LANGUAGES)),
    Spec("searxng_url", "search", "SearXNG address", "url", help="The SearXNG instance Archer queries for results and suggestions.", placeholder="http://127.0.0.1:8888", test="searxng"),
    Spec("suggest_timeout", "search", "Suggestion time limit", "number", help="Address-bar suggestions give up after this, so typing never lags.", unit="seconds", step="0.1", minimum="0.3", maximum="5", advanced=True),
    Spec("results_url", "search", "SearXNG page for bangs", help="Searches with !bangs open here. {q} becomes the query.", placeholder="http://localhost:8888/search?q={q}", advanced=True),

    Spec("deepseek_api_key", "ai", "DeepSeek API key", "secret", help="Stored in .env on this computer. Testing it lists models and costs nothing.", placeholder="sk-…", test="ai"),
    Spec("ai_model", "ai", "Model", help="Model used for answers and research planning.", placeholder="deepseek-flash", datalist=("deepseek-flash", "deepseek-chat", "deepseek-reasoner")),
    Spec("ai_base_url", "ai", "API address", "url", help="An OpenAI-compatible chat completions endpoint.", placeholder="https://api.deepseek.com", advanced=True),
    Spec("ai_planning_rounds", "ai", "Research planning", "select", help="Tool-planning calls a research run may make before writing.", advanced=True,
         options=opts((("0", "None · answer from search only"), ("1", "One round (recommended)"), ("2", "Two rounds · more thorough")))),
    Spec("ai_answer_tokens", "ai", "Research answer length", "number", help="Ceiling for one research answer.", unit="tokens", step="100", minimum="1500", maximum="8000", advanced=True),

    Spec("ai_run_usd", "limits", "Per answer", "number", help="A single answer or research run stops before exceeding this.", unit="USD", step="0.01", minimum="0.01"),
    Spec("ai_daily_usd", "limits", "Per day", "number", help="All AI use stops for the day (UTC) at this amount.", unit="USD", step="0.1", minimum="0.01"),
    Spec("ai_daily_tokens", "limits", "Daily tokens", "number", help="A hard token cap that applies even when pricing is unknown.", unit="tokens", step="1000", minimum="1"),
    Spec("ai_input_price", "limits", "Input price", "number", help="Override the provider's price. Leave empty to use Archer's reviewed DeepSeek ceilings.", unit="USD / 1M tokens", step="0.01", minimum="0", optional=True, advanced=True),
    Spec("ai_output_price", "limits", "Output price", "number", unit="USD / 1M tokens", step="0.01", minimum="0", optional=True, advanced=True),

    Spec("tmdb_api_key", "connections", "TMDB key", "secret", help="Free key from themoviedb.org. Enables country-specific where-to-watch listings.", placeholder="v3 key or v4 read token", test="tmdb"),

    Spec("port", "system", "Port", "number", help="Archer's local port. Update Helium's search engine address after changing it.", minimum="1024", maximum="65535", step="1", restart=True),
    Spec("public_host", "system", "Address name", help="Chromium resolves any *.localhost name to this computer.", placeholder="archer.localhost", restart=True),
)
SECTIONS_ORDER = (
    ("general", "General", "Defaults Archer uses when a search doesn't say otherwise."),
    ("search", "Search", "How Archer asks your SearXNG instance for results and suggestions."),
    ("ai", "AI", "Balanced answers and AI Mode. Nothing is sent to a provider until you ask."),
    ("limits", "Spending limits", "Hard caps checked before every AI call."),
    ("connections", "Connections", "Optional services that make cards more precise."),
    ("system", "System", "How Archer runs on this computer."),
)
BY_NAME = {spec.name: spec for spec in SPECS}
SECRETS = {spec.name for spec in SPECS if spec.kind == "secret"}


def env_name(name: str) -> str:
    """The variable a field is written as; aliased keys keep their conventional names."""
    alias = Settings.model_fields[name].validation_alias
    return alias.choices[0] if alias is not None else "ARCHER_" + name.upper()


def env_names(name: str) -> list[str]:
    alias = Settings.model_fields[name].validation_alias
    return list(alias.choices) if alias is not None else ["ARCHER_" + name.upper()]


def locked(name: str) -> bool:
    """A process environment variable overrides .env, so the page can't change it."""
    return any(key in os.environ or key.lower() in os.environ for key in env_names(name))


def display(value):
    if value is None:
        return ""
    return f"{value:g}" if isinstance(value, float) else str(value)


def mask(value: str) -> dict:
    return {"set": bool(value), "hint": "•••• " + value[-4:] if len(value) >= 8 else ("••••" if value else "")}


class SettingsError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


class SettingsService:
    def __init__(self, path: Path = ENV_FILE, live=settings):
        self.path = path
        self.live = live
        # Port and address are bound at startup; the file can hold newer values until a restart.
        self.saved = None

    def stored(self) -> Settings:
        return self.saved or self.live

    def fields(self) -> list[dict]:
        current = self.stored()
        out = []
        for spec in SPECS:
            value = getattr(current, spec.name)
            item = {"name": spec.name, "locked": locked(spec.name)}
            if spec.kind == "secret":
                item.update(mask(value or ""))
            elif spec.name == "contact" and value == Settings.model_fields["contact"].default:
                item["value"] = ""  # the built-in placeholder isn't a real contact
            else:
                item["value"] = display(value)
            out.append(item)
        return out

    @property
    def restart_pending(self) -> bool:
        current = self.stored()
        return any(getattr(current, s.name) != getattr(self.live, s.name) for s in SPECS if s.restart)

    def candidate(self, changes: dict[str, str | None]) -> tuple[str, Settings]:
        """Build the new .env text and load it exactly as Archer would at startup."""
        names = {}
        for key, value in changes.items():
            primary, *others = env_names(key)
            names[primary] = value
            # Another spelling of the same key would keep overriding the edit.
            names.update({other: None for other in others})
        text = envfile.update(envfile.read(self.path), names)
        handle, temporary = tempfile.mkstemp(prefix="archer-env-", suffix=".tmp", dir=DATA_DIR if DATA_DIR.exists() else None)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(text)
            loaded = Settings(_env_file=temporary)
        except ValidationError as exc:
            errors = {}
            for error in exc.errors():
                location = str(error["loc"][0]) if error["loc"] else ""
                name = next((s.name for s in SPECS if location in (s.name, *env_names(s.name))), location)
                errors[name] = friendly(error, name)
            raise SettingsError(errors) from exc
        finally:
            Path(temporary).unlink(missing_ok=True)
        return text, loaded

    def apply(self, raw: dict) -> dict:
        changes, errors = {}, {}
        for name, value in raw.items():
            spec = BY_NAME.get(name)
            if not spec:
                errors[name] = "Unknown setting."
                continue
            if locked(name):
                errors[name] = "Set by a system environment variable, so it can't be changed here."
                continue
            if value is not None and not isinstance(value, (str, int, float)):
                errors[name] = "Unsupported value."
                continue
            text = "" if value is None else str(value).strip()
            if len(text) > 500 or re.search(r"[\r\n\x00]", text):
                errors[name] = "Use a single line of up to 500 characters."
                continue
            if spec.kind == "url" and text and not re.match(r"^https?://[^\s/]+", text):
                errors[name] = "Enter an address starting with http:// or https://."
                continue
            # An empty value removes the line, so Archer's default applies again.
            changes[name] = text or None
        if errors:
            raise SettingsError(errors)
        if not changes:
            return {"changed": [], "restart": self.restart_pending}
        text, loaded = self.candidate(changes)
        envfile.write(self.path, text)
        previous = {s.name: getattr(self.live, s.name) for s in SPECS}
        for name in type(loaded).model_fields:
            if name in BY_NAME and BY_NAME[name].restart:
                continue
            setattr(self.live, name, getattr(loaded, name))
        self.saved = loaded
        changed = [s.name for s in SPECS if getattr(loaded, s.name) != previous[s.name]]
        return {"changed": changed, "restart": self.restart_pending}


PATTERNS = {"public_host": "Use localhost, 127.0.0.1 or a name ending in .localhost, so Archer stays on this computer.",
            "search_language": "Choose a language from the list."}


def friendly(error: dict, name: str = "") -> str:
    kind, ctx = error.get("type", ""), error.get("ctx") or {}
    number = lambda value: f"{value:g}" if isinstance(value, (int, float)) else str(value)
    if kind == "greater_than_equal":
        return f"Use {number(ctx['ge'])} or more."
    if kind == "greater_than":
        return f"Use a value above {number(ctx['gt'])}."
    if kind == "less_than_equal":
        return f"Use {number(ctx['le'])} or less."
    if kind == "less_than":
        return f"Use a value below {number(ctx['lt'])}."
    if kind.startswith(("float", "int")):
        return "Enter a number."
    if kind == "string_pattern_mismatch":
        return PATTERNS.get(name, "Choose one of the listed options.")
    return error.get("msg", "Invalid value.").removeprefix("Value error, ")


def apply_runtime(app, changed: list[str]) -> None:
    """Push live changes into clients created at startup."""
    state = app.state
    if "searxng_url" in changed and getattr(state, "searxng", None) is not None:
        state.searxng.base_url = settings.searxng_url
    if "suggest_timeout" in changed and getattr(state, "searxng", None) is not None:
        state.searxng.timeout = httpx.Timeout(settings.suggest_timeout)
    if {"searxng_url", "safesearch", "search_language"} & set(changed) and getattr(state, "searx", None) is not None:
        state.searx._cache.clear()
        state.searx._suggest_cache.clear()
    if "contact" in changed:
        agent = re.sub(r"\(.*?;", f"({settings.contact};", sources.USER_AGENT, count=1)
        sources.USER_AGENT = agent
        for client in (getattr(state, "searxng", None), getattr(state, "ext", None)):
            if client is not None:
                client.headers["User-Agent"] = agent


async def check_connection(client: httpx.AsyncClient, target: str, value: str = "", base: str = "") -> dict:
    """Check a connection without spending money: list models, read TMDB config, read SearXNG config."""
    try:
        if target == "ai":
            key = value or settings.deepseek_api_key
            if not key:
                return {"ok": False, "message": "Add a key first."}
            url = (base or settings.ai_base_url).rstrip("/") + "/models"
            response = await client.get(url, headers={"Authorization": "Bearer " + key}, timeout=8)
            if response.status_code in (401, 403):
                return {"ok": False, "message": "The provider rejected this key."}
            if response.status_code != 200:
                return {"ok": False, "message": f"The provider answered HTTP {response.status_code}."}
            models = [m.get("id") for m in response.json().get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
            model = settings.ai_model
            note = "" if not models or model in models else f" · “{model}” isn't in its model list"
            return {"ok": True, "message": f"Key accepted · {len(models)} model{'' if len(models) == 1 else 's'} available{note}", "models": models[:40]}
        if target == "tmdb":
            key = value or settings.tmdb_api_key
            if not key:
                return {"ok": False, "message": "Add a key first."}
            headers, params = ({"Authorization": f"Bearer {key}"}, {}) if key.startswith("eyJ") else ({}, {"api_key": key})
            response = await client.get("https://api.themoviedb.org/3/configuration", headers=headers, params=params, timeout=8)
            if response.status_code == 401:
                return {"ok": False, "message": "TMDB rejected this key."}
            return {"ok": response.status_code == 200, "message": "Key accepted" if response.status_code == 200 else f"TMDB answered HTTP {response.status_code}."}
        if target == "searxng":
            url = (value or settings.searxng_url).rstrip("/")
            if not re.match(r"^https?://", url):
                return {"ok": False, "message": "Enter an address starting with http:// or https://."}
            # /config describes the instance without sending a query to any search engine.
            response = await client.get(url + "/config", timeout=4)
            if response.status_code != 200:
                return {"ok": False, "message": f"SearXNG answered HTTP {response.status_code}."}
            data = response.json()
            engines = sum(1 for e in data.get("engines", []) if isinstance(e, dict) and e.get("enabled"))
            autocomplete = data.get("autocomplete") or ""
            detail = f" · suggestions via {autocomplete}" if autocomplete else " · suggestions are off in SearXNG"
            return {"ok": True, "message": f"Connected · {engines} engine{'' if engines == 1 else 's'} enabled{detail}"}
    except (httpx.HTTPError, ValueError, AttributeError):
        return {"ok": False, "message": "Couldn't reach it. Check the address and that it's running."}
    return {"ok": False, "message": "Unknown connection."}
