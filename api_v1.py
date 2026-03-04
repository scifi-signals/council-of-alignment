"""Council of Alignment — API v1 (JSON REST with API key auth)."""

import os
import hmac
import uuid
import logging

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from starlette.requests import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import MODELS, get_council_models, BASE_URL
from database import get_db
from council_pipeline import run_council_review, acquire_lock, release_lock, is_locked

limiter = Limiter(key_func=get_remote_address)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

COUNCIL_API_KEY = os.getenv("COUNCIL_API_KEY", "")


# ─── Auth dependency ──────────────────────────────────────────

async def require_api_key(request: Request) -> None:
    """Validate Bearer token against COUNCIL_API_KEY."""
    if not COUNCIL_API_KEY:
        raise HTTPException(503, "API key not configured on server")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing Authorization: Bearer <key> header")
    token = auth[7:]
    if not hmac.compare_digest(token, COUNCIL_API_KEY):
        raise HTTPException(403, "Invalid API key")


# ─── Helpers ──────────────────────────────────────────────────

def _get_shared(request: Request):
    """Get shared instances from app.state."""
    app = request.app
    return app.state.sm, app.state.dispatcher, app.state.tracker, app.state.github_ctx


def _session_url(session_id: str) -> str:
    return f"{BASE_URL}/session/{session_id}"


# ─── Health (no auth) ─────────────────────────────────────────

@router.get("/health")
async def health():
    return JSONResponse({"status": "ok", "version": "v1"})


# ─── Sessions ─────────────────────────────────────────────────

@router.post("/sessions", dependencies=[Depends(require_api_key)])
async def create_session(request: Request):
    """Create a new Council review session."""
    sm, dispatcher, tracker, github_ctx = _get_shared(request)
    body = await request.json()

    title = body.get("title", "Untitled Review")[:200]
    lead_model = body.get("lead_model", "claude")

    if lead_model not in MODELS:
        raise HTTPException(400, "Invalid lead_model")

    council_models = get_council_models(lead_model)
    session = await sm.create_session(title, lead_model, user_id=None)

    return JSONResponse({
        "session_id": session["id"],
        "title": session["title"],
        "lead_model": lead_model,
        "council_models": council_models,
        "web_url": _session_url(session["id"]),
    })


@router.get("/sessions", dependencies=[Depends(require_api_key)])
async def list_sessions(request: Request):
    """List recent sessions."""
    sm, *_ = _get_shared(request)
    sessions = await sm.list_sessions()
    return JSONResponse({
        "sessions": [
            {
                "id": s["id"],
                "title": s["title"],
                "status": s.get("status", "designing"),
                "created_at": s["created_at"],
                "web_url": _session_url(s["id"]),
            }
            for s in sessions
        ]
    })


# ─── Files ────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/files", dependencies=[Depends(require_api_key)])
async def add_files(session_id: str, request: Request):
    """Attach source files to a session."""
    sm, *_ = _get_shared(request)

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    body = await request.json()
    files = body.get("files", [])
    if not files:
        raise HTTPException(400, "No files provided")
    if len(files) > 100:
        raise HTTPException(400, "Too many files (max 100)")

    MAX_FILE_CONTENT = 500 * 1024  # 500KB per file

    db = await get_db()
    try:
        for f in files:
            filename = f.get("filename", "unnamed")[:500]
            content = f.get("content", "")
            if len(content) > MAX_FILE_CONTENT:
                continue  # skip oversized files
            await db.execute(
                "INSERT INTO attachments (id, session_id, filename, content, size_bytes) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex[:8], session_id, filename, content, len(content)),
            )
        await db.commit()
    finally:
        await db.close()

    return JSONResponse({
        "attached": len(files),
        "filenames": [f.get("filename", "unnamed") for f in files],
    })


# ─── Message ──────────────────────────────────────────────────

@router.post("/sessions/{session_id}/message", dependencies=[Depends(require_api_key)])
async def send_message(session_id: str, request: Request):
    """Send a message to the Lead AI."""
    sm, dispatcher, tracker, github_ctx = _get_shared(request)

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    body = await request.json()
    message = body.get("message", "")
    if not message:
        raise HTTPException(400, "Empty message")

    from chat_engine import ChatEngine
    from github_context import GitHubContextProvider
    engine = ChatEngine(dispatcher, github_ctx)
    result = await engine.send_message(session_id, message)

    return JSONResponse({
        "response": result["response"],
        "verified": result.get("verified", False),
    })


# ─── Convene ──────────────────────────────────────────────────

@router.post("/sessions/{session_id}/convene", dependencies=[Depends(require_api_key)])
@limiter.limit("3/minute")
async def convene(session_id: str, request: Request):
    """Run a full Council review. Blocks 3-5 minutes."""
    sm, dispatcher, tracker, github_ctx = _get_shared(request)

    if not acquire_lock(session_id):
        raise HTTPException(409, "Council already in session for this review")

    session = await sm.get_session(session_id)
    if not session:
        release_lock(session_id)
        raise HTTPException(404, "Session not found")

    try:
        result = await run_council_review(
            session_id, session, sm, dispatcher, tracker, github_ctx
        )
        result["web_url"] = _session_url(session_id)
        return JSONResponse(result)
    finally:
        release_lock(session_id)


# ─── Results ──────────────────────────────────────────────────

@router.get("/sessions/{session_id}/results", dependencies=[Depends(require_api_key)])
async def get_results(session_id: str, request: Request):
    """Get the latest synthesis and reviews for a session."""
    sm, *_ = _get_shared(request)

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    synthesis = await sm.get_latest_synthesis(session_id)
    if not synthesis:
        raise HTTPException(404, "No review results yet")

    # Get the round number from synthesis context
    round_number = await sm.get_round_number(session_id) - 1
    reviews_list = await sm.get_reviews(session_id, round_number)
    reviews = {r["model_name"]: {"content": r["response"]} for r in reviews_list}

    return JSONResponse({
        "session_id": session_id,
        "round_number": round_number,
        "reviews": reviews,
        "synthesis": synthesis,
        "web_url": _session_url(session_id),
    })


# ─── Decide ───────────────────────────────────────────────────

@router.post("/sessions/{session_id}/decide", dependencies=[Depends(require_api_key)])
async def decide(session_id: str, request: Request):
    """Accept/reject proposed changes."""
    sm, dispatcher, tracker, github_ctx = _get_shared(request)

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    body = await request.json()
    decisions = body.get("decisions", [])
    if not decisions:
        raise HTTPException(400, "No decisions provided")

    synthesis = await sm.get_latest_synthesis(session_id)
    if not synthesis:
        raise HTTPException(400, "No synthesis found")

    changes = {c["id"]: c for c in synthesis.get("proposed_changes", [])}
    round_number = await sm.get_round_number(session_id) - 1

    accepted = []
    rejected = []

    for decision in decisions:
        change_id = decision.get("id")
        is_accepted = decision.get("accepted")
        if not change_id or is_accepted is None:
            continue
        reason = decision.get("reason", "")

        change = changes.get(change_id)
        if not change:
            continue

        await sm.save_changelog_entry(session_id, round_number, change, is_accepted, reason)
        await tracker.record_decision(change_id, is_accepted)

        if is_accepted:
            change["rejection_reason"] = None
            accepted.append(change)
        else:
            change["rejection_reason"] = reason
            rejected.append(change)

    # Inject synthesis into Lead conversation
    lead_response = ""
    if accepted:
        from chat_engine import ChatEngine
        engine = ChatEngine(dispatcher, github_ctx)
        lead_response = await engine.inject_synthesis(session_id, synthesis, accepted, rejected)

    return JSONResponse({
        "accepted": len(accepted),
        "rejected": len(rejected),
        "lead_response": lead_response,
    })
