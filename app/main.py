"""HTTP API for the tutor."""

from __future__ import annotations

import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db, safety, tutor
from .profile import Profile
from .ratelimit import RateLimiter, visitor_key

log = logging.getLogger("tutor")
STATIC = Path(__file__).parent / "static"
MAX_TAGS = 10
MAX_TAG_CHARS = 60
MAX_MESSAGE_CHARS = 2000
MAX_BODY_BYTES = 64 * 1024
HISTORY_FETCH = 40  # rows read from SQLite; tutor.trim_history then applies the token budget
limiter = RateLimiter()
budget = tutor.Budget()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="SAT Tutor", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class BodyLimit:
    """Reject request bodies over MAX_BODY_BYTES before they are read into memory."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and int(length) > MAX_BODY_BYTES:
            return await _too_large(send)
        seen = 0

        async def limited():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > MAX_BODY_BYTES:
                raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited, send)
        except _BodyTooLarge:
            await _too_large(send)


class _BodyTooLarge(Exception):
    pass


async def _too_large(send) -> None:
    body = json.dumps({"detail": "Request body is too large."}).encode()
    await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


app.add_middleware(BodyLimit)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field errors without echoing the submitted input back."""
    errors = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
    first = errors[0]["msg"].removeprefix("Value error, ") if errors else "Invalid request."
    return JSONResponse({"detail": first, "errors": errors}, status_code=422)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors https://huggingface.co https://*.hf.space"
    )
    return response


def _clean_tags(tags: list[str]) -> list[str]:
    seen, out = set(), []
    for t in tags:
        t = tutor.fence_safe(t)  # one line, no angle brackets
        if not t:
            continue
        if len(t) > MAX_TAG_CHARS:
            raise ValueError(f"each tag must be at most {MAX_TAG_CHARS} characters")
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


class StudentIn(BaseModel):
    name: str = Field(max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    profile: Profile = Field(default_factory=Profile)

    @field_validator("name")
    @classmethod
    def name_present(cls, v: str) -> str:
        v = tutor.fence_safe(v)
        if not v:
            raise ValueError("name is required")
        return v

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, v: list[str]) -> list[str]:
        return _clean_tags(v)


class TagsIn(BaseModel):
    tags: list[str] = Field(max_length=MAX_TAGS)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, v: list[str]) -> list[str]:
        return _clean_tags(v)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message is empty")
        return v.strip()


def current_student(student_id: str, authorization: Annotated[str | None, Header()] = None) -> dict:
    """Resolve the student and check the bearer token issued when the session was created."""
    student = db.get_student(student_id)
    token = (authorization or "").removeprefix("Bearer ").strip()
    # Same 404 for "no such student" and "wrong token", so ids cannot be probed.
    if student is None or not token or not hmac.compare_digest(db.hash_token(token), student["token_hash"]):
        raise HTTPException(404, "session not found")
    return student


Student = Annotated[dict, Depends(current_student)]


def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("ADMIN_TOKEN", "")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(404)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": "demo" if tutor.demo_mode() else "live",
        "model": tutor.model(),
        "moderation": "off" if tutor.demo_mode() else safety.MODERATION_MODEL,
        "pricing": "known" if tutor.price() else "missing",
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/students", status_code=201)
def create_student(body: StudentIn, request: Request) -> dict:
    if not limiter.allow_session(visitor_key(request)):
        raise HTTPException(429, "Too many new sessions from this address. Please try again later.")
    return db.create_student(body.name, body.tags, body.profile.model_dump(mode="json", exclude_defaults=True))


@app.put("/students/{student_id}/tags")
def update_tags(body: TagsIn, student: Student) -> dict:
    db.set_tags(student["pk"], body.tags)
    return {"id": student["id"], "tags": body.tags}


@app.put("/students/{student_id}/profile")
def update_profile(body: Profile, student: Student) -> dict:
    data = body.model_dump(mode="json", exclude_defaults=True)
    db.set_profile(student["pk"], data)
    return {"id": student["id"], "profile": data}


@app.get("/students/{student_id}/history")
def get_history(student: Student) -> dict:
    return {"id": student["id"], "messages": db.history(student["pk"], limit=200)}


@app.delete("/students/{student_id}", status_code=204)
def delete_student(student: Student) -> Response:
    db.delete_student(student["pk"])
    return Response(status_code=204)


def _in_care(student: dict) -> bool:
    until = student.get("care_until")
    return bool(until) and datetime.fromisoformat(until) > datetime.now(timezone.utc)


def _preflight(body: ChatIn, student: dict, request: Request) -> JSONResponse | tuple[list[dict], float]:
    """Checks shared by both chat endpoints.

    Returns a response to send now, or (prompt messages, reserved budget). A caller that
    gets a reservation must release it once the call has finished.
    """
    ok, why = limiter.allow_chat(visitor_key(request))
    if not ok:
        raise HTTPException(429, why)
    if not tutor.demo_mode() and tutor.price() is None:
        log.error("no price for model %s; set TUTOR_PRICE_IN and TUTOR_PRICE_OUT", tutor.model())
        raise HTTPException(503, "The tutor is not configured yet. Please try again later.")

    # Care mode: after a crisis flag, follow-ups get support, never tutoring, for a while.
    if _in_care(student):
        return JSONResponse({"reply": safety.CARE_REPLY, "safety": True})
    check = safety.screen(body.message, None if tutor.demo_mode() else tutor.client())
    if check.flagged:
        db.log_safety_event(check.source, check.categories)
        until = datetime.now(timezone.utc) + timedelta(minutes=safety.care_minutes())
        db.set_care_until(student["pk"], until.isoformat())
        safety.alert_mentor(student["id"], check.source)
        return JSONResponse({"reply": safety.SAFETY_REPLY, "safety": True})

    past = db.history(student["pk"], limit=HISTORY_FETCH)
    messages = tutor.build_messages(student["name"], student["tags"], past, body.message, student["profile"])

    limit = float(os.getenv("DAILY_BUDGET_USD", "2.0"))
    spent = db.spend_on(db.today())
    reserve = tutor.worst_case_usd(messages)
    if not budget.reserve(spent, limit, reserve):
        log.error("daily budget of $%.2f reached ($%.4f spent); chat paused", limit, spent)
        raise HTTPException(503, "The demo has used today's budget. Please come back tomorrow.")
    if spent >= 0.8 * limit:
        log.warning("80%% of the daily budget used ($%.4f of $%.2f)", spent, limit)
    return messages, reserve


def _log_usage(usage: tutor.Usage) -> None:
    db.log_usage(tutor.model(), usage.tokens_in, usage.tokens_out, usage.usd)


@app.post("/students/{student_id}/chat")
def chat(body: ChatIn, student: Student, request: Request):
    prepared = _preflight(body, student, request)
    if isinstance(prepared, JSONResponse):
        return prepared
    messages, reserved = prepared
    try:
        text, usage = tutor.reply(messages)
        db.add_turn(student["pk"], body.message, text)
        _log_usage(usage)  # spend is logged before the reservation is released, so no gap
    except tutor.TutorUnavailable as exc:
        log.error("model call failed: %s", exc)
        raise HTTPException(503, "The tutor is unavailable right now. Please try again in a minute.") from exc
    finally:
        budget.release(reserved)
    return {"reply": text, "safety": False}


@app.post("/students/{student_id}/chat/stream")
def chat_stream(body: ChatIn, student: Student, request: Request):
    """Server-sent events: `delta` events with text, then one `done` (or `error`) event."""
    prepared = _preflight(body, student, request)
    if isinstance(prepared, JSONResponse):
        return prepared
    messages, reserved = prepared

    def events():
        done: dict = {}
        try:
            for delta in tutor.stream(messages, done):
                yield f"event: delta\ndata: {json.dumps(delta)}\n\n"
            db.add_turn(student["pk"], body.message, done["text"])
            _log_usage(done["usage"])
            yield "event: done\ndata: {}\n\n"
        except tutor.TutorUnavailable as exc:
            log.error("model stream failed: %s", exc)
            yield f"event: error\ndata: {json.dumps('The tutor is unavailable right now.')}\n\n"
        finally:
            if "usage" not in done and done.get("parts"):
                # The client left mid-stream. The provider still bills what was generated, so log an estimate.
                partial = "".join(done["parts"])
                prompt = sum(tutor.estimate_tokens(m["content"]) for m in messages)
                _log_usage(tutor.Usage(prompt, tutor.estimate_tokens(partial)))
            budget.release(reserved)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/admin/usage", dependencies=[Depends(require_admin)])
def usage() -> dict:
    budget = float(os.getenv("DAILY_BUDGET_USD", "2.0"))
    return {
        "today_usd": round(db.spend_on(db.today()), 6),
        "daily_budget_usd": budget,
        "by_day": db.usage_by_day(),
        "safety_events_by_day": db.safety_by_day(),
        "rate_limiter_keys": len(limiter),
    }
