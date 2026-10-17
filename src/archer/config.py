from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
ENV_FILE = ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARCHER_", env_file=ENV_FILE, extra="ignore"
    )

    # Bind to loopback only: Archer is a single-machine engine.
    host: str = "127.0.0.1"
    port: int = Field(4747, ge=1024, le=65535)
    # Chromium resolves *.localhost to loopback, so no hosts-file edit is needed.
    public_host: str = Field("archer.localhost", pattern=r"^(localhost|127\.0\.0\.1|[a-z0-9-]+(\.[a-z0-9-]+)*\.localhost)$")
    # Where results come from. SearXNG is self-hosted; DuckDuckGo needs nothing; Google and
    # Brave need a key (see websearch.py for why each works the way it does).
    search_provider: Literal["searxng", "duckduckgo", "google", "brave"] = "searxng"
    # When the provider fails (down, rate-limited, out of credit), show DuckDuckGo's results instead.
    search_fallback: bool = True
    # Address-bar suggestions: "auto" follows the search provider (Brave uses DuckDuckGo's).
    suggest_provider: Literal["auto", "searxng", "duckduckgo", "google", "off"] = "auto"
    # Google results: Serper.dev (open to new sign-ups) or a legacy Programmable Search key.
    google_backend: Literal["serper", "cse"] = "serper"
    serper_api_key: str = ""
    google_api_key: str = ""
    google_cx: str = Field("", max_length=100)
    brave_api_key: str = Field("", validation_alias=AliasChoices("ARCHER_BRAVE_API_KEY", "BRAVE_API_KEY"))
    # Local SearXNG (Docker). Archer calls it server-side for results and suggestions...
    searxng_url: str = "http://127.0.0.1:8888"
    # ...and sends !bang searches to its page.
    results_url: str = "http://localhost:8888/search?q={q}"
    # A Docker container to `docker start` when Archer launches, so SearXNG is up before the first search.
    searxng_container: str = Field("", max_length=64, pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    # Address-bar suggestions must feel instant; give up rather than lag.
    suggest_timeout: float = Field(1.5, ge=0.3, le=5)
    # SafeSearch sent to SearXNG: 0 off, 1 moderate, 2 strict. Unset keeps the instance default.
    safesearch: int | None = Field(None, ge=0, le=2)
    # Search language sent to SearXNG, e.g. "en-IN" or "all". Empty keeps the instance default.
    search_language: str = Field("", max_length=12, pattern=r"^[A-Za-z]{0,3}(-[A-Za-z]{2})?$|^all$|^auto$")
    # Used for "weather" / "time" queries that name no place.
    home_location: str = "Bengaluru"
    # Fiat currency for crypto prices when the query names none.
    home_currency: str = "inr"
    # Wikimedia's bot policy requires contact info (email or URL) in the User-Agent.
    contact: str = "https://localhost; set ARCHER_CONTACT"
    # Country for "where to watch" and music embeds (ISO 3166-1 alpha-2).
    home_country: str = "IN"
    # TMDB API key (free) for country-specific streaming availability (JustWatch data).
    # Accepts a v3 key or a v4 read token. Read from TMDB_API_KEY or ARCHER_TMDB_API_KEY.
    tmdb_api_key: str = Field("", validation_alias=AliasChoices("TMDB_API_KEY", "ARCHER_TMDB_API_KEY"))
    # The AI provider. DeepSeek is recommended (cheap, cited answers); see ai_provider.PROVIDERS.
    ai_provider: Literal["deepseek", "openai", "anthropic", "ollama", "openrouter", "custom"] = "deepseek"
    deepseek_api_key: str = Field("", validation_alias=AliasChoices("DEEPSEEK_API_KEY", "ARCHER_DEEPSEEK_API_KEY"))
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    # Key for a custom OpenAI-compatible endpoint (optional; many local servers need none).
    ai_api_key: str = ""
    # Empty means the provider's own address and recommended model.
    ai_base_url: str = ""
    ai_model: str = Field("", max_length=120)
    # Ollama's context window. Its default is too small for Archer's evidence and silently truncates it.
    ollama_context: int = Field(16384, ge=2048, le=262144)
    ai_planning_rounds: int = Field(1, ge=0, le=2)
    ai_answer_tokens: int = Field(5000, ge=1500, le=8000)
    # Input budget for one research or AI Mode turn: sources plus the conversation so far. Older turns
    # are summarised and dropped oldest-first to fit; Ollama is further held to its context window.
    ai_context_tokens: int = Field(120_000, ge=16_000, le=1_000_000)
    ai_daily_tokens: int = Field(500_000, ge=1)
    ai_run_usd: float = Field(0.10, gt=0)
    ai_daily_usd: float = Field(1.0, gt=0)
    # Optional USD per million tokens. Unknown pricing retains conservative token caps.
    ai_input_price: float | None = Field(None, ge=0)
    ai_output_price: float | None = Field(None, ge=0)

    # Search history, kept only in data/history.sqlite3 on this computer.
    history: bool = True
    # Days of history to keep; 0 keeps everything until you clear it.
    history_days: int = Field(365, ge=0, le=3650)
    # Use history and site preferences to rank results, recall visited pages and suggest past searches.
    personalize: bool = True
    # How meaning is matched: a small local embedding model, a local Ollama model, or word matching only.
    semantic_model: Literal["potion-base-8M", "potion-retrieval-32M", "ollama", "lexical"] = "potion-base-8M"
    ollama_embed_model: str = Field("nomic-embed-text", max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")

    # What the launcher does when it starts Archer: stay in the tray, open the companion app, or open search.
    startup_open: Literal["none", "app", "search"] = "none"
    # Seconds to wait after Windows sign-in before starting, so Archer doesn't compete with sign-in.
    startup_delay: int = Field(0, ge=0, le=300)
    # A tray notification once Archer is ready after starting with Windows.
    startup_notify: bool = False

    @property
    def base_url(self) -> str:
        return f"http://{self.public_host}:{self.port}"

    @property
    def allowed_hosts(self) -> list[str]:
        # Host-header allowlist; rejects DNS-rebinding requests from other sites.
        return [self.public_host, "localhost", "127.0.0.1"]


settings = Settings()
