"""AI HTTP boundary: same-origin POSTs, CSRF, owner-scoped runs and replayable streams."""
import asyncio
import json
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from archer.ai_provider import AIError
from archer.ai_service import AIService
from archer.config import DATA_DIR, settings

router = APIRouter(prefix="/api/ai")


def service(request):
    if not hasattr(request.app.state, "ai"):
        request.app.state.ai = AIService(settings, DATA_DIR)
    ai = request.app.state.ai
    ai.searx, ai.ext = request.app.state.searx, request.app.state.ext
    return ai


def set_cookie(response, session):
    response.set_cookie("archer_session", session.id, httponly=True, samesite="strict", max_age=1800)
    response.headers["Cache-Control"] = "no-store"


def guard(request, ai):
    ai.prune()
    session = ai.sessions.get(request.cookies.get("archer_session"))
    if request.headers.get("sec-fetch-site") != "same-origin" or request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "AI actions require an Archer page on the same origin.")
    if not session or not secrets.compare_digest(request.headers.get("x-archer-csrf", ""), session.csrf):
        raise HTTPException(403, "This browser session expired. Reload Archer and try again.")
    ai.session(session.id)
    return session


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Preferences(Input):
    consent: bool
    mode: Literal["classic", "balanced"]


class Generate(Input):
    snapshot: str = Field(min_length=1, max_length=100)
    depth: Literal["compact", "research"] = "compact"
    automatic: bool = False
    parent: str = Field(default="", max_length=100)
    question: str = Field(default="", max_length=600)
    selected: list[str] = Field(default_factory=list, max_length=4)
    regenerate: bool = False


@router.get("/session")
async def session_info(request: Request):
    ai = service(request)
    session = ai.session(request.cookies.get("archer_session"))
    response = JSONResponse(ai.info(session))
    set_cookie(response, session)
    return response


@router.post("/preferences")
async def preferences(request: Request, body: Preferences):
    ai = service(request)
    session = guard(request, ai)
    try:
        ai.preferences(body.consent, body.mode)
    except AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not body.consent:
        for run in ai.runs.values():
            if not run.done and run.task:
                run.task.cancel()
    response = JSONResponse(ai.info(session))
    set_cookie(response, session)
    return response


@router.post("/runs")
async def generate(request: Request, body: Generate):
    ai = service(request)
    session = guard(request, ai)
    try:
        run = ai.start(session, body.snapshot, body.depth, body.automatic, body.parent,
            body.question.strip(), body.selected, body.regenerate)
    except AIError as exc:
        raise HTTPException(400, str(exc)) from exc

    async def stream():
        cursor = 0
        run.watchers += 1
        try:
            while True:
                changed = run.changed        # taken before draining, so nothing emitted after is missed
                while cursor < len(run.events):
                    yield json.dumps(run.events[cursor], ensure_ascii=False) + "\n"
                    cursor += 1
                if run.done:
                    break
                if await request.is_disconnected():
                    break
                try:
                    # Wakes as soon as the run emits; the timeout only paces the disconnect check.
                    await asyncio.wait_for(changed.wait(), 0.5)
                except asyncio.TimeoutError:
                    pass
        finally:
            run.watchers -= 1
            if not run.watchers and not run.done and run.task:
                run.task.cancel()
    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"})


@router.post("/runs/{run_id}/cancel")
async def cancel(request: Request, run_id: str):
    ai = service(request)
    session = guard(request, ai)
    run = ai.runs.get(run_id)
    if not run or run.owner != session.id:
        raise HTTPException(404, "Run not found.")
    if not run.done and run.task:
        run.task.cancel()
    return JSONResponse({"stopped": True})


@router.get("/runs/{run_id}")
async def cached_run(request: Request, run_id: str):
    ai = service(request)
    ai.prune()
    owner = ai.sessions.get(request.cookies.get("archer_session"))
    run = ai.runs.get(run_id)
    if not owner or not run or run.owner != owner.id or not run.done:
        raise HTTPException(404, "Completed research not found.")
    chain, current = [], run
    while current and len(chain) < 8 and current.owner == owner.id:
        chain.append({"question": current.question, "depth": current.depth, "events": current.events})
        current = ai.runs.get(current.parent)
    return JSONResponse({"snapshot": run.snapshot.id, "events": run.events, "question": run.question,
        "turns": list(reversed(chain))},
        headers={"Cache-Control": "no-store"})
