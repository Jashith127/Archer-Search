from pathlib import Path

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
    # Local SearXNG (Docker). Archer calls it server-side for suggestions...
    searxng_url: str = "http://127.0.0.1:8888"
    # ...and, until Archer renders its own results page, sends the browser here.
    results_url: str = "http://localhost:8888/search?q={q}"
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
    deepseek_api_key: str = Field("", validation_alias=AliasChoices("DEEPSEEK_API_KEY", "ARCHER_DEEPSEEK_API_KEY"))
    ai_base_url: str = "https://api.deepseek.com"
    ai_model: str = "deepseek-flash"
    ai_planning_rounds: int = Field(1, ge=0, le=2)
    ai_answer_tokens: int = Field(5000, ge=1500, le=8000)
    ai_daily_tokens: int = Field(500_000, ge=1)
    ai_run_usd: float = Field(0.10, gt=0)
    ai_daily_usd: float = Field(1.0, gt=0)
    # Optional USD per million tokens. Unknown pricing retains conservative token caps.
    ai_input_price: float | None = Field(None, ge=0)
    ai_output_price: float | None = Field(None, ge=0)

    @property
    def base_url(self) -> str:
        return f"http://{self.public_host}:{self.port}"

    @property
    def allowed_hosts(self) -> list[str]:
        # Host-header allowlist; rejects DNS-rebinding requests from other sites.
        return [self.public_host, "localhost", "127.0.0.1"]


settings = Settings()
