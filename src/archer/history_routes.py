"""History JSON API: recording opened results, recent searches, deleting, clearing, site preferences.

Recording a click needs only a same-origin request (results pages send it with `keepalive` as you
leave, so it can't carry the CSRF header reliably and reveals nothing). Everything that reads a
list or changes what is stored is guarded like the AI and settings POSTs.
"""
import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from archer.ai_routes import guard, service as ai_service
from archer.config import settings
from archer.history import History
from archer.personalize import Personalizer

router = APIRouter(prefix="/api/history")


def history(request) -> History:
    if not hasattr(request.app.state, "history"):
        request.app.state.history = History()
    return request.app.state.history


def personalizer(request) -> Personalizer:
    if not hasattr(request.app.state, "personal"):
        request.app.state.personal = Personalizer(history(request))
    return request.app.state.personal


def same_origin(request) -> None:
    if request.headers.get("sec-fetch-site") != "same-origin":
        raise HTTPException(403, "History is only available to Archer's own pages.")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Click(Input):
    search: int | None = None
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")
    title: str = Field(default="", max_length=300)
    snippet: str = Field(default="", max_length=500)
    position: int = Field(default=0, ge=0, le=200)


class Forget(Input):
    q: str = Field(min_length=1, max_length=300)


class Delete(Input):
    searches: list[int] = Field(default_factory=list, max_length=200)
    clicks: list[int] = Field(default_factory=list, max_length=200)


class Clear(Input):
    range: Literal["hour", "day", "week", "all"]


class Site(Input):
    host: str = Field(min_length=3, max_length=253, pattern=r"^[A-Za-z0-9.-]+$")
    pref: Literal["raise", "lower", "block"] | None = None


@router.post("/click")
async def click(request: Request, body: Click):
    same_origin(request)
    if not settings.history:
        return JSONResponse({"recorded": False})
    store = history(request)
    await asyncio.to_thread(store.record_click, body.search, body.url, body.title, body.snippet, body.position)
    return JSONResponse({"recorded": True})


@router.get("/recent")
async def recent(request: Request, q: str = Query("", max_length=300)):
    """Past searches for the search box's dropdown: recent ones on focus, matching ones while typing."""
    same_origin(request)
    if not (settings.history and settings.personalize):
        return JSONResponse({"items": []}, headers={"Cache-Control": "no-store"})
    items = history(request).recent(limit=6 if not q.strip() else 3, prefix=q)
    return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})


@router.post("/forget")
async def forget(request: Request, body: Forget):
    guard(request, ai_service(request))
    return JSONResponse({"removed": history(request).delete_query(body.q)})


@router.post("/delete")
async def delete(request: Request, body: Delete):
    guard(request, ai_service(request))
    store = history(request)
    removed = sum(store.delete_search(i) for i in body.searches) + sum(store.delete_click(i) for i in body.clicks)
    store.drop_orphan_vectors()
    return JSONResponse({"removed": removed})


@router.post("/clear")
async def clear(request: Request, body: Clear):
    guard(request, ai_service(request))
    since = None if body.range == "all" else time.time() - {"hour": 3600, "day": 86400, "week": 7 * 86400}[body.range]
    await asyncio.to_thread(history(request).clear, since)
    return JSONResponse({"cleared": body.range, "stats": history(request).stats()})


@router.post("/site")
async def site(request: Request, body: Site):
    guard(request, ai_service(request))
    history(request).set_site(body.host, body.pref)
    return JSONResponse({"host": body.host.lower().removeprefix("www."), "pref": body.pref})
