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
from starlette.responses import Response

from config import MODELS, get_council_models, GITHUB_TOKEN
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Council of Alignment", lifespan=lifespan, docs_url=None, redoc_url=None)

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Shared instances
dispatcher = ModelDispatcher()
sm = SessionManager()
tracker = ReviewerTracker()

# Lock to prevent concurrent convene calls per session
_convene_locks: set[str] = set()
fm = FileManager()
github_ctx = GitHubContextProvider()


def get_engine():
    return ChatEngine(dispatcher, github_ctx)


# ─── Template helpers ────────────────────────────────────────

def _ctx(request: Request, **kwargs):
    """Build template context with common data."""
    return {"request": request, "models": MODELS, **kwargs}


# ─── Pages ───────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    sessions = await sm.list_sessions()
    return templates.TemplateResponse("home.html", _ctx(request, sessions=sessions))


@app.get("/new", response_class=HTMLResponse)
async def new_session_page(request: Request):
    return templates.TemplateResponse("new.html", _ctx(request))


@app.post("/new")
async def create_session(
    request: Request,
    title: str = Form(...),
    lead: str = Form(...),
):
    session = await sm.create_session(title, lead)
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

        # Attachments
        att_cursor = await db.execute(
            "SELECT id, filename, size_bytes, created_at FROM attachments WHERE session_id = ? ORDER BY filename",
            (session_id,),
        )
        attachments = [dict(r) for r in await att_cursor.fetchall()]
    finally:
        await db.close()

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

    return templates.TemplateResponse("session.html", _ctx(
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
    ))


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    stats = await tracker.get_stats()
    return templates.TemplateResponse("stats.html", _ctx(request, stats=stats))


# ─── HTMX API endpoints ─────────────────────────────────────

@app.delete("/api/session/{session_id}")
async def api_delete_session(session_id: str):
    """Delete a session and all its data."""
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404)
    await sm.delete_session(session_id)
    return HTMLResponse("")


@app.post("/api/upload/{session_id}")
async def api_upload(session_id: str, file: UploadFile = File(...)):
    """Upload a zip file and extract text files as session attachments."""
    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404)

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
async def api_delete_attachment(attachment_id: str):
    """Delete a single attachment."""
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
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GitHub token not configured on server."}, status_code=400)

    session = await sm.get_session(session_id)
    if not session:
        raise HTTPException(404)

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
async def api_github_refresh(session_id: str):
    """Re-fetch the repo tree."""
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
async def api_github_disconnect(session_id: str):
    """Disconnect the GitHub repo from a session."""
    await sm.disconnect_github_repo(session_id)
    return JSONResponse({"ok": True})


@app.post("/api/chat/{session_id}")
async def api_chat(session_id: str, request: Request):
    """Send a message to the Lead AI. Returns HTML fragment for HTMX."""
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
async def api_convene(session_id: str):
    """Run a full Council review cycle. Returns HTML fragment."""
    if session_id in _convene_locks:
        return HTMLResponse('<div class="convene-progress"><h3>Council already in session</h3><p class="dim">Please wait for the current review to finish.</p></div>')
    _convene_locks.add(session_id)

    session = await sm.get_session(session_id)
    if not session:
        _convene_locks.discard(session_id)
        raise HTTPException(404)

    try:
        engine = get_engine()
        lead = session["lead_model"]
        council = session["council_models"]
        round_number = await sm.get_round_number(session_id)

        # Step 1: Build the raw conversation as the briefing
        # No extraction, no telephone — reviewers see exactly what was discussed
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )
            rows = await cursor.fetchall()

            # Check for attachments (reference codebase)
            att_cursor = await db.execute(
                "SELECT filename, content FROM attachments WHERE session_id = ? ORDER BY filename",
                (session_id,),
            )
            att_rows = [dict(r) for r in await att_cursor.fetchall()]
        finally:
            await db.close()

        lead_name = MODELS[lead]["name"]
        conversation_lines = []
        for r in rows:
            speaker = "User" if r["role"] == "user" else lead_name
            conversation_lines.append(f"**{speaker}:**\n{r['content']}")
        raw_conversation = "\n\n---\n\n".join(conversation_lines)

        # Build changelog context for round 2+
        changelog = await sm.get_changelog(session_id)
        changelog_section = ""
        if changelog and round_number > 1:
            changelog_lines = []
            for c in changelog:
                status = "accepted" if c.get("accepted") else "rejected"
                reviewers = ", ".join(c.get("source_reviewers", []))
                changelog_lines.append(f"- [{c.get('category', 'general')}] {c['description']} (from {reviewers}, {status})")
            changelog_section = f"\n\n## Changes From Previous Round\n\n" + "\n".join(changelog_lines)

        attachment_section = build_attachment_context(att_rows, heading="Reference Codebase")

        # ── GitHub auto-context ──────────────────────────────
        github_section = ""
        github_file_count = 0
        repo_info = await sm.get_github_repo(session_id)
        if repo_info and repo_info.get("tree_json"):
            try:
                tree = json.loads(repo_info["tree_json"])
                selected_paths = await github_ctx.select_relevant_files(
                    dispatcher, lead, raw_conversation, tree, max_files=30
                )
                if selected_paths:
                    file_contents = await github_ctx.fetch_file_contents(
                        repo_info["owner"], repo_info["repo_name"],
                        selected_paths, repo_info["default_branch"],
                    )
                    # Deduplicate against manual uploads
                    manual_names = {a["filename"].split("/")[-1].lower() for a in att_rows}
                    file_contents = [
                        f for f in file_contents
                        if f["filename"].split("/")[-1].lower() not in manual_names
                    ]
                    github_file_count = len(file_contents)
                    if file_contents:
                        github_section = build_attachment_context(
                            file_contents,
                            heading="Auto-Selected Codebase Context",
                            auto_selected=True,
                        )
                        # Add the full file tree so reviewers know what exists
                        # even if it wasn't selected for full content
                        not_included = [p for p in tree if p not in selected_paths]
                        if not_included:
                            github_section += (
                                f"\n\n### Other Files in Repository (not loaded — request if needed)\n"
                                f"```\n" + "\n".join(sorted(not_included)) + "\n```\n"
                            )
            except Exception as e:
                logging.getLogger(__name__).warning("GitHub auto-context failed: %s", e)
                # Graceful degradation — proceed without

        briefing = f"""## Design Review — Round {round_number}

Below is the full conversation between the user and {lead_name} (the Lead AI). This is everything that was discussed — nothing has been summarized or filtered. Read it all carefully before giving your review.

## Full Conversation

{raw_conversation}
{changelog_section}
{github_section}
{attachment_section}

---

Question everything. Trace every data flow to its endpoint. If something claims to solve a problem, verify the complete path from input to changed behavior. Find what's missing, not just what's present."""

        # Save version (use raw conversation as the "design" for version tracking)
        version = await sm.save_version(session_id, raw_conversation, "council_review")
        round_id = await sm.save_review_round(session_id, round_number, briefing)

        # Step 2: Dispatch to Council — they see the full conversation
        council_system = (
            "You are a design reviewer on the Council of Alignment. "
            "You're about to read the full conversation between a user and their Lead AI, "
            "along with source code from the codebase.\n\n"

            "YOUR JOB IS TO QUESTION AND VALIDATE, NOT TO SUGGEST AND ENHANCE.\n\n"

            "You are a data flow tracer, NOT a code quality reviewer. The only question you answer is: "
            "'Does data get from A to B?' You do not review error handling, logging, fallback behavior, "
            "code style, or engineering practices. A function that catches an error and returns a default "
            "value has a COMPLETE data flow. Do not report it.\n\n"

            "The default assumption is that the proposed solution is incomplete or broken until you can "
            "prove otherwise. Do not accept claims at face value. If the design says it solves a problem, "
            "trace the solution end-to-end and verify it actually works. Specifically:\n\n"

            "- TRACE THE DATA FLOW: If something writes data, find what reads it. If nothing reads it, "
            "the solution is broken — say so. If a new component is proposed, verify it connects to the "
            "existing system that needs it.\n"
            "- QUESTION EVERY CLAIM: If the design says 'this enables learning,' ask: where exactly does "
            "the learning happen? What code path turns this data into changed behavior? If you can't trace "
            "the path, the claim is unverified.\n"
            "- FIND WHAT'S MISSING: The most important things are often what ISN'T discussed. What downstream "
            "systems need to change? What existing code needs to be updated? What assumptions are being made "
            "about how things connect?\n"
            "- TEST THE COMPLETENESS: For every proposed change, ask 'and then what?' If the answer isn't "
            "covered, the solution is incomplete.\n"
            "- BE SPECIFIC: Don't say 'this might have issues.' Say exactly what's broken and exactly what "
            "it would take to fix it.\n\n"

            "VERIFY BEFORE YOU REPORT:\n"
            "For every issue you claim, show your work. Name the EXACT file and EXACT function — "
            "not inferred names, not guesses. Read the loaded code and cite real function names. "
            "Never reference a file that doesn't exist in the loaded code or file tree. "
            "If you claim a data flow is broken, state: 'I looked for a consumer of X in [files] "
            "and found nothing' or 'I found the consumer at [file:function].' "
            "If you didn't check, don't claim it's broken. "
            "If a file you need wasn't provided, say so explicitly — "
            "'I cannot verify this because [file] was not included in the review' — "
            "rather than guessing.\n\n"

            "CRITICAL — IGNORE SPEC FILES:\n"
            "If a file ends in -spec.md, -CLAUDE.md, -design.md, or is clearly a specification, "
            "roadmap, or design document — DO NOT use it as a source of truth. Specs describe what "
            "SHOULD exist. Your job is to analyze what DOES exist. If a spec says 'implement 10 checks' "
            "and the code has 3, that is a backlog item, NOT a broken data flow. Never cite a spec file "
            "as evidence that running code is broken. Only report issues found by tracing actual code "
            "in .py, .js, .ts, or other executable files.\n\n"

            "FINDING NOTHING IS A VALID OUTCOME:\n"
            "If the code is sound, say so and be done. A short response finding zero issues is more "
            "valuable than a long response inventing problems. Do not pad your review to match some "
            "expected length. The number of findings should be zero if zero real issues exist. "
            "Do not reframe intentional design choices as bugs. Do not flag working code with "
            "'this could potentially...' hedging. Either it's broken or it isn't.\n\n"

            "THESE ARE NOT FINDINGS — do not report them:\n"
            "- A feature disabled via config flag ('enabled': false) is a feature toggle, not a dead end\n"
            "- An error handler that logs a warning and returns a fallback value is graceful degradation, not a silent failure\n"
            "- A config file with initial values is not 'stale state' if code updates those values at runtime\n"
            "- A JSON config with starting defaults is not a 'contradiction' with code that modifies them during execution\n"
            "- A file you didn't read is not evidence of a broken chain — it's a gap in your visibility\n"
            "If you have more than 5 findings, you are almost certainly padding. Review every finding and ask: "
            "'Is this actually broken, or am I reporting a design choice I wouldn't have made?' Delete the latter.\n\n"

            "DO NOT SUGGEST IMPROVEMENTS OR ENHANCEMENTS. Your job is to find what's broken, not to suggest "
            "what could be better. If a chain works, do not add 'but it could be more sophisticated' or "
            "'there's an opportunity to enhance.' No 'areas for improvement' sections, no 'opportunities,' "
            "no 'could be enhanced.' The user asked what's broken, not what you'd do differently.\n\n"

            "Write in plain, conversational language. No jargon, no consultant-speak, no bullet-point walls. "
            "Explain your reasoning like you're talking to someone over coffee.\n\n"

            "PLAIN LANGUAGE RULE: For any technical term, architecture pattern, or industry concept you mention, "
            "include a brief parenthetical or one-sentence explanation of what it means and why it matters. "
            "Write as if the reader is smart but not necessarily familiar with every term. "
            "Clarity over brevity — don't sacrifice understanding to sound concise.\n\n"
            "Bad: 'The model-agnostic BYOK approach is smart.'\n"
            "Good: 'Letting users bring their own API keys (meaning they connect their own AI accounts rather than "
            "you paying for access) and supporting multiple AI providers is smart because it keeps costs flexible "
            "and avoids vendor lock-in.'"
        )
        reviews = await dispatcher.dispatch_to_council(council, council_system, briefing)

        # Save reviews
        for model_key, review_data in reviews.items():
            await sm.save_review(
                round_id, model_key, review_data["content"],
                review_data.get("tokens_in", 0), review_data.get("tokens_out", 0), review_data.get("cost", 0),
            )
        await sm.complete_review_round(round_id)

        # Step 3: Synthesize
        reviewer_stats = await tracker.get_stats_for_synthesis(council)
        synthesis = await synthesize_reviews(dispatcher, lead, raw_conversation, reviews, changelog, reviewer_stats)
        await sm.save_synthesis(round_id, synthesis)

        # Record suggestions
        for change in synthesis.get("proposed_changes", []):
            for reviewer in change.get("source_reviewers", []):
                await tracker.record_suggestion(reviewer, change["id"], change.get("category", "other"))

        # Build the HTML response
        html = _build_council_html(session, reviews, synthesis, round_number)
        return HTMLResponse(html)
    finally:
        _convene_locks.discard(session_id)


@app.post("/api/decide/{session_id}")
async def api_decide(session_id: str, request: Request):
    """Accept/reject changes. Expects JSON {decisions: [{id, accepted, reason}]}."""
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
