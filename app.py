"""Council of Alignment — FastAPI web application."""

import os
import io
import json
import uuid
import asyncio
import logging
import zipfile
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)

# ─── Document text extraction ─────────────────────────────
BINARY_EXTRACTORS = {}  # ext -> callable(bytes) -> str

try:
    import docx
    def _extract_docx(data: bytes) -> str:
        doc = docx.Document(io.BytesIO(data))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    BINARY_EXTRACTORS[".docx"] = _extract_docx
except ImportError:
    logger.warning("python-docx not installed — .docx uploads disabled")

try:
    from PyPDF2 import PdfReader
    def _extract_pdf(data: bytes) -> str:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text and text.strip():
                pages.append(text)
        return "\n\n".join(pages)
    BINARY_EXTRACTORS[".pdf"] = _extract_pdf
except ImportError:
    logger.warning("PyPDF2 not installed — .pdf uploads disabled")

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
    generate_state, get_user_api_key, set_user_api_key, delete_user_api_key,
    increment_free_convenes, get_free_convenes_remaining, is_admin,
    log_key_access,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Council of Alignment", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=30 * 24 * 60 * 60, same_site="lax", https_only=True)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse({"error": "Rate limit exceeded"}, status_code=429)

MAX_REQUEST_BODY = 10 * 1024 * 1024  # 10MB


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY:
                return JSONResponse({"error": "Request too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
    return await call_next(request)


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
@limiter.limit("10/minute")
async def create_session(
    request: Request,
    title: str = Form(...),
    lead: str = Form(...),
):
    user_id = await require_auth_api(request)
    title = title[:200]
    session = await sm.create_session(title, lead, user_id=user_id)
    return RedirectResponse(f"/session/{session['id']}", status_code=303)


@app.get("/session/{session_id}", response_class=HTMLResponse)
async def session_page(request: Request, session_id: str):
    user = await get_current_user(request)
    if not user:
        request.session["oauth_next"] = f"/session/{session_id}"
        return RedirectResponse("/auth/login", status_code=302)
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

    # Free tier info for BYOK UX
    free_convenes_remaining = user.get("free_convenes_remaining", 3) if user else 0
    has_api_key = user.get("has_api_key", False) if user else False

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
        free_convenes_remaining=free_convenes_remaining,
        has_api_key=has_api_key,
    ))


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    stats = await tracker.get_stats()
    return templates.TemplateResponse("stats.html", await _ctx(request, stats=stats))


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = await get_current_user(request)
    if not is_admin(user):
        raise HTTPException(404, "Not found")

    db = await get_db()
    try:
        # Overview counts
        total_users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        total_sessions = (await (await db.execute("SELECT COUNT(*) FROM sessions")).fetchone())[0]
        total_convenes = (await (await db.execute("SELECT COUNT(*) FROM review_rounds")).fetchone())[0]
        active_7d = (await (await db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM sessions WHERE created_at > datetime('now', '-7 days') AND user_id IS NOT NULL"
        )).fetchone())[0]
        byok_users = (await (await db.execute(
            "SELECT COUNT(*) FROM users WHERE openrouter_key_encrypted IS NOT NULL AND openrouter_key_encrypted != ''"
        )).fetchone())[0]

        # Signups by day (30d)
        cursor = await db.execute(
            "SELECT date(created_at) as day, COUNT(*) as cnt FROM users "
            "WHERE created_at > datetime('now', '-30 days') GROUP BY day ORDER BY day"
        )
        signups_by_day = [dict(r) for r in await cursor.fetchall()]

        # Sessions by day (30d)
        cursor = await db.execute(
            "SELECT date(created_at) as day, COUNT(*) as cnt FROM sessions "
            "WHERE created_at > datetime('now', '-30 days') GROUP BY day ORDER BY day"
        )
        sessions_by_day = [dict(r) for r in await cursor.fetchall()]

        # Recent 10 users
        cursor = await db.execute(
            "SELECT id, github_login, display_name, avatar_url, created_at, free_convenes_used, "
            "openrouter_key_encrypted IS NOT NULL AND openrouter_key_encrypted != '' as has_byok "
            "FROM users ORDER BY created_at DESC LIMIT 10"
        )
        recent_users = [dict(r) for r in await cursor.fetchall()]

        # Recent 10 sessions (with user info)
        cursor = await db.execute(
            "SELECT s.id, s.title, s.lead_model, s.created_at, s.status, "
            "u.github_login, u.display_name, u.avatar_url "
            "FROM sessions s LEFT JOIN users u ON s.user_id = u.id "
            "ORDER BY s.created_at DESC LIMIT 10"
        )
        recent_sessions = [dict(r) for r in await cursor.fetchall()]

        # Cost totals from reviews
        cursor = await db.execute(
            "SELECT COALESCE(SUM(tokens_in), 0) as total_tokens_in, "
            "COALESCE(SUM(tokens_out), 0) as total_tokens_out, "
            "COALESCE(SUM(cost_estimate), 0) as total_cost "
            "FROM reviews"
        )
        cost_row = dict(await cursor.fetchone())
    finally:
        await db.close()

    return templates.TemplateResponse("admin.html", await _ctx(
        request,
        total_users=total_users,
        total_sessions=total_sessions,
        total_convenes=total_convenes,
        active_7d=active_7d,
        byok_users=byok_users,
        signups_by_day=signups_by_day,
        sessions_by_day=sessions_by_day,
        recent_users=recent_users,
        recent_sessions=recent_sessions,
        cost=cost_row,
    ))


# ─── Auth routes ─────────────────────────────────────────────

@app.get("/auth/login")
@limiter.limit("10/minute")
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
    # Prevent open redirect — only allow relative paths on this domain
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    return RedirectResponse(next_url)


@app.get("/auth/logout")
async def auth_logout(request: Request):
    """Clear session and redirect home."""
    request.session.clear()
    return RedirectResponse("/")


async def _verify_session_owner(session_id: str, user_id: str) -> dict:
    """Check that user owns the session."""
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") != user_id:
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
@limiter.limit("10/minute")
async def api_upload(request: Request, session_id: str, file: UploadFile = File(...)):
    """Upload a zip file and extract text files as session attachments."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)

    TEXT_EXT = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".txt", ".html", ".css",
        ".yaml", ".yml", ".toml", ".csv", ".xml", ".cfg", ".ini", ".sh", ".sql",
        ".env.example", ".gitignore", ".dockerfile", ".rst", ".r", ".go", ".rs",
    }
    ALLOWED_EXT = TEXT_EXT | set(BINARY_EXTRACTORS.keys())
    SKIP_DIRS = {"__pycache__", "node_modules", ".git", "venv", ".venv", ".tox", ".mypy_cache", "dist", "build"}
    MAX_FILE_SIZE = 500_000  # 500KB per file

    MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50MB compressed
    MAX_ZIP_FILES = 200

    data = await file.read()
    if len(data) > MAX_ZIP_SIZE:
        return HTMLResponse('<div class="attachment-error">Zip file too large (max 50MB).</div>', status_code=400)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return HTMLResponse('<div class="attachment-error">Not a valid zip file.</div>', status_code=400)

    # Check for zip bomb (total uncompressed > 500MB or excessive file count)
    total_uncompressed = sum(i.file_size for i in zf.infolist())
    if total_uncompressed > 500 * 1024 * 1024:
        return HTMLResponse('<div class="attachment-error">Zip contents too large.</div>', status_code=400)

    db = await get_db()
    added = []
    file_count = 0
    try:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if file_count >= MAX_ZIP_FILES:
                break
            # Skip large files
            if info.file_size > MAX_FILE_SIZE:
                continue
            # Skip hidden/build directories and path traversal attempts
            parts = info.filename.replace("\\", "/").split("/")
            if any(p == ".." for p in parts):
                continue
            if any(p in SKIP_DIRS for p in parts):
                continue
            # Check extension
            fname = parts[-1]
            ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ext not in ALLOWED_EXT:
                continue
            # Read content
            try:
                raw = zf.read(info.filename)
                if ext in BINARY_EXTRACTORS:
                    content = BINARY_EXTRACTORS[ext](raw)
                else:
                    content = raw.decode("utf-8", errors="replace")
            except Exception:
                continue

            att_id = str(uuid.uuid4())[:8]
            await db.execute(
                "INSERT INTO attachments (id, session_id, filename, content, size_bytes) VALUES (?, ?, ?, ?, ?)",
                (att_id, session_id, info.filename, content, info.file_size),
            )
            added.append({"id": att_id, "filename": info.filename, "size_bytes": info.file_size})
            file_count += 1
        await db.commit()
    finally:
        await db.close()

    return HTMLResponse(_build_attachments_html(added, session_id, full_list=True))


@app.post("/api/upload-file/{session_id}")
@limiter.limit("10/minute")
async def api_upload_file(request: Request, session_id: str, file: UploadFile = File(...)):
    """Upload a single file (text or document) as a session attachment."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)

    fname = (file.filename or "unnamed").replace("\\", "/").split("/")[-1][:200]
    ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""

    TEXT_EXT = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".txt", ".html", ".css",
        ".yaml", ".yml", ".toml", ".csv", ".xml", ".cfg", ".ini", ".sh", ".sql",
        ".rst", ".r", ".go", ".rs",
    }
    allowed = TEXT_EXT | set(BINARY_EXTRACTORS.keys())
    if ext not in allowed:
        return HTMLResponse(f'<div class="attachment-error">Unsupported file type: {ext}</div>', status_code=400)

    data = await file.read()
    if len(data) > 500_000:
        return HTMLResponse('<div class="attachment-error">File too large (max 500KB).</div>', status_code=400)

    try:
        if ext in BINARY_EXTRACTORS:
            content = BINARY_EXTRACTORS[ext](data)
        else:
            content = data.decode("utf-8", errors="replace")
    except Exception:
        return HTMLResponse('<div class="attachment-error">Could not extract text from file.</div>', status_code=400)

    att_id = str(uuid.uuid4())[:8]
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO attachments (id, session_id, filename, content, size_bytes) VALUES (?, ?, ?, ?, ?)",
            (att_id, session_id, fname, content, len(data)),
        )
        await db.commit()
    finally:
        await db.close()

    added = [{"id": att_id, "filename": fname, "size_bytes": len(data)}]
    return HTMLResponse(_build_attachments_html(added, session_id, full_list=True))


@app.delete("/api/attachment/{attachment_id}")
async def api_delete_attachment(request: Request, attachment_id: str):
    """Delete a single attachment."""
    user_id = await require_auth_api(request)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT session_id FROM attachments WHERE id = ?", (attachment_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Attachment not found")
        await _verify_session_owner(row["session_id"], user_id)
        await db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        await db.commit()
    finally:
        await db.close()
    return HTMLResponse("")


@app.get("/api/attachments/{session_id}")
async def api_get_attachments(request: Request, session_id: str):
    """Return HTML fragment of current attachments."""
    await require_auth_api(request)
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
        logger.error(f"GitHub connect error for {parsed['owner']}/{parsed['repo']}: {e}")
        return JSONResponse({"error": "Failed to fetch repository. Check the URL and try again."}, status_code=400)

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
        logger.error(f"GitHub refresh error for {repo_info['owner']}/{repo_info['repo_name']}: {e}")
        return JSONResponse({"error": "Failed to refresh repository."}, status_code=400)

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
@limiter.limit("20/minute")
async def api_chat(session_id: str, request: Request):
    """Send a message to the Lead AI. Returns HTML fragment for HTMX."""
    user_id = await require_auth_api(request)
    await _verify_session_owner(session_id, user_id)

    form = await request.form()
    message = form.get("message", "").strip()[:50000]
    if not message:
        return HTMLResponse("")

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404)

    engine = get_engine()
    lead = session["lead_model"]
    lead_name = MODELS[lead]["name"]
    lead_color = MODELS[lead]["color"]

    # Load user's BYOK key (if set, all API calls route through it)
    user_key = await get_user_api_key(user_id)
    if user_key:
        await log_key_access(user_id, "chat", session_id)

    # Always show the user message immediately
    user_html = f"""
    <div class="message user-message">
        <div class="message-sender">You</div>
        <div class="message-content">{_escape(message)}</div>
    </div>
    """

    try:
        result = await engine.send_message(session_id, message, api_key_override=user_key)

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
        logger.error(f"Chat error for session {session_id}: {e}")
        html = user_html + """
        <div class="message error-message">
            <div class="message-sender" style="color: #EF4444">Error</div>
            <div class="message-content">Something went wrong. Please try again.</div>
        </div>
        """

    return HTMLResponse(html)


@app.post("/api/convene/{session_id}")
@limiter.limit("3/minute")
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

    # BYOK gating: check if user has their own key or free convenes remaining
    user_key = await get_user_api_key(user_id)
    if user_key:
        await log_key_access(user_id, "convene", session_id)
    if not user_key:
        remaining = await get_free_convenes_remaining(user_id)
        if remaining <= 0:
            release_lock(session_id)
            return HTMLResponse(
                '<div class="convene-progress">'
                '<h3>Free reviews used up</h3>'
                '<p>You\'ve used your free Council review.</p>'
                '<p>Add your own <a href="/settings" style="color: var(--accent); font-weight: 600;">OpenRouter API key</a> to keep reviewing.</p>'
                '<p class="dim" style="margin-top: 12px;">OpenRouter lets you access multiple AI models with one key. Sign up free at openrouter.ai.</p>'
                '</div>'
            )

    try:
        result = await run_council_review(
            session_id, session, sm, dispatcher, tracker, github_ctx,
            api_key_override=user_key,
        )

        # If using server key (no BYOK), increment the free convene counter
        if not user_key:
            await increment_free_convenes(user_id)

        # Reconstruct full review data for HTML (pipeline returns content-only)
        reviews_for_html = {k: {"content": v["content"]} for k, v in result["reviews"].items()}
        html = _build_council_html(session, reviews_for_html, result["synthesis"], result["round_number"])
        return HTMLResponse(html)
    except Exception as e:
        logger.error(f"Council review failed for session {session_id}: {e}", exc_info=True)
        error_html = (
            '<div class="convene-progress">'
            '<h3>Council review failed</h3>'
            '<p>The review couldn\'t be completed. This is usually temporary.</p>'
            '<p><strong>What to try:</strong></p>'
            '<ul>'
            '<li>Wait 30 seconds and try again</li>'
            '<li>If using your own API key, verify it\'s valid at <a href="https://openrouter.ai" target="_blank">openrouter.ai</a></li>'
            '</ul>'
            '<p class="dim" style="margin-top: 12px;">If this keeps happening, '
            '<a href="https://github.com/scifi-signals/council-of-alignment/issues">open an issue</a>.</p>'
            '</div>'
        )
        return HTMLResponse(error_html, status_code=500)
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
        user_key = await get_user_api_key(user_id)
        if user_key:
            await log_key_access(user_id, "decide", session_id)
        engine = get_engine()
        response_text = await engine.inject_synthesis(session_id, synthesis, accepted, rejected, api_key_override=user_key)

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
async def api_export(request: Request, session_id: str):
    """Export design files as a download."""
    user_id = await require_auth_api(request)
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404)
    await _verify_session_owner(session_id, user_id)

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


@app.get("/report/{session_id}", response_class=HTMLResponse)
async def report_page(request: Request, session_id: str):
    """Printable report with all rounds and reviewer perspectives side-by-side."""
    user = await get_current_user(request)
    if not user:
        request.session["oauth_next"] = f"/report/{session_id}"
        return RedirectResponse("/auth/login", status_code=302)

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    all_syntheses = await sm.get_all_syntheses(session_id)
    all_reviews = await sm.get_reviews(session_id)  # all rounds
    total_rounds = len(all_syntheses)

    if not all_syntheses and not all_reviews:
        raise HTTPException(404, "No council review to export yet")

    from markupsafe import escape
    lead = session["lead_model"]
    lead_name = MODELS.get(lead, {}).get("name", "Claude")

    html = _build_report_html(session_id, session, lead_name, all_syntheses, all_reviews, total_rounds, escape)
    return HTMLResponse(html)


def _render_synthesis_html(synthesis: dict, escape) -> str:
    """Render a single synthesis into HTML sections."""
    html = ""

    if synthesis.get("consensus"):
        html += '<div class="synthesis-section"><h3>Points of Accord</h3><ul>'
        for c in synthesis["consensus"]:
            reviewers = ", ".join(c.get("reviewers", []))
            html += f'<li>{escape(c["point"])} <span class="source-tag">({escape(reviewers)})</span></li>'
        html += '</ul></div>'

    if synthesis.get("majority"):
        html += '<div class="synthesis-section"><h3>Majority Position</h3><ul>'
        for m in synthesis["majority"]:
            for_list = ", ".join(m.get("for", []))
            against_list = ", ".join(m.get("against", []))
            html += f'<li>{escape(m["point"])} <span class="source-tag">(For: {escape(for_list)}'
            if against_list:
                reasoning = m.get("against_reasoning", "")
                html += f' / Against: {escape(against_list)}'
                if reasoning:
                    html += f' &mdash; {escape(reasoning)}'
            html += ')</span></li>'
        html += '</ul></div>'

    if synthesis.get("unique_insights"):
        html += '<div class="synthesis-section"><h3>Lone Warnings</h3><ul>'
        for u in synthesis["unique_insights"]:
            html += f'<li>{escape(u["insight"])} <span class="source-tag">({escape(u["reviewer"])})</span></li>'
        html += '</ul></div>'

    if synthesis.get("disagreements"):
        html += '<div class="synthesis-section"><h3>Points of Dissent</h3><ul>'
        for d in synthesis["disagreements"]:
            html += f'<li><strong>{escape(d["topic"])}</strong>'
            for model, pos in d.get("positions", {}).items():
                model_info = MODELS.get(model.lower(), {})
                name = model_info.get("name", model)
                html += f'<br><span class="model-label model-label-{model.lower()}">{escape(name)}</span> {escape(pos)}'
            html += '</li>'
        html += '</ul></div>'

    if synthesis.get("overall_verdict"):
        v = synthesis["overall_verdict"]
        ready_class = "badge-green" if v.get("ready_to_build") else "badge-red"
        ready_text = "Ready to build" if v.get("ready_to_build") else "Not ready yet"
        round_class = "badge-yellow" if v.get("another_round_recommended") else "badge-green"
        round_text = "Another round recommended" if v.get("another_round_recommended") else "No more rounds needed"
        html += f'<div class="verdict"><span class="badge {ready_class}">{ready_text}</span> <span class="badge {round_class}">{round_text}</span>'
        html += f'<p style="margin-top:8px">{escape(v.get("summary", ""))}</p></div>'

    if synthesis.get("proposed_changes"):
        html += '<h3 style="margin-top:24px">Proposed Changes</h3>'
        html += '<table class="changes-table"><thead><tr><th>Change</th><th>Category</th><th>Confidence</th><th>Source</th></tr></thead><tbody>'
        for c in synthesis["proposed_changes"]:
            reviewers = ", ".join(c.get("source_reviewers", []))
            html += f'<tr><td>{escape(c["description"])}</td><td>{escape(c.get("category", ""))}</td><td>{escape(c.get("confidence", ""))}</td><td>{escape(reviewers)}</td></tr>'
        html += '</tbody></table>'

    return html


def _build_report_html(session_id: str, session: dict, lead_name: str,
                       all_syntheses: list, all_reviews: list, total_rounds: int, escape) -> str:
    """Build the full report HTML document."""
    title_escaped = str(escape(session["title"]))
    round_summary = f"{total_rounds} round{'s' if total_rounds != 1 else ''}" if total_rounds else "No reviews"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_escaped} — Council Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=DM+Sans:wght@400;500;600;700&display=swap');
:root {{
    --claude: #7C5CFF; --chatgpt: #1FD08C; --gemini: #4DA3FF; --grok: #FF9B42;
    --text-primary: #1A1D23; --text-secondary: #5A6270; --text-muted: #8C95A4;
    --border: rgba(0,0,0,0.12); --bg-light: #F7F8FA;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'DM Sans', -apple-system, sans-serif; color: var(--text-primary); line-height: 1.7; max-width: 1100px; margin: 0 auto; padding: 32px 24px; }}
h1 {{ font-family: 'Source Serif 4', Georgia, serif; font-size: 1.8rem; margin-bottom: 4px; }}
.report-header {{ display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 16px; margin-bottom: 32px; }}
.report-meta {{ color: var(--text-muted); font-size: 13px; }}
.report-actions {{ display: flex; gap: 8px; flex-shrink: 0; }}
.report-actions button {{ display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; font-family: 'DM Sans', sans-serif; transition: all 0.15s; }}
.btn-print {{ background: #2D3748; color: white; border: none; }}
.btn-print:hover {{ background: #1A202C; }}
.btn-save {{ background: white; color: var(--text-primary); border: 1px solid var(--border); }}
.btn-save:hover {{ background: var(--bg-light); }}
h2 {{ font-family: 'Source Serif 4', Georgia, serif; font-size: 1.3rem; margin: 32px 0 16px; padding-bottom: 8px; border-bottom: 2px solid var(--border); }}
h3 {{ font-size: 1rem; margin: 16px 0 8px; }}
.round-divider {{ margin: 48px 0 32px; padding: 16px 0; border-top: 3px solid var(--border); }}
.round-divider h2 {{ margin-top: 0; border-bottom: none; padding-bottom: 0; font-size: 1.5rem; }}
.model-label {{ display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; border: 1px solid; }}
.model-label-claude {{ color: var(--claude); border-color: var(--claude); }}
.model-label-chatgpt {{ color: var(--chatgpt); border-color: var(--chatgpt); }}
.model-label-gemini {{ color: var(--gemini); border-color: var(--gemini); }}
.model-label-grok {{ color: var(--grok); border-color: var(--grok); }}
.source-tag {{ color: var(--text-muted); font-size: 12px; }}
.synthesis-section {{ margin-bottom: 24px; }}
.synthesis-section h3 {{ color: var(--text-secondary); font-size: 0.95rem; text-transform: uppercase; letter-spacing: 0.5px; }}
.synthesis-section ul {{ padding-left: 20px; }}
.synthesis-section li {{ margin-bottom: 8px; }}
.reviewer-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
.reviewer-panel {{ border: 1px solid var(--border); border-radius: 8px; padding: 20px; break-inside: avoid; }}
.reviewer-panel h3 {{ margin-top: 0; display: flex; align-items: center; gap: 8px; }}
.reviewer-body {{ font-size: 14px; white-space: pre-wrap; }}
.reviewer-body h1, .reviewer-body h2, .reviewer-body h3, .reviewer-body h4 {{ font-size: 1em; margin: 12px 0 6px; }}
.verdict {{ margin-top: 16px; padding: 16px; background: var(--bg-light); border-radius: 8px; text-align: center; }}
.badge {{ display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; margin: 0 4px; }}
.badge-green {{ background: rgba(15,169,104,0.1); color: #0FA968; }}
.badge-red {{ background: rgba(220,53,69,0.1); color: #DC3545; }}
.badge-yellow {{ background: rgba(217,119,6,0.1); color: #D97706; }}
.changes-table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 12px; }}
.changes-table th {{ text-align: left; padding: 8px; border-bottom: 2px solid var(--border); font-size: 12px; text-transform: uppercase; color: var(--text-secondary); }}
.changes-table td {{ padding: 8px; border-bottom: 1px solid rgba(0,0,0,0.06); vertical-align: top; }}
.back-link {{ display: inline-block; margin-bottom: 16px; color: var(--text-secondary); text-decoration: none; font-size: 13px; }}
.back-link:hover {{ color: var(--text-primary); }}
@media print {{
    body {{ padding: 0; max-width: none; }}
    .no-print {{ display: none !important; }}
    .reviewer-grid {{ grid-template-columns: 1fr 1fr; }}
    .reviewer-panel {{ border: 1px solid #ccc; }}
    .round-divider {{ break-before: page; }}
}}
@media (max-width: 768px) {{
    .reviewer-grid {{ grid-template-columns: 1fr; }}
    .report-header {{ flex-direction: column; }}
}}
</style>
</head>
<body>
<div class="no-print">
    <a href="/session/{session_id}" class="back-link">&larr; Back to session</a>
</div>
<div class="report-header">
    <div>
        <h1>{title_escaped}</h1>
        <div class="report-meta">Council Report &bull; {escape(round_summary)} &bull; Lead: {escape(lead_name)}</div>
    </div>
    <div class="report-actions no-print">
        <button class="btn-print" onclick="window.print()">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>
            Print
        </button>
        <button class="btn-save" onclick="saveAsHTML()">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            Save HTML
        </button>
    </div>
</div>
"""

    # Group reviews by round number
    reviews_by_round: dict[int, list] = {}
    for r in all_reviews:
        rn = r["round_number"]
        reviews_by_round.setdefault(rn, []).append(r)

    # Render each round
    for i, synth_entry in enumerate(all_syntheses):
        round_num = synth_entry["round_number"]
        synthesis = synth_entry["synthesis"]
        round_reviews = reviews_by_round.get(round_num, [])
        roman = _to_roman(round_num)

        if i > 0:
            html += '<div class="round-divider">'
        else:
            html += '<div>'

        html += f'<h2>Round {roman} &mdash; Synthesis</h2>'
        html += _render_synthesis_html(synthesis, escape)

        if round_reviews:
            html += f'<h2>Round {roman} &mdash; Individual Perspectives</h2>'
            html += '<div class="reviewer-grid">'
            for r in round_reviews:
                model_key = r["model_name"]
                model_info = MODELS.get(model_key, {})
                name = model_info.get("name", model_key)
                html += f'<div class="reviewer-panel">'
                html += f'<h3><span class="model-label model-label-{model_key}">{escape(name)}</span></h3>'
                html += f'<div class="reviewer-body">{escape(r["response"])}</div>'
                html += '</div>'
            html += '</div>'

        html += '</div>'

    html += """
<script>
// Markdown rendering for reviewer bodies
document.querySelectorAll('.reviewer-body').forEach(el => {
    let text = el.textContent;
    text = text.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
    text = text.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
    text = text.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
    text = text.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    text = text.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    text = text.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    text = text.replace(/^[\\-\\*] (.+)$/gm, '<li>$1</li>');
    text = text.replace(/```[\\s\\S]*?```/g, match => '<pre><code>' + match.slice(3, -3).replace(/^\\w*\\n/, '') + '</code></pre>');
    text = text.replace(/`(.+?)`/g, '<code>$1</code>');
    el.innerHTML = text;
});

function saveAsHTML() {
    const html = document.documentElement.outerHTML;
    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = document.title.replace(/[^a-zA-Z0-9 _-]/g, '') + '.html';
    a.click();
    URL.revokeObjectURL(url);
}
</script>
</body>
</html>"""

    return html


@app.get("/api/cost")
async def api_cost(request: Request):
    """Get current cost summary."""
    await require_auth_api(request)
    return JSONResponse({"summary": dispatcher.get_cost_summary()})


@app.get("/api/timeline/{session_id}")
async def api_timeline(request: Request, session_id: str):
    """Get evolution timeline data for a session."""
    await require_auth_api(request)
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    data = await sm.get_timeline_data(session_id)
    return JSONResponse({"rounds": data})


# ─── Settings / BYOK routes ──────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Render the settings page."""
    user = await get_current_user(request)
    if not user:
        request.session["oauth_next"] = "/settings"
        return RedirectResponse("/auth/login", status_code=302)
    return templates.TemplateResponse("settings.html", await _ctx(request))


@app.post("/api/settings/api-key")
@limiter.limit("10/minute")
async def api_set_key(request: Request):
    """Validate and store the user's OpenRouter API key."""
    user_id = await require_auth_api(request)
    form = await request.form()
    api_key = form.get("api_key", "").strip()
    if not api_key:
        return HTMLResponse('<div class="settings-error">Please enter an API key.</div>', status_code=400)

    # Validate the key by hitting OpenRouter's models endpoint
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code in (401, 403):
                return HTMLResponse('<div class="settings-error">Invalid API key. Please check and try again.</div>', status_code=400)
            if resp.status_code != 200:
                return HTMLResponse('<div class="settings-error">Could not validate key. Please try again.</div>', status_code=400)
    except Exception:
        return HTMLResponse('<div class="settings-error">Could not reach OpenRouter. Please try again.</div>', status_code=400)

    await set_user_api_key(user_id, api_key)
    await log_key_access(user_id, "key_saved")

    # Return the "key is set" UI fragment
    masked = api_key[:6] + "..." + api_key[-4:]
    html = f'''
    <div class="key-status key-set">
        <div class="key-masked"><span class="dim">Key:</span> <code>{_escape(masked)}</code></div>
        <p class="key-status-text" style="color: var(--green);">Key saved and validated.</p>
        <form hx-post="/api/settings/api-key/delete" hx-target="#key-section" hx-swap="innerHTML" style="margin-top: 12px;">
            <button type="submit" class="btn btn-secondary">Remove Key</button>
        </form>
    </div>
    '''
    return HTMLResponse(html)


@app.post("/api/settings/api-key/delete")
async def api_delete_key(request: Request):
    """Remove the user's stored API key."""
    user_id = await require_auth_api(request)
    await delete_user_api_key(user_id)
    await log_key_access(user_id, "key_deleted")

    html = '''
    <div class="key-status key-unset">
        <form hx-post="/api/settings/api-key" hx-target="#key-section" hx-swap="innerHTML" hx-encoding="application/x-www-form-urlencoded">
            <div class="key-input-row">
                <input type="password" name="api_key" placeholder="sk-or-..." class="key-input" required>
                <button type="submit" class="btn btn-primary">Save Key</button>
            </div>
        </form>
        <p class="dim" style="margin-top: 8px; font-size: 13px;">Key removed. Add a new one to resume reviewing.</p>
    </div>
    '''
    return HTMLResponse(html)


# ─── Helpers ─────────────────────────────────────────────────

def _escape(text: str) -> str:
    """Escape HTML but preserve newlines for markdown rendering.

    Newlines are kept as literal \\n (not <br>) so that renderMarkdown()
    can read them via el.textContent and pass them to marked.parse().
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
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

    # ─── Proposed changes (inline, below verdict) ────────
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
                    {"<p class='change-context'>" + _escape(c["context"]) + "</p>" if c.get("context") else ""}
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

    html += '</div>'  # close council-review-content
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
    uvicorn.run("app:app", host="127.0.0.1", port=8890)
