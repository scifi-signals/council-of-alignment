"""Council of Alignment — FastAPI web application."""

import os
import io
import json
import uuid
import asyncio
import logging
import zipfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from config import MODELS, get_council_models, GITHUB_TOKEN, SESSION_SECRET, GITHUB_CLIENT_ID
from database import init_db, get_db
from dispatcher import ModelDispatcher
from chat_engine import ChatEngine
from briefing_generator import generate_briefing
from synthesis_engine import synthesize_reviews
from session_manager import SessionManager
from reviewer_tracker import ReviewerTracker
from attachment_context import build_attachment_context
from file_manager import FileManager
from github_context import GitHubContextProvider, parse_repo_url
from council_pipeline import run_council_review, acquire_lock, release_lock
from api_v1 import router as api_v1_router
from auth import (
    github_login_url, exchange_code_for_token, fetch_github_user,
    get_or_create_user, get_current_user, require_auth, require_auth_api,
    generate_state,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Council of Alignment", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=30 * 24 * 60 * 60)

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Shared instances
dispatcher = ModelDispatcher()
sm = SessionManager()
tracker = ReviewerTracker()
fm = FileManager()
github_ctx = GitHubContextProvider()

# Expose shared instances for API v1 router
app.state.sm = sm
app.state.dispatcher = dispatcher
app.state.tracker = tracker
app.state.github_ctx = github_ctx

# Mount API v1 router
app.include_router(api_v1_router)


def get_engine():
    return ChatEngine(dispatcher, github_ctx)


# ─── Template helpers ────────────────────────────────────────

async def _ctx(request: Request, **kwargs):
    """Build template context with common data."""
    user = await get_current_user(request)
    return {"request": request, "models": MODELS, "user": user, **kwargs}


# ─── Pages ───────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = await get_current_user(request)
    if user:
        sessions = await sm.list_sessions(user_id=user["id"])
    else:
        sessions = []
    return templates.TemplateResponse("home.html", await _ctx(request, sessions=sessions))


@app.get("/new", response_class=HTMLResponse)
async def new_session_page(request: Request):
    user = await get_current_user(request)
    if not user:
        request.session["oauth_next"] = "/new"
        return RedirectResponse("/auth/login", status_code=302)
    return templates.TemplateResponse("new.html", await _ctx(request))


@app.post("/new")
async def create_session(
    request: Request,
    title: str = Form(...),
    lead: str = Form(...),
):
    user_id = await require_auth_api(request)
    session = await sm.create_session(title, lead, user_id=user_id)
    return RedirectResponse(f"/session/{session['id']}", status_code=303)


@app.get("/session/{session_id}", response_class=HTMLResponse)
async def session_page(request: Request, session_id: str):
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    # Load all data for the session view
    db = await get_db()
    try:
        # Messages
        cursor = await db.execute(
            "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        messages = [dict(r) for r in await cursor.fetchall()]

        # Latest synthesis
        synthesis = await sm.get_latest_synthesis(session_id)

        # Changelog
        changelog = await sm.get_changelog(session_id)

        # Timeline
        timeline = await sm.get_timeline(session_id)

        # Round number (next round to be created)
        round_number = await sm.get_round_number(session_id)
        latest_round = round_number - 1

        # Reviews from the latest round only (matches the displayed synthesis)
        reviews = await sm.get_reviews(session_id, round_number=latest_round) if latest_round > 0 else []

        # Get review round completed_at for chronological positioning
        review_completed_at = None
        if latest_round > 0:
            rr_cursor = await db.execute(
                "SELECT completed_at FROM review_rounds WHERE session_id = ? AND round_number = ?",
                (session_id, latest_round),
            )
            rr_row = await rr_cursor.fetchone()
            if rr_row and rr_row[0]:
                # Normalize to space-separated format to match message timestamps
                review_completed_at = rr_row[0].replace("T", " ")

        # Attachments
        att_cursor = await db.execute(
            "SELECT id, filename, size_bytes, created_at FROM attachments WHERE session_id = ? ORDER BY filename",
            (session_id,),
        )
        attachments = [dict(r) for r in await att_cursor.fetchall()]
    finally:
        await db.close()

    # Insert council review marker at the right chronological position
    if review_completed_at and synthesis:
        messages.append({
            "role": "council_review",
            "content": "",
            "created_at": review_completed_at,
        })
        messages.sort(key=lambda m: m["created_at"])

    # Build lookup of already-decided changes from changelog
    decided = {}
    for entry in changelog:
        decided[entry["id"]] = {
            "accepted": entry["accepted"],
            "reason": entry.get("rejection_reason", ""),
        }

    # Check if there are undecided proposed changes (drives right panel visibility)
    has_undecided = False
    if synthesis:
        total_changes = len(synthesis.get('proposed_changes', []))
        has_undecided = len(decided) < total_changes and total_changes > 0

    # GitHub repo connection info
    github_repo = await sm.get_github_repo(session_id)
    github_file_count = 0
    if github_repo and github_repo.get("tree_json"):
        github_file_count = len(json.loads(github_repo["tree_json"]))

    # Determine ownership
    user = await get_current_user(request)
    is_owner = False
    if user:
        # Owner if: session has no user_id (legacy) or user_id matches
        is_owner = not session.get("user_id") or session["user_id"] == user["id"]

    return templates.TemplateResponse("session.html", await _ctx(
        request,
        session=session,
        messages=messages,
        reviews=reviews,
        synthesis=synthesis,
        changelog=changelog,
        timeline=timeline,
        round_number=round_number,
        attachments=attachments,
        decided=decided,
        has_undecided=has_undecided,
        github_repo=github_repo,
        github_file_count=github_file_count,
        github_enabled=bool(GITHUB_TOKEN),
        round_roman=_to_roman(round_number - 1) if round_number > 1 else "",
        review_completed_at=review_completed_at,
        is_owner=is_owner,
    ))


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    stats = await tracker.get_stats()
    return templates.TemplateResponse("stats.html", await _ctx(request, stats=stats))


# ─── Auth routes ─────────────────────────────────────────────

@app.get("/auth/login")
async def auth_login(request: Request):
    """Redirect to GitHub OAuth."""
    if not GITHUB_CLIENT_ID:
        raise HTTPException(500, "GitHub OAuth not configured")
    state = generate_state()
    request.session["oauth_state"] = state
    return RedirectResponse(github_login_url(state))


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = ""):
    """Handle GitHub OAuth callback."""
    saved_state = request.session.pop("oauth_state", "")
    if not state or state != saved_state:
        raise HTTPException(400, "Invalid OAuth state")

    token = await exchange_code_for_token(code)
    github_user = await fetch_github_user(token)
    user = await get_or_create_user(github_user)
    request.session["user_id"] = user["id"]

    # Redirect to where they were trying to go, or home
    next_url = request.session.pop("oauth_next", "/")
    return RedirectResponse(next_url)


@app.get("/auth/logout")
async def auth_logout(request: Request):
    """Clear session and redirect home."""
    request.session.clear()
    return RedirectResponse("/")


async def _verify_session_owner(session_id: str, user_id: str) -> dict:
    """Check that user owns the session. Legacy sessions (user_id=NULL) accessible to any logged-in user."""
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") and session["user_id"] != user_id:
        raise HTTPException(403, "Not your session")
    return session


# ─── HTMX API endpoints ─────────────────────────────────────

@app.delete("/api/session/{session_id}")
async def api_delete_session(request: Request, session_id: str):
    """Delete a session and all its data."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)
    await sm.delete_session(session_id)
    return HTMLResponse("")


@app.post("/api/upload/{session_id}")
async def api_upload(request: Request, session_id: str, file: UploadFile = File(...)):
    """Upload a zip file and extract text files as session attachments."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)

    ALLOWED_EXT = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".txt", ".html", ".css",
        ".yaml", ".yml", ".toml", ".csv", ".xml", ".cfg", ".ini", ".sh", ".sql",
        ".env.example", ".gitignore", ".dockerfile", ".rst", ".r", ".go", ".rs",
    }
    SKIP_DIRS = {"__pycache__", "node_modules", ".git", "venv", ".venv", ".tox", ".mypy_cache", "dist", "build"}
    MAX_FILE_SIZE = 500_000  # 500KB per file

    data = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return HTMLResponse('<div class="attachment-error">Not a valid zip file.</div>', status_code=400)

    db = await get_db()
    added = []
    try:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Skip large files
            if info.file_size > MAX_FILE_SIZE:
                continue
            # Skip hidden/build directories
            parts = info.filename.replace("\\", "/").split("/")
            if any(p in SKIP_DIRS for p in parts):
                continue
            # Check extension
            fname = parts[-1]
            ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ext not in ALLOWED_EXT:
                continue
            # Read content
            try:
                content = zf.read(info.filename).decode("utf-8", errors="replace")
            except Exception:
                continue

            att_id = str(uuid.uuid4())[:8]
            await db.execute(
                "INSERT INTO attachments (id, session_id, filename, content, size_bytes) VALUES (?, ?, ?, ?, ?)",
                (att_id, session_id, info.filename, content, info.file_size),
            )
            added.append({"id": att_id, "filename": info.filename, "size_bytes": info.file_size})
        await db.commit()
    finally:
        await db.close()

    return HTMLResponse(_build_attachments_html(added, session_id, full_list=True))


@app.delete("/api/attachment/{attachment_id}")
async def api_delete_attachment(request: Request, attachment_id: str):
    """Delete a single attachment."""
    await require_auth_api(request)
    db = await get_db()
    try:
        await db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        await db.commit()
    finally:
        await db.close()
    return HTMLResponse("")


@app.get("/api/attachments/{session_id}")
async def api_get_attachments(session_id: str):
    """Return HTML fragment of current attachments."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, filename, size_bytes FROM attachments WHERE session_id = ? ORDER BY filename",
            (session_id,),
        )
        attachments = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
    return HTMLResponse(_build_attachments_html(attachments, session_id))


def _build_attachments_html(attachments: list[dict], session_id: str, full_list: bool = False) -> str:
    """Build HTML for the attachment list."""
    if not attachments:
        return '<div class="attachments-empty dim">No files attached.</div>'

    def _fmt_size(b):
        if b < 1024:
            return f"{b} B"
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    count = len(attachments)
    label = f'{count} file{"s" if count != 1 else ""} attached'
    html = '<div class="attachments-list">'
    html += f'<button class="attachments-toggle" onclick="toggleAttachments()" type="button">'
    html += f'<span class="attachments-arrow" id="att-arrow">&#9654;</span>'
    html += f'<span id="att-count-label">{label}</span></button>'
    html += '<div class="attachments-items" id="attachments-items">'
    for att in attachments:
        size = _fmt_size(att.get("size_bytes", 0))
        html += f'''<div class="attachment-item" id="att-{att["id"]}">
            <span class="attachment-name">{_escape(att["filename"])}</span>
            <span class="attachment-size dim">{size}</span>
            <button class="attachment-remove" onclick="removeAttachment('{att["id"]}')" title="Remove">&times;</button>
        </div>'''
    html += '</div></div>'
    return html


# ─── GitHub auto-context endpoints ────────────────────────

@app.post("/api/github/{session_id}")
async def api_github_connect(session_id: str, request: Request):
    """Connect a GitHub repo to a session."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)

    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GitHub token not configured on server."}, status_code=400)

    body = await request.json()
    repo_url = body.get("repo_url", "").strip()
    parsed = parse_repo_url(repo_url)
    if not parsed:
        return JSONResponse({"error": "Invalid GitHub URL. Use: https://github.com/owner/repo"}, status_code=400)

    try:
        tree, branch = await github_ctx.fetch_repo_tree(parsed["owner"], parsed["repo"])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    result = await sm.connect_github_repo(
        session_id, repo_url, parsed["owner"], parsed["repo"],
        branch, json.dumps(tree),
    )
    return JSONResponse(result)


@app.post("/api/github/{session_id}/refresh")
async def api_github_refresh(request: Request, session_id: str):
    """Re-fetch the repo tree."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)
    repo_info = await sm.get_github_repo(session_id)
    if not repo_info:
        return JSONResponse({"error": "No GitHub repo connected."}, status_code=400)

    try:
        tree, branch = await github_ctx.fetch_repo_tree(
            repo_info["owner"], repo_info["repo_name"], repo_info["default_branch"]
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    await sm.update_github_tree(session_id, json.dumps(tree))
    return JSONResponse({"file_count": len(tree), "owner": repo_info["owner"], "repo_name": repo_info["repo_name"]})


@app.delete("/api/github/{session_id}")
async def api_github_disconnect(request: Request, session_id: str):
    """Disconnect the GitHub repo from a session."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)
    await sm.disconnect_github_repo(session_id)
    return JSONResponse({"ok": True})


@app.post("/api/chat/{session_id}")
async def api_chat(session_id: str, request: Request):
    """Send a message to the Lead AI. Returns HTML fragment for HTMX."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)

    form = await request.form()
    message = form.get("message", "").strip()
    if not message:
        return HTMLResponse("")

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404)

    engine = get_engine()
    lead = session["lead_model"]
    lead_name = MODELS[lead]["name"]
    lead_color = MODELS[lead]["color"]

    # Always show the user message immediately
    user_html = f"""
    <div class="message user-message">
        <div class="message-sender">You</div>
        <div class="message-content">{_escape(message)}</div>
    </div>
    """

    try:
        result = await engine.send_message(session_id, message)

        if result.get("verified"):
            # Show initial analysis (collapsed) + verification challenge + verified result
            html = user_html + f"""
            <div class="message assistant-message initial-analysis">
                <div class="message-sender" style="color: {lead_color}">{lead_name} <span class="dim">(initial analysis)</span></div>
                <div class="message-content markdown-body">{_escape(result['initial_response'])}</div>
            </div>
            <div class="message user-message verification-prompt">
                <div class="message-sender">Auto-Verification</div>
                <div class="message-content dim">Verification step: Lead challenged to prove each claim against actual code.</div>
            </div>
            <div class="message assistant-message verified-analysis">
                <div class="message-sender" style="color: {lead_color}">{lead_name} <span style="color: #1FD08C">&#10003; Verified</span></div>
                <div class="message-content markdown-body">{_escape(result['response'])}</div>
            </div>
            """
        else:
            html = user_html + f"""
            <div class="message assistant-message">
                <div class="message-sender" style="color: {lead_color}">{lead_name}</div>
                <div class="message-content markdown-body">{_escape(result['response'])}</div>
            </div>
            """
    except Exception as e:
        html = user_html + f"""
        <div class="message error-message">
            <div class="message-sender" style="color: #EF4444">Error</div>
            <div class="message-content">{_escape(str(e))}</div>
        </div>
        """

    return HTMLResponse(html)


@app.post("/api/convene/{session_id}")
async def api_convene(request: Request, session_id: str):
    """Run a full Council review cycle. Returns HTML fragment."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)

    if not acquire_lock(session_id):
        return HTMLResponse('<div class="convene-progress"><h3>Council already in session</h3><p class="dim">Please wait for the current review to finish.</p></div>')

    session = await sm.get_session(session_id)
    if not session:
        release_lock(session_id)
        raise HTTPException(404)

    try:
        result = await run_council_review(
            session_id, session, sm, dispatcher, tracker, github_ctx
        )

        # Reconstruct full review data for HTML (pipeline returns content-only)
        reviews_for_html = {k: {"content": v["content"]} for k, v in result["reviews"].items()}
        html = _build_council_html(session, reviews_for_html, result["synthesis"], result["round_number"])
        return HTMLResponse(html)
    finally:
        release_lock(session_id)


@app.post("/api/decide/{session_id}")
async def api_decide(session_id: str, request: Request):
    """Accept/reject changes. Expects JSON {decisions: [{id, accepted, reason}]}."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404)

    body = await request.json()
    decisions = body.get("decisions", [])

    synthesis = await sm.get_latest_synthesis(session_id)
    if not synthesis:
        raise HTTPException(400, "No synthesis found")

    changes = {c["id"]: c for c in synthesis.get("proposed_changes", [])}
    round_number = await sm.get_round_number(session_id) - 1

    accepted = []
    rejected = []

    for decision in decisions:
        change_id = decision["id"]
        is_accepted = decision["accepted"]
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
    response_text = ""
    if accepted:
        engine = get_engine()
        response_text = await engine.inject_synthesis(session_id, synthesis, accepted, rejected)

    lead = session["lead_model"]
    lead_name = MODELS[lead]["name"]
    lead_color = MODELS[lead]["color"]

    html = f"""
    <div class="decide-result">
        <div class="decide-summary">
            <span class="badge badge-accepted">{len(accepted)} accepted</span>
            <span class="badge badge-rejected">{len(rejected)} rejected</span>
        </div>
    """
    if response_text:
        html += f"""
        <div class="message assistant-message">
            <div class="message-sender" style="color: {lead_color}">{lead_name}</div>
            <div class="message-content markdown-body">{_escape(response_text)}</div>
        </div>
        """
    # Show "another round recommended" notification if applicable
    verdict = synthesis.get("overall_verdict", {})
    if verdict.get("another_round_recommended"):
        html += """
        <div class="round-recommended-banner">
            <span class="banner-icon">
                <span class="btn-orbs">
                    <span class="orb" style="background:var(--claude)"></span>
                    <span class="orb" style="background:var(--chatgpt)"></span>
                    <span class="orb" style="background:var(--gemini)"></span>
                    <span class="orb" style="background:var(--grok)"></span>
                </span>
            </span>
            <span>The Council recommends another round of review. When you're ready, hit <strong>Convene the Council</strong> to start Round 2.</span>
        </div>
        """

    html += "</div>"
    return HTMLResponse(html)


@app.get("/api/export/{session_id}")
async def api_export(session_id: str):
    """Export design files as a download."""
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404)

    latest = await sm.get_latest_version(session_id)
    changelog = await sm.get_changelog(session_id)
    files = await fm.generate_all_files(session, latest, changelog)

    # Return as a combined markdown file
    combined = ""
    for filename, content in files:
        combined += f"<!-- FILE: {filename} -->\n\n{content}\n\n---\n\n"

    return Response(
        content=combined,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{session["title"]}-export.md"'},
    )


@app.get("/api/cost")
async def api_cost():
    """Get current cost summary."""
    return JSONResponse({"summary": dispatcher.get_cost_summary()})


@app.get("/api/timeline/{session_id}")
async def api_timeline(session_id: str):
    """Get evolution timeline data for a session."""
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    data = await sm.get_timeline_data(session_id)
    return JSONResponse({"rounds": data})


# ─── Helpers ─────────────────────────────────────────────────

def _escape(text: str) -> str:
    """Escape HTML but preserve newlines for markdown rendering."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br>")
    )


def _model_color(name: str) -> str:
    """Get color for a model by key or display name."""
    # Direct key lookup
    if name in MODELS:
        return MODELS[name]["color"]
    # Display name lookup
    for m in MODELS.values():
        if m["name"] == name:
            return m["color"]
    return "inherit"


def _colorize_model_names(text: str) -> str:
    """Wrap model names in colored spans wherever they appear in text."""
    for model_key, model_info in MODELS.items():
        name = model_info["name"]
        color = model_info["color"]
        placeholder = f'<span class="model-name" style="color: {color}">{name}</span>'
        if placeholder not in text:
            text = text.replace(name, placeholder)
    return text


def _model_chip(name: str) -> str:
    """Create a compact model chip with the model's color."""
    # Find model key from name
    chip_class = ""
    for key, info in MODELS.items():
        if info["name"] == name or key == name:
            chip_class = f"model-chip-{key}"
            name = info["name"]
            break
    return f'<span class="model-chip {chip_class}">{name}</span>'


def _model_chips(names: list[str]) -> str:
    """Create a row of model chips."""
    return " ".join(_model_chip(n) for n in names)


def _build_council_html(session: dict, reviews: dict, synthesis: dict, round_number: int) -> str:
    """Build the full Council results HTML panel with tabs."""
    lead_model = session.get("lead_model", "claude")
    lead_name = MODELS.get(lead_model, {}).get("name", "Claude")
    lead_color = MODELS.get(lead_model, {}).get("color", "#7C5CFF")

    # Convert round number to roman numeral for display
    roman = _to_roman(round_number)

    html = '<div class="council-results" id="council-results">'
    html += '<div class="council-review-content">'
    html += f'<h2>Council Review &mdash; Round {roman}</h2>'

    # Build tab bar: Synthesis + one tab per reviewer
    html += '<div class="council-tabs">'
    html += '<button class="tab active" data-tab="synthesis" style="--tab-color: var(--accent)" onclick="switchTab(\'synthesis\', this)">'
    html += '<span class="tab-dot" style="background: var(--accent)"></span>Synthesis</button>'
    for model_key in reviews:
        name = MODELS[model_key]["name"]
        color = MODELS[model_key]["color"]
        html += f'<button class="tab" data-tab="reviewer-{model_key}" style="--tab-color: {color}" onclick="switchTab(\'reviewer-{model_key}\', this)">'
        html += f'<span class="tab-dot" style="background: {color}"></span>{name}</button>'
    html += '</div>'

    # ─── Synthesis tab (default active) ──────────────────
    html += '<div class="tab-content active" id="tab-synthesis">'
    html += '<div class="chamber-panel">'

    # Lead note
    html += f'<div class="synthesis-lead-note">'
    html += f'<span class="tab-dot" style="background: {lead_color}; color: {lead_color}"></span>'
    html += f'Synthesized by {_model_chip(lead_model)} <span class="dim">(Lead)</span>'
    html += '</div>'

    # Points of Accord (was Consensus)
    consensus = synthesis.get("consensus", [])
    if consensus:
        html += '<div class="synthesis-section"><h4>Points of Accord</h4><ul>'
        for c in consensus:
            reviewer_names = c.get("reviewers", [])
            chips = _model_chips(reviewer_names)
            html += f'<li>{_escape(c["point"])} <div class="change-source">{chips}</div></li>'
        html += '</ul></div>'

    # Majority Position (was Majority)
    majority = synthesis.get("majority", [])
    if majority:
        html += '<div class="synthesis-section"><h4>Majority Position</h4><ul>'
        for m in majority:
            for_chips = _model_chips(m.get("for", []))
            against_chips = _model_chips(m.get("against", []))
            html += f'<li>{_escape(m["point"])} <div class="change-source"><span class="dim">For:</span> {for_chips}</div>'
            # Nest dissent under the majority point
            against_reasoning = m.get("against_reasoning", "")
            if against_reasoning and m.get("against"):
                html += f'<div class="dissent-detail">{against_chips} <span class="dim">disagrees:</span> {_escape(against_reasoning)}</div>'
            html += '</li>'
        html += '</ul></div>'

    # Lone Warnings (was Unique Insights)
    unique = synthesis.get("unique_insights", [])
    if unique:
        html += '<div class="synthesis-section"><h4>Lone Warnings</h4><ul>'
        for u in unique:
            reviewer = u.get("reviewer", "?")
            chip = _model_chip(reviewer)
            html += f'<li>{_escape(u["insight"])} <div class="change-source">{chip}</div></li>'
        html += '</ul></div>'

    # Points of Dissent (was Disagreements)
    disagreements = synthesis.get("disagreements", [])
    if disagreements:
        html += '<div class="synthesis-section"><h4>Points of Dissent</h4><ul>'
        for d in disagreements:
            html += f'<li>{_escape(d["topic"])}'
            for model, pos in d.get("positions", {}).items():
                chip = _model_chip(model)
                html += f'<br>{chip} <span class="dim">{_escape(pos)}</span>'
            html += '</li>'
        html += '</ul></div>'

    # Verdict
    verdict = synthesis.get("overall_verdict", {})
    if verdict:
        ready = verdict.get("ready_to_build", False)
        another = verdict.get("another_round_recommended", True)
        html += f"""
        <div class="verdict">
            <div class="verdict-badges">
                <span class="badge {"badge-accepted" if ready else "badge-rejected"}">
                    {"Ready to build" if ready else "Not ready yet"}
                </span>
                <span class="badge {"badge-warning" if another else "badge-accepted"}">
                    {"Another round recommended" if another else "No more rounds needed"}
                </span>
            </div>
            <p>{_escape(verdict.get("summary", ""))}</p>
        </div>
        """

    html += '</div>'  # close chamber-panel
    html += '</div>'  # close tab-synthesis

    # ─── Reviewer tabs (hidden by default) ──────────────
    for model_key, review_data in reviews.items():
        content = review_data["content"] if isinstance(review_data, dict) else review_data
        escaped = _escape(content)
        escaped = _colorize_model_names(escaped)
        html += f'<div class="tab-content" id="tab-reviewer-{model_key}">'
        html += f'<div class="chamber-panel"><div class="review-body markdown-body">{escaped}</div></div>'
        html += '</div>'

    html += '</div>'  # close council-review-content

    # ─── Proposed changes (sorted by confidence) ────────
    changes = synthesis.get("proposed_changes", [])
    if changes:
        confidence_order = {"consensus": 0, "majority": 1, "single": 2}
        changes_sorted = sorted(changes, key=lambda c: confidence_order.get(c.get("confidence", "single"), 2))

        html += '<div class="changes-section">'
        html += '<h3>Proposed Changes</h3>'
        html += f'<form id="decide-form" data-action="/api/decide/{session.get("id", "")}">'

        for c in changes_sorted:
            conf = c.get("confidence", "single")
            conf_class = f"badge-{conf}"
            source_reviewers = c.get("source_reviewers", [])
            source_chips = _model_chips(source_reviewers)
            html += f"""
            <div class="change-item" data-change-id="{c["id"]}">
                <div class="change-info">
                    <div class="change-badges">
                        <span class="change-category">{c.get("category", "other")}</span>
                        <span class="badge {conf_class}">{conf}</span>
                    </div>
                    <p class="change-desc">{_escape(c["description"])}</p>
                    {"<p class='change-rationale'>" + _escape(c.get("rationale", "")) + "</p>" if c.get("rationale") else ""}
                    <div class="change-source">{source_chips}</div>
                </div>
                <div class="change-actions">
                    <button type="button" class="btn btn-accept" onclick="setDecision(this, '{c["id"]}', true)">Accept</button>
                    <button type="button" class="btn btn-reject" onclick="setDecision(this, '{c["id"]}', false)">Reject</button>
                </div>
            </div>
            """

        html += '<button type="button" class="btn btn-submit-decisions" onclick="submitDecisions()">Submit Decisions</button>'
        html += '</form>'
        html += '<div id="decide-result"></div>'
        html += '</div>'

    html += '</div>'  # close council-results
    return html


def _to_roman(n: int) -> str:
    """Convert integer to Roman numeral."""
    vals = [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),
            (50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]
    result = ''
    for val, numeral in vals:
        while n >= val:
            result += numeral
            n -= val
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8890, reload=True)
