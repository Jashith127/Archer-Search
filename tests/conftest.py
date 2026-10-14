"""Pin provider settings, so tests never depend on what the real .env chose."""
import pytest

from archer.config import settings


@pytest.fixture(autouse=True)
def default_providers(monkeypatch):
    monkeypatch.setattr(settings, "search_provider", "searxng")
    monkeypatch.setattr(settings, "search_fallback", False)
    monkeypatch.setattr(settings, "suggest_provider", "auto")
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(settings, "ai_model", "")
    monkeypatch.setattr(settings, "ai_base_url", "")
