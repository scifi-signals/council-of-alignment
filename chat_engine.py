"""Lead AI conversation manager — maintains chat history, extracts design state."""

import json
import logging
import uuid
from database import get_db
from dispatcher import ModelDispatcher
from config import MODELS
from attachment_context import build_attachment_context
from github_context import GitHubContextProvider

logger = logging.getLogger(__name__)

LEAD_SYSTEM_PROMPT = """You are {model_name}, Lead AI for "{title}".

You have two modes. Read the full prompt, then match your behavior to the situation.

## Mode 1: Design Conversation
When the user is describing an idea, exploring options, or building something new — be a genuine
collaborator. Ask questions that sharpen the idea. Identify trade-offs worth discussing. Help them
think through what they haven't considered yet. Be direct and opinionated — if you think an approach
is wrong, say so and say why.

## Mode 2: Code & Design Analysis
When Reference Materials are attached below, or the user asks you to evaluate existing code or a
concrete design — switch to analytical mode.

DO NOT SUMMARIZE. Do not describe what each component does. Do not give a tour of the architecture.
The user wrote the code — they know what it's supposed to do. Your job is to find what's actually
broken, missing, or disconnected.

### Method: Trace the chains
Pick the most important data flows in the system and trace each one end-to-end through the actual
code. For each chain, report one of:
- COMPLETE: data flows from origin to final consumer and produces the intended effect
- BROKEN: the chain breaks at a specific point (say exactly where and why)
- DEAD END: data is written/collected but nothing reads it or acts on it

For example: if the code says "lessons are extracted from trades," find where lessons are created,
then find what reads those lessons, then find whether that reader changes any downstream behavior.
If the chain breaks — lessons are stored but nothing retrieves them for decisions — that's a finding.

### Your ONLY job: trace data flows
You are a data flow tracer, NOT a code quality reviewer. The only question you answer is:
"Does data get from A to B?" You do not review error handling, logging, fallback behavior,
code style, or engineering practices. A function that catches an error and returns a default
value has a COMPLETE data flow (input → processing → output). Do not report it.

### What to report
- Broken chains: where data stops flowing and what downstream behavior never happens as a result
- Dead writes: data collected or computed but never consumed
- Missing connections: function imported but never called, value computed but never read
- Contradictions: where two parts of the system assume different things about shared state

### VERIFY BEFORE YOU REPORT
For every issue you claim, show your work. Name the EXACT file and EXACT function you traced
through — not "(inferred)" names, not guesses. If you have a file loaded, read it and cite
the real function names. If you write "run_trading_cycle() (inferred)" when the actual function
is called run_cycle(), that tells the user you're skimming, not analyzing.

NEVER reference a file that doesn't exist. If you didn't see a file in the loaded code or in
the file tree, it doesn't exist — do not invent it. "reporter.py (inferred existence)" when
no such file exists destroys your credibility.

If you claim a data flow is broken, state: "I looked for a consumer of X in [file:function]
and found nothing" or "I found the consumer at [file:function] — chain is complete."
If you didn't read the file where the consumer might live, say so explicitly: "I cannot verify
this because [file] was not included" — rather than assuming it's broken.

Do NOT report a chain as broken if you simply haven't seen the file that completes it.
That's a gap in your visibility, not a gap in the code.

### What NOT to report
Before you say anything, ask: "Could I say this about any project without reading the code?" If
yes, delete it. "Add unit tests," "improve error handling," "add caching," "add retry logic" — if
you can't point to a specific function, a specific failure mode, and a specific consequence, don't
say it. Generic improvement suggestions are worthless.

DO NOT SUGGEST IMPROVEMENTS OR ENHANCEMENTS. Your job is to find what's broken, not to suggest
what could be better. If a chain is complete and working, do not add "but it could be more
sophisticated" or "there's an opportunity to enhance." The user didn't ask what you'd do
differently — they asked what's broken. If nothing is broken, say nothing. No "areas for
improvement" sections, no "opportunities," no "could be enhanced." These are padding.

CRITICAL — IGNORE SPEC FILES: If a file ends in -spec.md, -CLAUDE.md, -design.md, or is clearly
a specification, roadmap, or design document — DO NOT use it as a source of truth. Specs describe
what SHOULD exist. Your job is to analyze what DOES exist. If a spec says "implement 10 anomaly
checks" and the code has 3, that is a backlog item, NOT a broken data flow. Never cite a spec file
as evidence that running code is broken or incomplete. Only report issues you find by tracing
actual code in .py, .js, .ts, or other executable files.

### Finding nothing is a valid outcome
If the code is sound, say so. A short response with zero findings is more valuable than a long
response with invented problems. Do not pad your analysis to match some expected length. Do not
reframe intentional design choices as bugs. Do not hedge with "this could potentially..." — either
trace the data flow and find the break, or confirm the chain is complete.

THESE ARE NOT FINDINGS — do not report them:
- A feature disabled via config flag is a feature toggle, not a dead end. This includes boolean
  flags like "feature_x": false, nested objects with "enabled": false, and conditional branches
  gated on config values. If code exists but is off by default, the data flow is COMPLETE — it's
  just gated. Do not report disabled features.
- An error handler that logs a warning and returns a fallback value is graceful degradation, not a silent failure
- A config file with initial values is not "stale state" if code updates those values at runtime
- A JSON config with starting defaults is not a "contradiction" with code that modifies them during execution
- A file you didn't read is not evidence of a broken chain — it's a gap in your visibility

If you have more than 5 findings, you are almost certainly padding. Most codebases have 1-3 real
issues. Before submitting, review every finding and ask: "Is this actually broken, or am I reporting
a design choice I wouldn't have made?" Delete the latter.

### Format
Don't bury findings in long paragraphs. Lead with the finding, then show the evidence. The user
should be able to scan your response and immediately see what's wrong and where.

## Always
- Write in plain language. For any technical term or pattern, briefly explain what it means and why
  it matters. Write as if the reader is smart but not necessarily a developer.
- When the user is ready, they can convene the Council — a panel of other AI models who will
  independently review. That review is adversarial by design. Your role is collaborative, but not
  soft. Prepare the user for a rigorous review by being honest about weaknesses before the Council
  finds them."""

VERIFICATION_PROMPT = """Now verify your analysis. Go through every claim you just made:

1. For every file you referenced: confirm it appears in the loaded code above. If you named a file
   that doesn't exist, retract it.
2. For every finding (broken chain, dead end, logic gap, etc.): quote the exact code that proves it.
   If you can't quote specific code, retract the finding.
3. For every "complete" chain: name the actual function calls in sequence (not inferred — real names
   from the code you read).
4. Remove any section that isn't about a verified broken chain (no "areas for investigation," no
   "well-implemented features," no improvement suggestions).

Rewrite your analysis with only what survives verification. If nothing is broken, say so in one
sentence and stop."""

EXTRACT_DESIGN_PROMPT = """Based on our conversation so far, extract and summarize the current design state.

Format it as a clean, structured document that another AI reviewer could understand cold. Include:
- Project overview and goals
- Architecture / key decisions
- Components and their responsibilities
- Any open questions or known trade-offs

Be comprehensive but concise. This will be sent to a panel of AI reviewers for critical review."""

INJECT_SYNTHESIS_PROMPT = """The Council of Alignment has reviewed the design. Here are the full results:

## Accepted Changes
{accepted_changes}

## Rejected Changes
{rejected_changes}

## Full Council Synthesis

### Points of Accord (all reviewers agreed)
{consensus_text}

### Majority Positions (with dissent)
{majority_text}

### Unique Insights (caught by one reviewer)
{unique_text}

### Disagreements
{disagreements_text}

### Overall Verdict
{verdict_text}

Please update the design to incorporate all accepted changes. Then continue the conversation with the user, summarizing what changed and asking if they want to refine anything further or convene another round of review."""


def _extract_mentioned_files(message: str, attachments: list[dict]) -> list[str]:
    """Find attachment filenames referenced in a message.

    Matches on basename (e.g., 'HOLD_PROBLEM_BRIEFING.md') or stem without extension
    (e.g., 'hold_problem_briefing'). Case-insensitive.
    """
    msg_lower = message.lower()
    mentioned = []
    for att in attachments:
        fname = att["filename"]
        # Get the basename (last path component)
        basename = fname.replace("\\", "/").split("/")[-1]
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename
        # Check if the basename or stem appears in the message
        if basename.lower() in msg_lower or stem.lower().replace("_", " ") in msg_lower.replace("_", " "):
            mentioned.append(fname)
    return mentioned


class ChatEngine:
    def __init__(self, dispatcher: ModelDispatcher, github_ctx: GitHubContextProvider = None):
        self.dispatcher = dispatcher
        self.github_ctx = github_ctx or GitHubContextProvider()

    async def send_message(self, session_id: str, user_message: str) -> dict:
        """Send user message to Lead AI, get response. Maintains full history in DB.

        Returns dict with:
            - response: the Lead's response (or verified response if auto-verification triggered)
            - initial_response: the pre-verification response (only if verification happened)
            - verified: bool indicating if auto-verification was applied
        """
        db = await get_db()
        try:
            # Get session info
            cursor = await db.execute("SELECT lead_model, title FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Session {session_id} not found")
            lead_model = row["lead_model"]
            title = row["title"]

            # Save user message
            msg_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
                (msg_id, session_id, "user", user_message),
            )
            await db.commit()

            # Build message history
            cursor = await db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )
            rows = await cursor.fetchall()
            messages = [{"role": r["role"], "content": r["content"]} for r in rows]

            # Build system prompt with attachments
            system = LEAD_SYSTEM_PROMPT.format(
                model_name=MODELS[lead_model]["name"],
                title=title,
            )

            # Include session attachments as reference materials
            att_cursor = await db.execute(
                "SELECT filename, content, size_bytes FROM attachments WHERE session_id = ? ORDER BY filename",
                (session_id,),
            )
            att_rows = [dict(r) for r in await att_cursor.fetchall()]

            # Detect filenames mentioned in recent messages to boost their priority
            priority_files = _extract_mentioned_files(user_message, att_rows)
            system += build_attachment_context(att_rows, heading="Reference Materials",
                                              priority_files=priority_files)

            # Include GitHub repo files if connected
            github_context, github_loaded_files = await self._get_github_context(
                session_id, lead_model, messages, att_rows, db
            )
            has_code_context = bool(att_rows) or bool(github_context)
            if github_context:
                system += github_context

            # Build file inventory and inject into the conversation so the model
            # knows exactly what files exist — prevents hallucinating file names
            if has_code_context:
                all_files = sorted(set(
                    [a["filename"] for a in att_rows] + github_loaded_files
                ))
                inventory = (
                    "\n\n---\n"
                    "FILE INVENTORY — You have access to EXACTLY these files and no others. "
                    "Do not reference any file not on this list.\n"
                    + "\n".join(f"  - {f}" for f in all_files)
                    + "\n---"
                )
                # Append inventory to the last user message in the conversation
                if messages and messages[-1]["role"] == "user":
                    messages[-1] = {
                        "role": "user",
                        "content": messages[-1]["content"] + inventory,
                    }

            # Call Lead AI
            result = await self.dispatcher.chat(lead_model, messages, system=system)
            response = result["content"]

            # Save assistant response
            resp_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
                (resp_id, session_id, "assistant", response),
            )
            await db.commit()

            # Auto-verification: if this is the first message and code is loaded,
            # challenge the Lead to prove its claims
            is_first_message = len(messages) <= 1  # only the user message we just added
            if has_code_context and is_first_message:
                initial_response = response
                verified_response = await self._verify_analysis(
                    session_id, lead_model, title, system, db
                )
                return {
                    "response": verified_response,
                    "initial_response": initial_response,
                    "verified": True,
                }

            return {"response": response, "verified": False}
        finally:
            await db.close()

    async def _verify_analysis(
        self, session_id: str, lead_model: str, title: str, system: str, db
    ) -> str:
        """Send verification challenge and return the verified analysis."""
        # Save the verification prompt as a user message
        verify_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
            (verify_id, session_id, "user", VERIFICATION_PROMPT),
        )
        await db.commit()

        # Rebuild message history (now includes initial response + verification prompt)
        cursor = await db.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        rows = await cursor.fetchall()
        messages = [{"role": r["role"], "content": r["content"]} for r in rows]

        # Call Lead AI again with full context
        result = await self.dispatcher.chat(lead_model, messages, system=system)
        verified = result["content"]

        # Save verified response
        resp_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
            (resp_id, session_id, "assistant", verified),
        )
        await db.commit()

        return verified

    async def _get_github_context(
        self, session_id: str, lead_model: str,
        messages: list[dict], att_rows: list[dict], db
    ) -> tuple[str, list[str]]:
        """Fetch GitHub files for the Lead AI's context. Cache on first use.

        Returns (context_string, list_of_loaded_filenames).
        """
        try:
            cursor = await db.execute(
                "SELECT owner, repo_name, default_branch, tree_json, chat_files_json "
                "FROM github_repos WHERE session_id = ?",
                (session_id,),
            )
            repo = await cursor.fetchone()
            if not repo or not repo["tree_json"]:
                return "", []

            # Cache hit — deserialize and use
            if repo["chat_files_json"]:
                file_contents = json.loads(repo["chat_files_json"])
            else:
                # Cache miss — select and fetch files
                tree = json.loads(repo["tree_json"])
                conversation = "\n".join(
                    f"{m['role']}: {m['content']}" for m in messages
                )
                selected_paths = await self.github_ctx.select_relevant_files(
                    self.dispatcher, lead_model, conversation, tree, max_files=30
                )
                if not selected_paths:
                    return "", []

                file_contents = await self.github_ctx.fetch_file_contents(
                    repo["owner"], repo["repo_name"],
                    selected_paths, repo["default_branch"],
                )
                if not file_contents:
                    return "", []

                # Cache the result
                await db.execute(
                    "UPDATE github_repos SET chat_files_json = ? WHERE session_id = ?",
                    (json.dumps(file_contents), session_id),
                )
                await db.commit()

            # Deduplicate against manual uploads
            manual_names = {a["filename"].split("/")[-1].lower() for a in att_rows}
            file_contents = [
                f for f in file_contents
                if f["filename"].split("/")[-1].lower() not in manual_names
            ]

            if not file_contents:
                return "", []

            loaded_filenames = [f["filename"] for f in file_contents]

            context = build_attachment_context(
                file_contents,
                heading="GitHub Codebase Context",
                auto_selected=True,
            )

            # Show the full file tree so the Lead knows what exists beyond loaded files
            tree = json.loads(repo["tree_json"])
            loaded_paths = set(loaded_filenames)
            not_loaded = [p for p in tree if p not in loaded_paths]
            if not_loaded:
                context += (
                    "\n\n### Other Files in Repository (not loaded — do not assume these are broken)\n"
                    "```\n" + "\n".join(sorted(not_loaded)) + "\n```\n"
                )

            return context, loaded_filenames
        except Exception as e:
            logger.warning("GitHub chat context failed: %s", e)
            return "", []

    async def get_design_state(self, session_id: str) -> str:
        """Ask the Lead AI to extract the current design from conversation."""
        db = await get_db()
        try:
            cursor = await db.execute("SELECT lead_model, title FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
            lead_model = row["lead_model"]
            title = row["title"]

            # Get full conversation
            cursor = await db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )
            rows = await cursor.fetchall()
            messages = [{"role": r["role"], "content": r["content"]} for r in rows]

            # Add extraction request
            messages.append({"role": "user", "content": EXTRACT_DESIGN_PROMPT})

            system = LEAD_SYSTEM_PROMPT.format(
                model_name=MODELS[lead_model]["name"],
                title=title,
            )
            result = await self.dispatcher.chat(lead_model, messages, system=system)
            return result["content"]
        finally:
            await db.close()

    async def inject_synthesis(self, session_id: str, synthesis: dict, accepted_changes: list, rejected_changes: list) -> str:
        """Feed full synthesis results back to Lead AI and continue conversation."""
        accepted_text = "\n".join(
            f"- [{c.get('category', 'general')}] {c['description']} (from {', '.join(c.get('source_reviewers', []))})"
            for c in accepted_changes
        ) or "None"

        rejected_text = "\n".join(
            f"- [{c.get('category', 'general')}] {c['description']} — Reason: {c.get('rejection_reason', 'N/A')}"
            for c in rejected_changes
        ) or "None"

        # Build full synthesis context — no filtering
        consensus_text = "\n".join(
            f"- {c.get('point', '')} (from {', '.join(c.get('reviewers', []))})"
            for c in synthesis.get("consensus", [])
        ) or "None"

        majority_text = "\n".join(
            f"- {m.get('point', '')} (for: {', '.join(m.get('for', []))}; against: {', '.join(m.get('against', []))} — {m.get('against_reasoning', 'no reason given')})"
            for m in synthesis.get("majority", [])
        ) or "None"

        unique_text = "\n".join(
            f"- [{u.get('significance', '?')}] {u.get('insight', '')} (from {u.get('reviewer', '?')})"
            for u in synthesis.get("unique_insights", [])
        ) or "None"

        disagreements_text = "\n".join(
            f"- {d.get('topic', '')}: " + "; ".join(f"{k}: {v}" for k, v in d.get("positions", {}).items())
            for d in synthesis.get("disagreements", [])
        ) or "None"

        verdict = synthesis.get("overall_verdict", {})
        verdict_text = verdict.get("summary", "No summary available.")
        if verdict.get("ready_to_build"):
            verdict_text += " (Council says: ready to build)"
        if verdict.get("another_round_recommended"):
            verdict_text += " (Council recommends another round)"

        inject_msg = INJECT_SYNTHESIS_PROMPT.format(
            accepted_changes=accepted_text,
            rejected_changes=rejected_text,
            consensus_text=consensus_text,
            majority_text=majority_text,
            unique_text=unique_text,
            disagreements_text=disagreements_text,
            verdict_text=verdict_text,
        )

        # Save as a system-injected message
        db = await get_db()
        try:
            msg_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
                (msg_id, session_id, "user", inject_msg),
            )
            await db.commit()
        finally:
            await db.close()

        # Get Lead's response to the injected synthesis
        return await self._get_lead_response(session_id)

    async def _get_lead_response(self, session_id: str) -> str:
        """Get Lead AI response to the current message history (no new user message)."""
        db = await get_db()
        try:
            cursor = await db.execute("SELECT lead_model, title FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
            lead_model = row["lead_model"]
            title = row["title"]

            cursor = await db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )
            rows = await cursor.fetchall()
            messages = [{"role": r["role"], "content": r["content"]} for r in rows]

            system = LEAD_SYSTEM_PROMPT.format(
                model_name=MODELS[lead_model]["name"],
                title=title,
            )
            result = await self.dispatcher.chat(lead_model, messages, system=system)
            response = result["content"]

            # Save response
            resp_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
                (resp_id, session_id, "assistant", response),
            )
            await db.commit()
            return response
        finally:
            await db.close()
