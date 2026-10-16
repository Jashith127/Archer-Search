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
import threading
from pathlib import Path

import httpx
from pydantic import ValidationError

from archer import envfile, sources
from archer.ai_provider import PROVIDERS, list_models, resolve
from archer.config import DATA_DIR, ENV_FILE, Settings, settings


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    note: str = ""  # a line under the label on provider cards
    badge: str = ""  # e.g. "Recommended", "No setup"


@dataclass(frozen=True)
class Spec:
    name: str
    section: str
    label: str
    kind: str = "text"  # text | number | select | secret | url | cards | switch | model
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
    # Shown only while other fields hold these values: "ai_provider=openai,anthropic;other=x".
    when: str = ""
    link: tuple[str, ...] = ()  # (label, url) for a "get a key" link


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
LANGUAGES = (("", "Provider default"), ("all", "All languages"), ("en", "English"), ("en-IN", "English (India)"),
    ("en-US", "English (US)"), ("en-GB", "English (UK)"), ("hi", "Hindi"), ("ta", "Tamil"), ("te", "Telugu"), ("kn", "Kannada"),
    ("ml", "Malayalam"), ("bn", "Bengali"), ("mr", "Marathi"), ("de", "German"), ("fr", "French"), ("es", "Spanish"),
    ("pt", "Portuguese"), ("it", "Italian"), ("nl", "Dutch"), ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"))


def opts(pairs):
    return tuple(Option(value, label) for value, label in pairs)


SEARCH_PROVIDERS = (
    Option("duckduckgo", "DuckDuckGo", "Works right away. No key, no account. Best effort: it may slow down if you search a lot.", "No setup"),
    Option("searxng", "SearXNG", "Your own metasearch instance (Docker). Most private, combines many engines.", "Self-hosted"),
    Option("google", "Google", "Google's results through Serper (2,500 free searches), or an existing Google search key.", "API key"),
    Option("brave", "Brave Search", "Brave's independent index through its official API. Includes a monthly credit.", "API key"),
)
AI_PROVIDERS = tuple(Option(name, info.label, info.blurb, {"deepseek": "Recommended", "ollama": "Local · free"}.get(name, ""))
                     for name, info in PROVIDERS.items())
AI_KEYED = ",".join(name for name, info in PROVIDERS.items() if info.key)

SPECS = (
    # ---------------------------------------------------------------- personal
    Spec("home_location", "personal", "Home location", help="Used for weather, time and nearby places when a search names no place.", placeholder="Your city"),
    Spec("home_country", "personal", "Country or region", "select", help="Regional results, where-to-watch listings and music embeds use this.", options=opts(COUNTRIES)),
    Spec("home_currency", "personal", "Currency", "select", help="Prices, crypto and currency answers default to this.", options=opts(CURRENCIES)),
    Spec("contact", "personal", "Contact for Wikipedia", help="Wikimedia asks automated clients for an email or URL. Without one, entity cards may be refused.", placeholder="you@example.com"),

    # ------------------------------------------------------------------ search
    Spec("search_provider", "search", "Search provider", "cards", options=SEARCH_PROVIDERS),
    Spec("searxng_url", "search", "SearXNG address", "url", help="The SearXNG instance Archer queries for results and suggestions.", placeholder="http://127.0.0.1:8888", test="searxng", when="search_provider=searxng"),
    Spec("google_backend", "search", "Connect Google through", "select", when="search_provider=google",
         help="Google stopped accepting new sign-ups for its own search API, which ends on 1 January 2027. Serper is open to everyone.",
         options=opts((("serper", "Serper.dev · recommended"), ("cse", "Google Programmable Search · existing keys")))),
    Spec("serper_api_key", "search", "Serper API key", "secret", help="Free key from serper.dev. Each search uses one credit; the test uses one too.", placeholder="Serper key", test="serper",
         when="search_provider=google;google_backend=serper", link=("Get a free key", "https://serper.dev/api-key")),
    Spec("google_api_key", "search", "Google API key", "secret", help="From Google Cloud, with the Custom Search API enabled. 100 free searches a day.", placeholder="AIza…", test="google",
         when="search_provider=google;google_backend=cse"),
    Spec("google_cx", "search", "Search engine ID", help="The “cx” of a Programmable Search Engine set to search the entire web.", placeholder="0123456789abc:defghij",
         when="search_provider=google;google_backend=cse"),
    Spec("brave_api_key", "search", "Brave Search API key", "secret", help="Subscription token from Brave's API dashboard. The test uses one query.", placeholder="BSA…", test="brave",
         when="search_provider=brave", link=("Get a key", "https://api-dashboard.search.brave.com/app/keys")),
    Spec("search_fallback", "search", "Fall back to DuckDuckGo", "switch", help="If your provider is down, rate-limited or out of credit, show DuckDuckGo's results and say so."),
    Spec("safesearch", "search", "SafeSearch", "select", help="Filtering requested from your search provider.",
         options=opts((("", "Provider default"), ("0", "Off"), ("1", "Moderate"), ("2", "Strict"))), optional=True),
    Spec("search_language", "search", "Results language", "select", help="Language hint sent with every search.", options=opts(LANGUAGES)),
    Spec("suggest_provider", "search", "Suggestions from", "select", help="Where address-bar and search-box suggestions come from. Neither DuckDuckGo nor Google needs a key for them.",
         options=opts((("auto", "Match the search provider"), ("duckduckgo", "DuckDuckGo"), ("google", "Google"), ("searxng", "SearXNG"), ("off", "Off")))),
    Spec("suggest_timeout", "search", "Suggestion time limit", "number", help="Suggestions give up after this, so typing never lags.", unit="seconds", step="0.1", minimum="0.3", maximum="5", advanced=True),
    Spec("results_url", "search", "SearXNG page for bangs", help="Searches with !bangs open here when SearXNG is your provider. {q} becomes the query. Other providers use DuckDuckGo's bangs.", placeholder="http://localhost:8888/search?q={q}", advanced=True),

    # ---------------------------------------------------------------------- ai
    Spec("ai_provider", "ai", "AI provider", "cards", options=AI_PROVIDERS),
    *(Spec(info.key, "ai", f"{info.label} API key", "secret", help="Stored in .env on this computer. Testing it lists models and costs nothing.",
           placeholder={"anthropic": "sk-ant-…", "openrouter": "sk-or-…"}.get(name, "sk-…"), test="ai", when=f"ai_provider={name}",
           link=("Get a key", info.key_url)) for name, info in PROVIDERS.items() if info.key and name != "custom"),
    Spec("ai_base_url", "ai", "Server address", "url", help="The OpenAI-compatible base address, usually ending in /v1.", placeholder="http://127.0.0.1:1234/v1", when="ai_provider=custom"),
    Spec("ai_api_key", "ai", "API key", "secret", help="Leave empty if your server doesn't need one.", placeholder="optional", test="ai", when="ai_provider=custom"),
    Spec("ai_model", "ai", "Model", "model", help="Used for answers and research planning. Test the connection to list every model you can use."),
    Spec("ollama_context", "ai", "Context window", "number", help="How much evidence a local model reads at once. Larger needs more memory.", unit="tokens", step="1024", minimum="2048", maximum="262144", when="ai_provider=ollama"),
    Spec("ai_planning_rounds", "ai", "Research planning", "select", help="Tool-planning calls a research run may make before writing.", advanced=True,
         options=opts((("0", "None · answer from search only"), ("1", "One round (recommended)"), ("2", "Two rounds · more thorough")))),
    Spec("ai_answer_tokens", "ai", "Research answer length", "number", help="Ceiling for one research answer.", unit="tokens", step="100", minimum="1500", maximum="8000", advanced=True),
    Spec("ai_base_url", "ai_advanced", "Provider address override", "url", help="Point a provider at a proxy or regional endpoint. Leave empty for its own address.", placeholder="Provider default", advanced=True,
         when="ai_provider=deepseek,openai,anthropic,ollama,openrouter"),

    Spec("ai_run_usd", "limits", "Per answer", "number", help="A single answer or research run stops before exceeding this.", unit="USD", step="0.01", minimum="0.01"),
    Spec("ai_daily_usd", "limits", "Per day", "number", help="All AI use stops for the day (UTC) at this amount.", unit="USD", step="0.1", minimum="0.01"),
    Spec("ai_daily_tokens", "limits", "Daily tokens", "number", help="A hard token cap that applies even when pricing is unknown.", unit="tokens", step="1000", minimum="1"),
    Spec("ai_input_price", "limits", "Input price", "number", help="Override the model's price. Leave empty to use Archer's known prices.", unit="USD / 1M tokens", step="0.01", minimum="0", optional=True, advanced=True),
    Spec("ai_output_price", "limits", "Output price", "number", unit="USD / 1M tokens", step="0.01", minimum="0", optional=True, advanced=True),

    # ------------------------------------------------------------- connections
    Spec("tmdb_api_key", "connections", "TMDB key", "secret", help="Free key from themoviedb.org. Enables country-specific where-to-watch listings.", placeholder="v3 key or v4 read token", test="tmdb",
         link=("Get a free key", "https://www.themoviedb.org/settings/api")),

    # ---------------------------------------------------------------- history
    Spec("history", "history", "Keep search history", "switch",
         help="Saves your searches and the results you open in data/history.sqlite3 on this computer. Nothing is uploaded."),
    Spec("personalize", "history", "Personalize results", "switch",
         help="Ranks results by meaning and by the sites you open for searches like this, shows pages you found before, and puts your own past searches first in suggestions."),
    Spec("history_days", "history", "Keep history for", "select", help="Older searches and visits are deleted when Archer starts.",
         options=opts((("30", "30 days"), ("90", "3 months"), ("365", "1 year"), ("0", "Until I clear it")))),
    Spec("semantic_model", "history", "Matching", "select",
         help="How Archer tells that two searches mean the same thing. The models run on this computer; they download once from Hugging Face.",
         options=opts((("potion-base-8M", "Small model · 30 MB (recommended)"), ("potion-retrieval-32M", "Retrieval model · 130 MB, closer matches"),
                       ("ollama", "Ollama embedding model (local)"), ("lexical", "Word matching only · no download")))),
    Spec("ollama_embed_model", "history", "Ollama embedding model", help="Pull it first, e.g. `ollama pull nomic-embed-text`.",
         placeholder="nomic-embed-text", when="semantic_model=ollama"),

    # ----------------------------------------------------------------- startup
    Spec("startup_open", "startup", "When Archer starts with Windows", "select", help="Opening Archer from the Start menu always shows this app.",
         options=opts((("none", "Stay quietly in the tray"), ("app", "Open this companion app"), ("search", "Open Archer search")))),
    Spec("startup_delay", "startup", "Delay after sign-in", "number", help="Wait before starting, so Archer doesn't compete with everything else at sign-in.", unit="seconds", step="5", minimum="0", maximum="300"),
    Spec("startup_notify", "startup", "Notify when ready", "switch", help="A small Windows notification once Archer is running after sign-in."),
    Spec("searxng_container", "startup", "Start SearXNG's container", help="A Docker container name to start with Archer, so SearXNG is ready before your first search. Leave empty to skip.", placeholder="searxng"),

    # ------------------------------------------------------------------ system
    Spec("port", "system", "Port", "number", help="Archer's local port. Update your browser's search engine address after changing it.", minimum="1024", maximum="65535", step="1", restart=True),
    Spec("public_host", "system", "Address name", help="Chromium browsers resolve any *.localhost name to this computer.", placeholder="archer.localhost", restart=True),
)
SECTIONS_ORDER = (
    ("personal", "Personal", "Defaults Archer uses when a search doesn't say otherwise."),
    ("search", "Search", "Where Archer's results and suggestions come from."),
    ("ai", "AI", "Answers and AI Mode. Nothing is sent to a provider until you ask."),
    ("ai_advanced", "Advanced", ""),
    ("limits", "Spending limits", "Hard caps checked before every AI call."),
    ("connections", "Connections", "Optional services that make cards more precise."),
    ("history", "History & personalization", "Kept on this computer only. Pausing stops recording; clearing removes it."),
    ("startup", "Startup", "How Archer starts with Windows."),
    ("system", "System", "How Archer runs on this computer."),
)
# ai_base_url appears twice (custom server, and an advanced override); one value backs both.
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
    if isinstance(value, bool):
        return "true" if value else "false"
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
        written = set(envfile.keys(envfile.read(self.path)))
        out = []
        for spec in BY_NAME.values():
            value = getattr(current, spec.name)
            # "explicit": saved in .env, not just Archer's default (setup only suggests values for defaults).
            item = {"name": spec.name, "locked": locked(spec.name), "explicit": bool(written & set(env_names(spec.name)))}
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
        if "ai_provider" in changes and changes["ai_provider"] != self.stored().ai_provider:
            # A model or address chosen for the previous provider would be wrong for the new one.
            changes.setdefault("ai_model", None)
            changes.setdefault("ai_base_url", None)
        if not changes:
            return {"changed": [], "restart": self.restart_pending}
        text, loaded = self.candidate(changes)
        envfile.write(self.path, text)
        previous = {name: getattr(self.live, name) for name in BY_NAME}
        for name in type(loaded).model_fields:
            if name in BY_NAME and BY_NAME[name].restart:
                continue
            setattr(self.live, name, getattr(loaded, name))
        self.saved = loaded
        changed = [name for name in BY_NAME if getattr(loaded, name) != previous[name]]
        return {"changed": changed, "restart": self.restart_pending}


PATTERNS = {"public_host": "Use localhost, 127.0.0.1 or a name ending in .localhost, so Archer stays on this computer.",
            "search_language": "Choose a language from the list.",
            "searxng_container": "Use the container's name: letters, digits, dots, dashes and underscores."}


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
    if {"searxng_url", "safesearch", "search_language", "search_provider", "suggest_provider", "google_backend",
            "serper_api_key", "google_api_key", "google_cx", "brave_api_key", "home_country", "search_fallback"} & set(changed) and getattr(state, "searx", None) is not None:
        state.searx._cache.clear()
        state.searx._suggest_cache.clear()
    if {"semantic_model", "personalize", "ollama_embed_model"} & set(changed) and settings.personalize:
        from archer.embed import embeddings
        threading.Thread(target=embeddings.warm, daemon=True).start()
    if "history_days" in changed and getattr(state, "history", None) is not None:
        state.history.prune(settings.history_days)
    if "contact" in changed:
        agent = re.sub(r"\(.*?;", f"({settings.contact};", sources.USER_AGENT, count=1)
        sources.USER_AGENT = agent
        for client in (getattr(state, "searxng", None), getattr(state, "ext", None)):
            if client is not None:
                client.headers["User-Agent"] = agent


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


async def check_connection(client: httpx.AsyncClient, target: str, value: str = "", base: str = "",
                           provider: str = "", model: str = "", extra: str = "") -> dict:
    """Check a connection. AI, TMDB and SearXNG checks spend nothing (they list models or read
    configuration). Search-API checks run one tiny query, which the button says beforehand."""
    try:
        if target == "ai":
            r = resolve(settings, provider or None, value or None, base or None, model or None)
            if r.info.key and r.provider != "custom" and not r.key:
                return {"ok": False, "message": "Add a key first."}
            if not r.base:
                return {"ok": False, "message": "Add the server's address first."}
            status, models = await list_models(client, r)
            if status in (401, 403):
                return {"ok": False, "message": f"{r.info.label} rejected this key."}
            if status != 200:
                return {"ok": False, "message": f"{r.info.label} answered HTTP {status}."}
            if r.provider == "ollama":
                if not models:
                    return {"ok": False, "message": "Ollama is running but has no models. Run `ollama pull qwen3:8b` (or another model) first.", "models": []}
                note = f" · “{r.model}” isn't installed" if r.model and r.model not in models else ""
                return {"ok": True, "message": f"Ollama is running · {_plural(len(models), 'model')} installed{note}", "models": models[:60]}
            note = f" · “{r.model}” isn't in its model list" if models and r.model and r.model not in models else ""
            return {"ok": True, "message": f"Key accepted · {_plural(len(models), 'model')} available{note}", "models": models[:200]}
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
            return {"ok": True, "message": f"Connected · {_plural(engines, 'engine')} enabled{detail}"}
        if target == "duckduckgo":
            from archer import websearch
            reply = await websearch.duckduckgo(client, "open source search engine", "general", 1)
            if not reply.results:
                return {"ok": False, "message": "DuckDuckGo answered but returned no results. It may be rate-limiting this computer."}
            return {"ok": True, "message": f"DuckDuckGo is answering · {_plural(len(reply.results), 'result')} for a test search"}
        if target == "serper":
            key = value or settings.serper_api_key
            if not key:
                return {"ok": False, "message": "Add a key first."}
            response = await client.post("https://google.serper.dev/search", json={"q": "archer", "num": 1},
                                          headers={"X-API-KEY": key}, timeout=8)
            if response.status_code in (401, 403):
                return {"ok": False, "message": "Serper rejected this key."}
            if response.status_code != 200:
                return {"ok": False, "message": f"Serper answered HTTP {response.status_code}" + (" · out of credits?" if response.status_code in (400, 429) else ".")}
            credits = response.json().get("credits")
            return {"ok": True, "message": "Key accepted · Google results are working" + (f" (used {credits} credit)" if credits else "")}
        if target == "google":
            key, cx = value or settings.google_api_key, extra or settings.google_cx
            if not key or not cx:
                return {"ok": False, "message": "Add the key and the search engine ID first."}
            response = await client.get("https://www.googleapis.com/customsearch/v1", params={"key": key, "cx": cx, "q": "archer", "num": 1}, timeout=8)
            if response.status_code in (400, 403):
                return {"ok": False, "message": "Google rejected this key or engine ID. New keys can no longer use this API."}
            if response.status_code != 200:
                return {"ok": False, "message": f"Google answered HTTP {response.status_code}."}
            return {"ok": True, "message": "Key accepted · Google results are working (until Google retires this API on 1 January 2027)"}
        if target == "brave":
            key = value or settings.brave_api_key
            if not key:
                return {"ok": False, "message": "Add a key first."}
            response = await client.get("https://api.search.brave.com/res/v1/web/search", params={"q": "archer", "count": 1},
                                        headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=8)
            if response.status_code in (401, 403, 422):
                return {"ok": False, "message": "Brave rejected this key."}
            if response.status_code == 429:
                return {"ok": False, "message": "Brave accepted the key but its rate limit or credit is used up."}
            if response.status_code != 200:
                return {"ok": False, "message": f"Brave answered HTTP {response.status_code}."}
            return {"ok": True, "message": "Key accepted · Brave results are working"}
    except (httpx.HTTPError, ValueError, AttributeError, KeyError, TypeError):
        if target == "ai" and (provider or settings.ai_provider) == "ollama" and not base:
            return {"ok": False, "message": "Ollama isn't running. Install it from ollama.com, open it, and try again."}
        return {"ok": False, "message": "Couldn't reach it. Check the address and that it's running."}
    return {"ok": False, "message": "Unknown connection."}
