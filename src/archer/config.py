from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARCHER_", env_file=ROOT / ".env", extra="ignore"
    )

    # Bind to loopback only: Archer is a single-machine engine.
    host: str = "127.0.0.1"
    port: int = 4747
    # Chromium resolves *.localhost to loopback, so no hosts-file edit is needed.
    public_host: str = "archer.localhost"
    # Local SearXNG (Docker). Archer calls it server-side for suggestions...
    searxng_url: str = "http://127.0.0.1:8888"
    # ...and, until Archer renders its own results page, sends the browser here.
    results_url: str = "http://localhost:8888/search?q={q}"
    # Address-bar suggestions must feel instant; give up rather than lag.
    suggest_timeout: float = 1.5
    # Used for "weather" / "time" queries that name no place.
    home_location: str = "Bengaluru"
    # Fiat currency for crypto prices when the query names none.
    home_currency: str = "inr"
    # Wikimedia's bot policy requires contact info (email or URL) in the User-Agent.
    contact: str = "https://localhost; set ARCHER_CONTACT"

    @property
    def base_url(self) -> str:
        return f"http://{self.public_host}:{self.port}"

    @property
    def allowed_hosts(self) -> list[str]:
        # Host-header allowlist; rejects DNS-rebinding requests from other sites.
        return [self.public_host, "localhost", "127.0.0.1"]


settings = Settings()
