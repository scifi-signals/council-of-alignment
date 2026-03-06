"""Council of Alignment — MCP Server (FastMCP, stdio transport).

Exposes Council review tools to Claude Code and other MCP-compatible clients.
Requires COUNCIL_API_URL and COUNCIL_API_KEY environment variables.
"""

from mcp.server.fastmcp import FastMCP
from council_of_alignment.client import CouncilClient

mcp = FastMCP("council-of-alignment")


def _client() -> CouncilClient:
    return CouncilClient()


@mcp.tool()
async def council_create_session(title: str, lead_model: str = "claude") -> dict:
    """Create a new Council review session.

    Args:
        title: A short descriptive title (e.g. "Auth System Review")
        lead_model: Which AI leads the conversation. Options: claude, chatgpt, gemini, grok.
                    Use 'gemini' or 'chatgpt' as lead if you want Claude as a reviewer.
                    Defaults to 'claude'.

    Returns: session_id, title, lead_model, council_models, web_url
    """
    return await _client().create_session(title, lead_model)


@mcp.tool()
async def council_add_files(session_id: str, files: list[dict]) -> dict:
    """Attach source files to a Council session for review.

    CRITICAL: Include ALL relevant files — the source under review plus its
    dependencies, callers, tests, and config. More context = better reviews.
    The Council can handle large codebases.

    Args:
        session_id: The session to attach files to
        files: List of {filename: str, content: str} objects.
               Use the full relative path as filename (e.g. "src/auth.py").

    Returns: attached count and filenames
    """
    return await _client().add_files(session_id, files)


@mcp.tool()
async def council_send_message(session_id: str, message: str) -> dict:
    """Send a message to the Lead AI to frame the review.

    This is how you tell the Lead AI what to focus on. A good message includes:
    - What's being built and its current state
    - What specific aspect to review (architecture, security, UX, etc.)
    - Any known concerns or constraints
    - What kind of feedback would be most useful

    The Lead AI will respond with its analysis. You can send multiple messages
    to refine the brief before convening the Council.

    Args:
        session_id: The session to message
        message: Your message to the Lead AI

    Returns: Lead AI response text and verification status
    """
    return await _client().send_message(session_id, message)


@mcp.tool()
async def council_convene(session_id: str) -> dict:
    """Convene the full Council for review. Takes 3-5 minutes.

    This runs the complete review pipeline:
    1. Packages the conversation + attached files as a briefing
    2. Lead AI scans for gaps in the brief
    3. Three reviewer AIs analyze everything in parallel
    4. Lead AI synthesizes all reviews into consensus/disagreements/proposals

    IMPORTANT: Make sure files are attached and at least one message has been
    sent before convening. The Council reviews what's in the session.

    Args:
        session_id: The session to convene

    Returns: round_number, synthesis (with proposed_changes), reviews, web_url
    """
    return await _client().convene(session_id)


@mcp.tool()
async def council_get_results(session_id: str) -> dict:
    """Get results from a completed Council review.

    Use this to retrieve synthesis and reviews after convening,
    or to re-read results from a previous session.

    Args:
        session_id: The session to get results for

    Returns: round_number, synthesis, reviews, web_url
    """
    return await _client().get_results(session_id)


@mcp.tool()
async def council_decide(session_id: str, decisions: list[dict]) -> dict:
    """Accept or reject proposed changes from the Council review.

    After reviewing the synthesis, decide on each proposed change.
    Accepted changes are fed back to the Lead AI to incorporate.

    Args:
        session_id: The session with pending decisions
        decisions: List of {id: str, accepted: bool, reason: str} objects.
                   The id comes from proposed_changes in the synthesis.
                   Reason is optional but helpful for rejected changes.

    Returns: accepted count, rejected count, lead_response
    """
    return await _client().decide(session_id, decisions)


@mcp.tool()
async def council_list_sessions() -> dict:
    """List recent Council sessions.

    Returns: list of sessions with id, title, status, created_at, web_url
    """
    return await _client().list_sessions()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
