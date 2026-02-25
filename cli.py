"""Council of Alignment — Interactive CLI.

Usage:
    python cli.py new "Title" --lead claude
    python cli.py list
    python cli.py chat <session_id>
    python cli.py convene <session_id>
    python cli.py decide <session_id>
    python cli.py reviews <session_id> [--round N]
    python cli.py synthesis <session_id>
    python cli.py changelog <session_id>
    python cli.py timeline <session_id>
    python cli.py export <session_id> [--output ./path/]
    python cli.py stats
"""

import sys
import os
import json
import asyncio
import argparse

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from config import MODELS, get_council_models
from database import init_db
from dispatcher import ModelDispatcher
from chat_engine import ChatEngine
from briefing_generator import generate_briefing
from synthesis_engine import synthesize_reviews
from session_manager import SessionManager
from reviewer_tracker import ReviewerTracker
from file_manager import FileManager

# ANSI colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
PURPLE = "\033[35m"
GREEN = "\033[32m"
BLUE = "\033[34m"
ORANGE = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
YELLOW = "\033[33m"

MODEL_COLORS = {
    "claude": PURPLE,
    "chatgpt": GREEN,
    "gemini": BLUE,
    "grok": ORANGE,
}


def color_model(model_key: str) -> str:
    c = MODEL_COLORS.get(model_key, "")
    name = MODELS.get(model_key, {}).get("name", model_key)
    return f"{c}{BOLD}{name}{RESET}"


def print_header(text: str):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")


def print_section(text: str):
    print(f"\n{BOLD}{text}{RESET}")
    print(f"{DIM}{'─'*40}{RESET}")


# ─── Commands ───────────────────────────────────────────────

async def cmd_new(args):
    """Create a new design session."""
    await init_db()
    sm = SessionManager()
    session = await sm.create_session(args.title, args.lead)
    print_header("Council of Alignment — New Session")
    print(f"  Session ID:  {BOLD}{session['id']}{RESET}")
    print(f"  Title:       {session['title']}")
    print(f"  Lead AI:     {color_model(session['lead_model'])}")
    print(f"  Council:     {', '.join(color_model(m) for m in session['council_models'])}")
    print(f"\n  Start chatting: {BOLD}python cli.py chat {session['id']}{RESET}")


async def cmd_list(args):
    """List all sessions."""
    await init_db()
    sm = SessionManager()
    sessions = await sm.list_sessions()
    print_header("Sessions")
    if not sessions:
        print("  No sessions yet. Create one with: python cli.py new \"Title\" --lead claude")
        return
    for s in sessions:
        lead = color_model(s["lead_model"])
        print(f"  {BOLD}{s['id']}{RESET}  {s['title']}  (lead: {lead}, status: {s['status']}, {s['created_at']})")


async def cmd_chat(args):
    """Interactive chat with Lead AI."""
    await init_db()
    sm = SessionManager()
    session = await sm.get_session(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        return

    dispatcher = ModelDispatcher()
    engine = ChatEngine(dispatcher)

    print_header(f"Chat — {session['title']}")
    print(f"  Lead: {color_model(session['lead_model'])}")
    print(f"  Type your message. Commands: /convene, /quit, /cost\n")

    while True:
        try:
            user_input = input(f"{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Session saved. Goodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "/quit":
            print("  Session saved. Goodbye!")
            break

        if user_input.lower() == "/cost":
            print(f"  {DIM}{dispatcher.get_cost_summary()}{RESET}")
            continue

        if user_input.lower() == "/convene":
            print(f"\n{YELLOW}  Convening the Council...{RESET}")
            await _run_convene(session, dispatcher, sm)
            continue

        # Send to Lead AI
        lead_color = MODEL_COLORS.get(session["lead_model"], "")
        lead_name = MODELS[session["lead_model"]]["name"]
        print(f"\n{DIM}  Thinking...{RESET}", end="", flush=True)

        try:
            response = await engine.send_message(session["id"], user_input)
            print(f"\r{' '*20}\r", end="")  # Clear "Thinking..."
            print(f"\n{lead_color}{BOLD}{lead_name}:{RESET} {response}\n")
        except Exception as e:
            print(f"\r{RED}  Error: {e}{RESET}\n")


async def _run_convene(session: dict, dispatcher: ModelDispatcher, sm: SessionManager):
    """Run a full Council review cycle."""
    engine = ChatEngine(dispatcher)
    tracker = ReviewerTracker()

    session_id = session["id"]
    lead = session["lead_model"]
    council = session["council_models"]
    round_number = await sm.get_round_number(session_id)

    # Step 1: Extract design state
    print(f"  {DIM}[1/4] Extracting design state from conversation...{RESET}")
    design = await engine.get_design_state(session_id)
    version = await sm.save_version(session_id, design, "council_review")
    print(f"  {GREEN}Design extracted (v{version['version_number']}){RESET}")

    # Step 2: Generate briefing
    print(f"  {DIM}[2/4] Generating review briefing...{RESET}")
    changelog = await sm.get_changelog(session_id)
    briefing = await generate_briefing(dispatcher, lead, design, round_number, changelog)
    round_id = await sm.save_review_round(session_id, round_number, briefing)
    print(f"  {GREEN}Briefing ready for Round {round_number}{RESET}")

    # Step 3: Dispatch to Council
    print(f"  {DIM}[3/4] Dispatching to Council ({', '.join(color_model(m) for m in council)})...{RESET}")
    council_system = (
        "You are a design reviewer on the Council of Alignment. "
        "Read the design briefing and give your honest take — like a smart friend who genuinely wants this to succeed. "
        "Write in plain, conversational language. No jargon, no consultant-speak, no bullet-point walls. "
        "Say what works, what worries you, and what you'd change. Be specific and direct, "
        "but explain your reasoning like you're talking to someone over coffee, not presenting to a board."
    )
    reviews = await dispatcher.dispatch_to_council(council, council_system, briefing)

    # Save reviews
    for model_key, review_data in reviews.items():
        await sm.save_review(
            round_id, model_key, review_data["content"],
            review_data.get("tokens_in", 0), review_data.get("tokens_out", 0), review_data.get("cost", 0),
        )
    await sm.complete_review_round(round_id)
    print(f"  {GREEN}All {len(reviews)} reviews received{RESET}")

    # Print reviews
    for model_key, review_data in reviews.items():
        mc = MODEL_COLORS.get(model_key, "")
        name = MODELS[model_key]["name"]
        print(f"\n{mc}{BOLD}{'─'*50}{RESET}")
        print(f"{mc}{BOLD}{name}'s Review:{RESET}")
        print(f"{mc}{BOLD}{'─'*50}{RESET}")
        content = review_data["content"]
        # Truncate for display
        if len(content) > 2000:
            print(content[:2000] + f"\n{DIM}... [truncated, use 'reviews' command for full text]{RESET}")
        else:
            print(content)

    # Step 4: Synthesize
    print(f"\n  {DIM}[4/4] Synthesizing reviews...{RESET}")
    reviewer_stats = await tracker.get_stats_for_synthesis(council)
    synthesis = await synthesize_reviews(dispatcher, lead, design, reviews, changelog, reviewer_stats)
    await sm.save_synthesis(round_id, synthesis)
    print(f"  {GREEN}Synthesis complete{RESET}")

    # Print synthesis
    _print_synthesis(synthesis)

    # Record suggestions in tracker
    for change in synthesis.get("proposed_changes", []):
        for reviewer in change.get("source_reviewers", []):
            await tracker.record_suggestion(reviewer, change["id"], change.get("category", "other"))

    print(f"\n  {BOLD}Next: python cli.py decide {session_id}{RESET}")
    print(f"  {DIM}{dispatcher.get_cost_summary()}{RESET}")


def _print_synthesis(synthesis: dict):
    """Pretty-print the synthesis results."""
    print_section("Synthesis Results")

    # Consensus
    consensus = synthesis.get("consensus", [])
    if consensus:
        print(f"\n  {GREEN}{BOLD}Consensus ({len(consensus)}):{RESET}")
        for c in consensus:
            reviewers = ", ".join(c.get("reviewers", []))
            print(f"    {GREEN}+{RESET} {c['point']} {DIM}({reviewers}){RESET}")

    # Majority
    majority = synthesis.get("majority", [])
    if majority:
        print(f"\n  {BLUE}{BOLD}Majority ({len(majority)}):{RESET}")
        for m in majority:
            print(f"    {BLUE}>{RESET} {m['point']}")
            print(f"      {DIM}For: {', '.join(m.get('for', []))} | Against: {', '.join(m.get('against', []))}{RESET}")

    # Unique insights
    unique = synthesis.get("unique_insights", [])
    if unique:
        print(f"\n  {PURPLE}{BOLD}Unique Insights ({len(unique)}):{RESET}")
        for u in unique:
            sig = u.get("significance", "medium")
            sig_color = RED if sig == "high" else YELLOW if sig == "medium" else DIM
            print(f"    {PURPLE}*{RESET} {u['insight']} {DIM}(by {u.get('reviewer', '?')}, {sig_color}{sig}{RESET})")

    # Disagreements
    disagreements = synthesis.get("disagreements", [])
    if disagreements:
        print(f"\n  {ORANGE}{BOLD}Disagreements ({len(disagreements)}):{RESET}")
        for d in disagreements:
            print(f"    {ORANGE}!{RESET} {d['topic']}")
            for model, pos in d.get("positions", {}).items():
                print(f"      {DIM}{model}: {pos}{RESET}")

    # Proposed changes
    changes = synthesis.get("proposed_changes", [])
    if changes:
        print(f"\n  {BOLD}Proposed Changes ({len(changes)}):{RESET}")
        for i, c in enumerate(changes, 1):
            conf = c.get("confidence", "?")
            conf_color = GREEN if conf == "consensus" else BLUE if conf == "majority" else DIM
            print(f"    {i}. [{c.get('category', 'other')}] {c['description']}")
            print(f"       {DIM}Source: {', '.join(c.get('source_reviewers', []))} | {conf_color}{conf}{RESET}")

    # Verdict
    verdict = synthesis.get("overall_verdict", {})
    if verdict:
        print(f"\n  {BOLD}Verdict:{RESET}")
        ready = verdict.get("ready_to_build", False)
        another = verdict.get("another_round_recommended", True)
        print(f"    Ready to build: {'Yes' if ready else 'Not yet'}")
        print(f"    Another round recommended: {'Yes' if another else 'No'}")
        print(f"    {verdict.get('summary', '')}")


async def cmd_convene(args):
    """Run a Council review cycle (standalone)."""
    await init_db()
    sm = SessionManager()
    session = await sm.get_session(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        return

    dispatcher = ModelDispatcher()
    print_header(f"Convening the Council — {session['title']}")
    await _run_convene(session, dispatcher, sm)


async def cmd_decide(args):
    """Interactively accept/reject proposed changes."""
    await init_db()
    sm = SessionManager()
    session = await sm.get_session(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        return

    synthesis = await sm.get_latest_synthesis(session["id"])
    if not synthesis:
        print(f"{RED}No synthesis found. Run 'convene' first.{RESET}")
        return

    changes = synthesis.get("proposed_changes", [])
    if not changes:
        print("No proposed changes to decide on.")
        return

    # Get current round number
    round_number = await sm.get_round_number(session["id"]) - 1  # Current (completed) round

    print_header(f"Decide — {session['title']} (Round {round_number})")

    accepted = []
    rejected = []
    tracker = ReviewerTracker()

    for i, change in enumerate(changes, 1):
        conf = change.get("confidence", "?")
        print(f"\n  {BOLD}Change {i}/{len(changes)}:{RESET}")
        print(f"  [{change.get('category', 'other')}] {change['description']}")
        print(f"  {DIM}Source: {', '.join(change.get('source_reviewers', []))} | Confidence: {conf}{RESET}")
        if change.get("rationale"):
            print(f"  {DIM}Rationale: {change['rationale']}{RESET}")

        while True:
            try:
                choice = input(f"\n  {BOLD}Accept (a), Reject (r), or Skip (s)?{RESET} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Aborted.")
                return

            if choice in ("a", "accept"):
                accepted.append(change)
                await sm.save_changelog_entry(session["id"], round_number, change, True)
                await tracker.record_decision(change["id"], True)
                for reviewer in change.get("source_reviewers", []):
                    await tracker.record_decision(change["id"], True)
                print(f"  {GREEN}Accepted{RESET}")
                break
            elif choice in ("r", "reject"):
                reason = input(f"  {DIM}Reason (optional):{RESET} ").strip()
                change["rejection_reason"] = reason or None
                rejected.append(change)
                await sm.save_changelog_entry(session["id"], round_number, change, False, reason)
                await tracker.record_decision(change["id"], False)
                print(f"  {RED}Rejected{RESET}")
                break
            elif choice in ("s", "skip"):
                print(f"  {DIM}Skipped{RESET}")
                break
            else:
                print(f"  {DIM}Enter a, r, or s{RESET}")

    # Summary
    print(f"\n{BOLD}Decision Summary:{RESET}")
    print(f"  {GREEN}Accepted: {len(accepted)}{RESET}")
    print(f"  {RED}Rejected: {len(rejected)}{RESET}")
    print(f"  {DIM}Skipped: {len(changes) - len(accepted) - len(rejected)}{RESET}")

    # Inject into Lead AI conversation
    if accepted:
        print(f"\n  {DIM}Injecting decisions into Lead conversation...{RESET}")
        dispatcher = ModelDispatcher()
        engine = ChatEngine(dispatcher)
        response = await engine.inject_synthesis(session["id"], synthesis, accepted, rejected)
        lead_color = MODEL_COLORS.get(session["lead_model"], "")
        lead_name = MODELS[session["lead_model"]]["name"]
        print(f"\n{lead_color}{BOLD}{lead_name}:{RESET} {response}")

    print(f"\n  Continue chatting: {BOLD}python cli.py chat {session['id']}{RESET}")


async def cmd_reviews(args):
    """Show reviews for a session."""
    await init_db()
    sm = SessionManager()
    session = await sm.get_session(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        return

    reviews = await sm.get_reviews(session["id"], round_number=args.round)
    if not reviews:
        print("No reviews found.")
        return

    print_header(f"Reviews — {session['title']}")
    for r in reviews:
        mc = MODEL_COLORS.get(r["model_name"], "")
        name = MODELS.get(r["model_name"], {}).get("name", r["model_name"])
        print(f"\n{mc}{BOLD}{'─'*50}{RESET}")
        print(f"{mc}{BOLD}{name} (Round {r['round_number']}):{RESET}")
        print(f"{mc}{BOLD}{'─'*50}{RESET}")
        print(r["response"])
        if r.get("cost_estimate"):
            print(f"\n{DIM}  Tokens: {r['tokens_in']} in / {r['tokens_out']} out | ${r['cost_estimate']:.4f}{RESET}")


async def cmd_synthesis(args):
    """Show latest synthesis."""
    await init_db()
    sm = SessionManager()
    session = await sm.get_session(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        return

    synthesis = await sm.get_latest_synthesis(session["id"])
    if not synthesis:
        print("No synthesis found.")
        return

    print_header(f"Synthesis — {session['title']}")
    _print_synthesis(synthesis)


async def cmd_changelog(args):
    """Show changelog."""
    await init_db()
    sm = SessionManager()
    session = await sm.get_session(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        return

    changelog = await sm.get_changelog(session["id"])
    if not changelog:
        print("No changelog entries yet.")
        return

    print_header(f"Changelog — {session['title']}")
    current_round = None
    for c in changelog:
        if c["round_number"] != current_round:
            current_round = c["round_number"]
            print(f"\n  {BOLD}Round {current_round}{RESET}")
        status = f"{GREEN}Accepted{RESET}" if c["accepted"] else f"{RED}Rejected{RESET}"
        reviewers = ", ".join(MODELS.get(r, {}).get("name", r) for r in c.get("source_reviewers", []))
        print(f"    [{c['category']}] {c['description']} — {status} {DIM}(by {reviewers}){RESET}")
        if c.get("rejection_reason"):
            print(f"      {DIM}Reason: {c['rejection_reason']}{RESET}")


async def cmd_timeline(args):
    """Show version timeline."""
    await init_db()
    sm = SessionManager()
    session = await sm.get_session(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        return

    timeline = await sm.get_timeline(session["id"])
    if not timeline:
        print("No versions yet.")
        return

    print_header(f"Timeline — {session['title']}")
    for v in timeline:
        print(f"  {BOLD}v{v['version_number']}{RESET} — {v['created_at']} ({v['created_from']})")
        print(f"    {DIM}{v['content_preview']}{RESET}")


async def cmd_export(args):
    """Export design files to disk."""
    await init_db()
    sm = SessionManager()
    session = await sm.get_session(args.session_id)
    if not session:
        print(f"{RED}Session not found: {args.session_id}{RESET}")
        return

    latest = await sm.get_latest_version(session["id"])
    changelog = await sm.get_changelog(session["id"])
    fm = FileManager()
    files = await fm.generate_all_files(session, latest, changelog)

    output_dir = args.output or "."
    written = fm.write_files(files, output_dir)

    print_header(f"Export — {session['title']}")
    for path in written:
        print(f"  {GREEN}Written:{RESET} {path}")


async def cmd_stats(args):
    """Show reviewer performance stats."""
    await init_db()
    tracker = ReviewerTracker()
    stats = await tracker.get_stats()

    print_header("Reviewer Performance Stats")
    if not stats:
        print("  No stats yet. Run some Council reviews first.")
        return

    for model_key, s in stats.items():
        mc = MODEL_COLORS.get(model_key, "")
        name = s.get("display_name", model_key)
        print(f"\n  {mc}{BOLD}{name}{RESET}")
        print(f"    Suggestions: {s['total_suggestions']}")
        print(f"    Accepted: {GREEN}{s['accepted']}{RESET} / Rejected: {RED}{s['rejected']}{RESET} / Pending: {s['pending']}")
        print(f"    Acceptance rate: {BOLD}{s['acceptance_rate']}%{RESET}")
        if s.get("categories"):
            cats = ", ".join(f"{k}: {v}" for k, v in s["categories"].items())
            print(f"    Categories: {DIM}{cats}{RESET}")


# ─── Main ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Council of Alignment — Multi-model design review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # new
    p_new = subparsers.add_parser("new", help="Create a new session")
    p_new.add_argument("title", help="Project title")
    p_new.add_argument("--lead", required=True, choices=list(MODELS.keys()), help="Lead AI model")

    # list
    subparsers.add_parser("list", help="List all sessions")

    # chat
    p_chat = subparsers.add_parser("chat", help="Chat with Lead AI")
    p_chat.add_argument("session_id", help="Session ID")

    # convene
    p_convene = subparsers.add_parser("convene", help="Convene the Council for review")
    p_convene.add_argument("session_id", help="Session ID")

    # decide
    p_decide = subparsers.add_parser("decide", help="Accept/reject proposed changes")
    p_decide.add_argument("session_id", help="Session ID")

    # reviews
    p_reviews = subparsers.add_parser("reviews", help="Show reviews")
    p_reviews.add_argument("session_id", help="Session ID")
    p_reviews.add_argument("--round", type=int, default=None, help="Filter by round number")

    # synthesis
    p_synth = subparsers.add_parser("synthesis", help="Show latest synthesis")
    p_synth.add_argument("session_id", help="Session ID")

    # changelog
    p_cl = subparsers.add_parser("changelog", help="Show changelog")
    p_cl.add_argument("session_id", help="Session ID")

    # timeline
    p_tl = subparsers.add_parser("timeline", help="Show version timeline")
    p_tl.add_argument("session_id", help="Session ID")

    # export
    p_export = subparsers.add_parser("export", help="Export design files")
    p_export.add_argument("session_id", help="Session ID")
    p_export.add_argument("--output", default=".", help="Output directory")

    # stats
    subparsers.add_parser("stats", help="Reviewer performance stats")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "new": cmd_new,
        "list": cmd_list,
        "chat": cmd_chat,
        "convene": cmd_convene,
        "decide": cmd_decide,
        "reviews": cmd_reviews,
        "synthesis": cmd_synthesis,
        "changelog": cmd_changelog,
        "timeline": cmd_timeline,
        "export": cmd_export,
        "stats": cmd_stats,
    }

    asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    main()
