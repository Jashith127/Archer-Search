"""Pin provider settings, so tests never depend on what the real .env chose."""
import pytest

from archer import history
from archer.config import settings


@pytest.fixture(autouse=True)
def default_providers(monkeypatch):
    monkeypatch.setattr(settings, "search_provider", "searxng")
    monkeypatch.setattr(settings, "search_fallback", False)
    monkeypatch.setattr(settings, "suggest_provider", "auto")
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(settings, "ai_model", "")
    monkeypatch.setattr(settings, "ai_base_url", "")


@pytest.fixture(autouse=True)
def private_history(monkeypatch, tmp_path):
    """History goes to a throwaway database, never data/history.sqlite3, and matching stays lexical
    (no model download)."""
    from archer.app import app
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "history.sqlite3")
    monkeypatch.setattr(settings, "semantic_model", "lexical")
    monkeypatch.setattr(settings, "history", True)
    monkeypatch.setattr(settings, "personalize", True)
    for name in ("history", "personal"):
        if hasattr(app.state, name):
            delattr(app.state, name)
    yield
    store = getattr(app.state, "history", None)
    if store is not None:
        store.close()
        delattr(app.state, "history")
    if hasattr(app.state, "personal"):
        delattr(app.state, "personal")
