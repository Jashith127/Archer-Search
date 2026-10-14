"""Companion app: the /app settings window, the /app/welcome setup, and their guarded JSON API.

Reads return masked state only. Every change is a same-origin POST carrying the session's CSRF
token (the same guard as AI actions), so another website can't alter Archer's configuration.
"""
import asyncio
import os
import sys
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from archer import __version__, companion, websearch
from archer.ai_provider import PROVIDERS, AIError, readiness, resolve
from archer.ai_routes import guard, service as ai_service, set_cookie
from archer.config import DATA_DIR, ROOT, settings
from archer.settings_service import SECTIONS_ORDER, SPECS, SettingsError, SettingsService, apply_runtime, check_connection

router = APIRouter()


def store(request) -> SettingsService:
    if not hasattr(request.app.state, "settings_service"):
        request.app.state.settings_service = SettingsService()
    return request.app.state.settings_service


def autostart_state():
    if sys.platform != "win32":
        return {"available": False, "enabled": False}
    from archer import autostart
    try:
        return {"available": True, "enabled": autostart.enabled()}
    except OSError:
        return {"available": False, "enabled": False}


def usage(ai):
    try:
        state = ai.budget.load()
    except AIError:
        return {"error": "Archer couldn't read its usage ledger (data/ai-usage.json)."}
    return {"day": state["day"], "tokens": state["tokens"], "usd": round(state["usd"], 4),
            "token_limit": settings.ai_daily_tokens, "usd_limit": settings.ai_daily_usd, "priced": ai.budget.priced}


def providers():
    """Readiness of the live search and AI providers, for the overview and setup."""
    ai_ready, ai_missing = readiness(settings)
    r = resolve(settings)
    search_ready, search_missing = websearch.configured()
    return {
        "search": {"id": settings.search_provider, "label": websearch.LABELS[settings.search_provider],
                   "ready": search_ready, "missing": search_missing, "fallback": settings.search_fallback,
                   "suggest": websearch.suggestion_source()},
        "ai": {"id": r.provider, "label": r.info.label, "model": r.model, "ready": ai_ready, "missing": ai_missing,
               "defaults": {name: {"model": info.model, "base": info.base, "models": list(info.models), "key": info.key}
                            for name, info in PROVIDERS.items()}},
    }


def state(request):
    ai, service = ai_service(request), store(request)
    saved = service.stored()
    return {
        "fields": service.fields(),
        "providers": providers(),
        "app": {"onboarded": companion.onboarded(), "browsers": companion.browsers() if sys.platform == "win32" else [],
                "docker": companion.docker_available()},
        "ai": {"consent": ai.consent, "mode": ai.mode, "configured": readiness(settings)[0]},
        "autostart": autostart_state(),
        "usage": usage(ai),
        "system": {
            "version": __version__, "data_dir": str(DATA_DIR), "folder": str(ROOT),
            "running_url": settings.base_url, "saved_url": f"http://{saved.public_host}:{saved.port}",
            "restart_available": callable(getattr(request.app.state, "restart", None)),
            "restart_pending": service.restart_pending,
            "conversations": sum(1 for r in ai.runs.values()),
        },
    }


def page_context(request):
    ai = ai_service(request)
    session = ai.session(request.cookies.get("archer_session"))
    intro = {key: (title, text, [s for s in SPECS if s.section == key]) for key, title, text in SECTIONS_ORDER}
    current = state(request)
    return session, {"intro": intro, "items": {f["name"]: f for f in current["fields"]}, "state": current, "csrf": session.csrf,
        "ai_providers": PROVIDERS, "search_labels": websearch.LABELS,
        "search_url": f"{settings.base_url}/search?q=%s", "suggest_url": f"{settings.base_url}/suggest?q=%s",
        "fallback_search_url": f"http://127.0.0.1:{settings.port}/search?q=%s"}


@router.get("/api/settings")
async def read_settings(request: Request):
    return JSONResponse(state(request), headers={"Cache-Control": "no-store"})


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Values(Input):
    values: dict[str, str | int | float | None] = Field(max_length=40)


class Test(Input):
    target: Literal["ai", "tmdb", "searxng", "duckduckgo", "serper", "google", "brave"]
    value: str = Field(default="", max_length=500)
    base: str = Field(default="", max_length=300)
    provider: Literal["", "deepseek", "openai", "anthropic", "ollama", "openrouter", "custom"] = ""
    model: str = Field(default="", max_length=120)
    extra: str = Field(default="", max_length=120)


class Browser(Input):
    browser: Literal["helium", "chrome", "edge", "brave", "vivaldi", "opera", "firefox"]


class Toggle(Input):
    enabled: bool


class Open(Input):
    target: Literal["data", "folder"]


@router.post("/api/settings")
async def save_settings(request: Request, body: Values):
    guard(request, ai_service(request))
    try:
        result = store(request).apply(body.values)
    except SettingsError as exc:
        return JSONResponse({"errors": exc.errors}, status_code=422)
    except OSError:
        raise HTTPException(500, "Archer couldn't write its .env file. Check that the folder is writable.")
    apply_runtime(request.app, result["changed"])
    return JSONResponse({**result, "state": state(request)})


@router.post("/api/settings/test")
async def test_settings(request: Request, body: Test):
    guard(request, ai_service(request))
    return JSONResponse(await check_connection(request.app.state.ext, body.target, body.value.strip(), body.base.strip(),
                                               body.provider, body.model.strip(), body.extra.strip()))


@router.post("/api/app/onboarded")
async def finish_setup(request: Request, body: Toggle):
    guard(request, ai_service(request))
    try:
        companion.set_onboarded(body.enabled)
    except OSError:
        raise HTTPException(500, "Archer couldn't write to its data folder.")
    return JSONResponse({"onboarded": body.enabled})


@router.post("/api/app/browser")
async def browser_settings(request: Request, body: Browser):
    """Open a detected browser at its search-engine settings page (a fixed page, never a client URL)."""
    guard(request, ai_service(request))
    if sys.platform != "win32" or not companion.open_search_settings(body.browser):
        raise HTTPException(400, "That browser isn't installed where Archer can find it. Open its settings yourself.")
    return JSONResponse({"opened": body.browser})


@router.post("/api/settings/autostart")
async def set_autostart(request: Request, body: Toggle):
    guard(request, ai_service(request))
    if not autostart_state()["available"]:
        raise HTTPException(400, "Start with Windows is only available on Windows.")
    from archer import autostart
    try:
        autostart.set_enabled(body.enabled)
    except OSError:
        raise HTTPException(500, "Windows didn't allow Archer to change its startup entry.")
    return JSONResponse(autostart_state())


@router.post("/api/settings/open")
async def open_folder(request: Request, body: Open):
    guard(request, ai_service(request))
    path = DATA_DIR if body.target == "data" else ROOT
    path.mkdir(exist_ok=True)
    if sys.platform != "win32":
        raise HTTPException(400, "Opening folders is only available on Windows. The path is " + str(path))
    os.startfile(path)  # noqa: S606 — a fixed, local folder; never a client-supplied path
    return JSONResponse({"opened": str(path)})


@router.post("/api/settings/conversations/clear")
async def clear_conversations(request: Request):
    guard(request, ai_service(request))
    ai = ai_service(request)
    for run in ai.runs.values():
        if not run.done and run.task:
            run.task.cancel()
    ai.runs.clear()
    ai.snapshots.clear()
    return JSONResponse({"cleared": True})


@router.post("/api/settings/restart")
async def restart(request: Request):
    guard(request, ai_service(request))
    restart_app = getattr(request.app.state, "restart", None)
    if not callable(restart_app):
        raise HTTPException(400, "Restart Archer from its tray icon, or stop and start it again.")
    # Answer first; the launcher stops the server and starts a fresh process.
    asyncio.get_running_loop().call_later(0.4, restart_app)
    saved = store(request).stored()
    return JSONResponse({"restarting": True, "url": f"http://{saved.public_host}:{saved.port}/app"})
