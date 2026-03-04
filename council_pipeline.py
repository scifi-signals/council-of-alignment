"""Council pipeline — extracted convene logic shared by web UI and API v1."""

import json
import logging

from config import MODELS
from database import get_db
from attachment_context import build_attachment_context
from synthesis_engine import synthesize_reviews

logger = logging.getLogger(__name__)

# Per-session lock — prevents concurrent convene calls.
# Shared between web UI (app.py) and API v1 (api_v1.py).
_convene_locks: set[str] = set()


def is_locked(session_id: str) -> bool:
    return session_id in _convene_locks


def acquire_lock(session_id: str) -> bool:
    """Try to acquire the convene lock. Returns False if already held."""
    if session_id in _convene_locks:
        return False
    _convene_locks.add(session_id)
    return True


def release_lock(session_id: str) -> None:
    _convene_locks.discard(session_id)


async def run_council_review(
    session_id: str,
    session: dict,
    sm,
    dispatcher,
    tracker,
    github_ctx,
    api_key_override: str = None,
) -> dict:
    """Run a full Council review cycle.

    Returns dict with keys:
        session_id, round_number, reviews, synthesis, briefing_length, web_url
    """
    lead = session["lead_model"]
    council = session["council_models"]
    round_number = await sm.get_round_number(session_id)

    # ── Step 1: Build raw conversation as briefing ────────────
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        rows = await cursor.fetchall()

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

    # Changelog context for round 2+
    changelog = await sm.get_changelog(session_id)
    changelog_section = ""
    if changelog and round_number > 1:
        changelog_lines = []
        for c in changelog:
            status = "accepted" if c.get("accepted") else "rejected"
            reviewers = ", ".join(c.get("source_reviewers", []))
            changelog_lines.append(
                f"- [{c.get('category', 'general')}] {c['description']} (from {reviewers}, {status})"
            )
        changelog_section = "\n\n## Changes From Previous Round\n\n" + "\n".join(changelog_lines)

    attachment_section = build_attachment_context(att_rows, heading="Reference Codebase")

    # ── GitHub auto-context ───────────────────────────────────
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
                    not_included = [p for p in tree if p not in selected_paths]
                    if not_included:
                        github_section += (
                            f"\n\n### Other Files in Repository (not loaded — request if needed)\n"
                            f"```\n" + "\n".join(sorted(not_included)) + "\n```\n"
                        )
        except Exception as e:
            logger.warning("GitHub auto-context failed: %s", type(e).__name__)

    briefing = f"""## Design Review — Round {round_number}

Below is the full conversation between the user and {lead_name} (the Lead AI). This is everything that was discussed — nothing has been summarized or filtered. Read it all carefully before giving your review.

## Full Conversation

{raw_conversation}
{changelog_section}
{github_section}
{attachment_section}

---

Question everything. Find what's missing, not just what's present. If code is included, trace every data flow to its endpoint. If this is a design or product concept, think about whether a real person would actually use it."""

    # ── Step 1b: Lead AI gap scan ─────────────────────────────
    gap_scan_prompt = (
        "You are preparing a design brief for review by a panel of AI reviewers. "
        "Scan the conversation above and identify which of the following dimensions are "
        "NOT addressed or are significantly underspecified. Only list the ones that are "
        "missing AND relevant to this particular project — skip any that don't apply.\n\n"
        "Dimensions to check:\n"
        "- Problem definition and target user\n"
        "- User journey (first visit through regular use)\n"
        "- Onboarding experience\n"
        "- Core functionality and feature scope\n"
        "- Error handling and edge cases\n"
        "- Data privacy and trust\n"
        "- Technical architecture\n"
        "- Business model / monetization\n"
        "- Competitive positioning\n"
        "- Scope boundaries (what it does NOT do)\n"
        "- Accessibility\n"
        "- Testing and validation plan\n\n"
        "Return ONLY the missing dimensions as a short bulleted list with one sentence each "
        "explaining why it matters for this project. If nothing important is missing, say "
        "'No significant gaps identified.' Be concise."
    )
    try:
        gap_result = await dispatcher.chat(
            lead, [{"role": "user", "content": briefing}], system=gap_scan_prompt,
            api_key_override=api_key_override,
        )
        gap_content = gap_result.get("content", "").strip()
        if gap_content and "no significant gaps" not in gap_content.lower():
            briefing = (
                f"{briefing}\n\n"
                f"---\n\n"
                f"## Lead AI Gap Analysis\n\n"
                f"The Lead AI identified the following dimensions that are not addressed "
                f"in the conversation above. Reviewers should consider whether these are "
                f"relevant gaps.\n\n"
                f"{gap_content}"
            )
    except Exception as e:
        logger.warning("Lead AI gap scan failed: %s", type(e).__name__)

    # Save version + round
    version = await sm.save_version(session_id, raw_conversation, "council_review")
    round_id = await sm.save_review_round(session_id, round_number, briefing)

    # ── Step 2: Dispatch to Council ───────────────────────────
    council_system = (
        "You are a design reviewer on the Council of Alignment. "
        "You're about to read the full conversation between a user and their Lead AI, "
        "along with source code from the codebase (if provided).\n\n"

        "YOUR JOB IS TO QUESTION AND VALIDATE.\n\n"

        "IDENTIFY WHAT'S ABSENT:\n"
        "Before reviewing what's written, identify anything important that is completely absent "
        "from this document. The most critical gaps are often things nobody thought to include. "
        "If nothing important is missing, move on.\n\n"

        "ADAPT YOUR REVIEW TO THE MATERIAL:\n"
        "Look at what you've been given. If it includes source code or a codebase, apply the "
        "Code Review rules below. If it's a design brief, product concept, or idea without code, "
        "apply the Design Review rules. If it's a mix, apply both.\n\n"

        "## WHEN CODE IS PRESENT — DATA FLOW TRACING\n\n"

        "You are a data flow tracer, NOT a code quality reviewer. The primary question is: "
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

        "THESE ARE NOT FINDINGS — do not report them:\n"
        "- A feature disabled via config flag ('enabled': false) is a feature toggle, not a dead end\n"
        "- An error handler that logs a warning and returns a fallback value is graceful degradation, not a silent failure\n"
        "- A config file with initial values is not 'stale state' if code updates those values at runtime\n"
        "- A JSON config with starting defaults is not a 'contradiction' with code that modifies them during execution\n"
        "- A file you didn't read is not evidence of a broken chain — it's a gap in your visibility\n\n"

        "## WHEN REVIEWING A DESIGN BRIEF OR PRODUCT CONCEPT\n\n"

        "Think beyond technical architecture. Your job is to stress-test the idea as something "
        "a real person would encounter and use. Ask:\n"
        "- What happens when someone opens this for the first time? Is the onboarding clear?\n"
        "- Walk through the full user journey — first visit to regular use. Where does it break down?\n"
        "- Would the target user understand what this is and why they should care?\n"
        "- What's the competitive landscape? Why would someone choose this over alternatives?\n"
        "- Is the business model or monetization strategy viable?\n"
        "- What are the biggest risks to adoption?\n\n"

        "## FOR ALL REVIEWS\n\n"

        "FINDING NOTHING IS A VALID OUTCOME:\n"
        "If everything is sound, say so and be done. A short response finding zero issues is more "
        "valuable than a long response inventing problems. Do not pad your review to match some "
        "expected length. Do not reframe intentional design choices as bugs.\n\n"

        "BE THOROUGH:\n"
        "Do not wrap up early if you have more genuine observations. Do not self-edit for response "
        "length. It is better to surface 10 real issues than to stop at 5 because the response "
        "feels long enough. But every finding must be real — padding is worse than brevity.\n\n"

        "DO NOT SUGGEST GENERIC IMPROVEMENTS. If something works, do not add 'but it could be more "
        "sophisticated' or 'there's an opportunity to enhance.' No vague 'areas for improvement.' "
        "However, DO identify substantive gaps — things that are missing, broken, or would prevent "
        "real people from using the product. There's a difference between 'you could add caching' "
        "(generic improvement — skip it) and 'there's no onboarding flow, so new users will have "
        "no idea what to do' (substantive gap — flag it).\n\n"

        "Write in plain, conversational language. No jargon, no consultant-speak, no bullet-point walls. "
        "Explain your reasoning like you're talking to someone over coffee.\n\n"

        "PLAIN LANGUAGE RULE: For any technical term, architecture pattern, or industry concept you mention, "
        "include a brief parenthetical or one-sentence explanation of what it means and why it matters. "
        "Write as if the reader is smart but not necessarily familiar with every term. "
        "Clarity over brevity — don't sacrifice understanding to sound concise.\n\n"
        "Bad: 'The model-agnostic BYOK approach is smart.'\n"
        "Good: 'Letting users bring their own API keys (meaning they connect their own AI accounts rather than "
        "you paying for access) and supporting multiple AI providers is smart because it keeps costs flexible "
        "and avoids vendor lock-in.'\n\n"

        "FINAL CHECK — REVIEW LENSES:\n"
        "After writing your review, briefly check your response against these lenses. For each one, "
        "note anything you missed. Skip any lens where you have nothing to add.\n"
        "- Architecture: Is the system well-designed? Are components properly connected?\n"
        "- Product/UX: Walk through the user journey. What's missing from the experience?\n"
        "- Strategy: Is this viable? Who's the competition? What's the biggest adoption risk?\n"
        "- Devil's Advocate: What assumptions might be false? Why might this fail entirely?"
    )
    reviews = await dispatcher.dispatch_to_council(council, council_system, briefing, api_key_override=api_key_override)

    # Save reviews
    for model_key, review_data in reviews.items():
        await sm.save_review(
            round_id, model_key, review_data["content"],
            review_data.get("tokens_in", 0),
            review_data.get("tokens_out", 0),
            review_data.get("cost", 0),
        )
    await sm.complete_review_round(round_id)

    # ── Step 3: Synthesize ────────────────────────────────────
    reviewer_stats = await tracker.get_stats_for_synthesis(council)
    synthesis = await synthesize_reviews(
        dispatcher, lead, raw_conversation, reviews, changelog, reviewer_stats,
        api_key_override=api_key_override,
    )
    await sm.save_synthesis(round_id, synthesis)

    # Record suggestions
    for change in synthesis.get("proposed_changes", []):
        for reviewer in change.get("source_reviewers", []):
            await tracker.record_suggestion(
                reviewer, change["id"], change.get("category", "other")
            )

    return {
        "session_id": session_id,
        "round_number": round_number,
        "reviews": {k: {"content": v["content"]} for k, v in reviews.items()},
        "synthesis": synthesis,
        "briefing_length": len(briefing),
    }
