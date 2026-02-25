"""Briefing generator — packages the design for Council review."""

from dispatcher import ModelDispatcher
from config import MODELS

BRIEFING_PROMPT_ROUND_1 = """You're packaging up a design for review by a panel of AI models.

Below is the current design. Turn it into a clear briefing that covers:

1. **What we're building** — Plain-language overview, no buzzwords
2. **Big decisions so far** — The main choices and why they were made
3. **Where we're unsure** — Things that could go either way
4. **Questions for reviewers** — 5-7 specific things you want their take on

Write this like you're catching up a smart colleague who's seeing this for the first time. Keep it clear and readable — no walls of jargon.

## Current Design

{design}

---

End the briefing with: "Give it to us straight. This is Round 1 — we want honest reactions, not polite nods."
"""

BRIEFING_PROMPT_ROUND_N = """You're packaging up a design for Round {round_number} of review.

This design has been reviewed before and updated based on feedback. Create an updated briefing that covers:

1. **What we're building** — Plain-language overview (updated)
2. **What changed since last time** — Specific updates based on reviewer feedback
3. **Change log** — Who suggested what, and whether it was accepted
4. **Big decisions right now** — Current key choices
5. **Where we're still unsure** — Remaining open questions
6. **Questions for this round** — 5-7 questions focused on the changes and remaining gaps

Write conversationally — like you're catching someone up, not writing a formal document.

## Current Design

{design}

## Changes Since Last Round

{changelog}

{custom_questions_section}

---

End the briefing with: "Give it to us straight. This is Round {round_number} — focus on whether the changes actually fix what was flagged last time. Don't be polite about it."
"""


async def generate_briefing(
    dispatcher: ModelDispatcher,
    lead_model: str,
    design: str,
    round_number: int,
    previous_changelog: list[dict] = None,
    custom_questions: list[str] = None,
) -> str:
    """Use the Lead AI to generate a review briefing from the current design."""
    if round_number == 1:
        prompt = BRIEFING_PROMPT_ROUND_1.format(design=design)
    else:
        changelog_text = "\n".join(
            f"- [{c.get('category', 'general')}] {c['description']} "
            f"(source: {c.get('source_reviewers', 'unknown')}, "
            f"{'accepted' if c.get('accepted') else 'rejected'})"
            for c in (previous_changelog or [])
        ) or "No changes recorded."

        custom_q = ""
        if custom_questions:
            custom_q = "## Additional Questions from the Designer\n\n" + "\n".join(
                f"- {q}" for q in custom_questions
            )

        prompt = BRIEFING_PROMPT_ROUND_N.format(
            round_number=round_number,
            design=design,
            changelog=changelog_text,
            custom_questions_section=custom_q,
        )

    messages = [{"role": "user", "content": prompt}]
    system = f"You are {MODELS[lead_model]['name']}, preparing a design review briefing."
    result = await dispatcher.chat(lead_model, messages, system=system)
    return result["content"]
