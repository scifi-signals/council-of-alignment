"""Synthesis engine — Lead reads all reviews and produces structured output."""

import json
import re
from dispatcher import ModelDispatcher
from config import MODELS

SYNTHESIS_PROMPT = """You are a neutral reporter summarizing the feedback from the Council of Alignment. You have NO stake in this design. You did not create it. You are not defending it. Your only job is to faithfully represent what the reviewers actually said.

{council_count} reviewers independently reviewed this project. Their full reviews are below. Read everything carefully and report what they said.

**Ground rules:**
- You are a REPORTER, not a diplomat. Don't smooth over disagreements or soften harsh feedback. If a reviewer said something is a bad idea, report that directly.
- Use the reviewers' own words and reasoning wherever possible. Don't paraphrase their sharp points into bland summaries.
- If someone caught something others missed, highlight it — don't bury it.
- When reviewers disagree, present the STRONGEST version of each side. Don't split the difference.
- Write in plain language — like you're explaining to a smart friend, not writing a consulting report.
- CRITICAL: Every point must be written so that someone with zero technical background can understand it. No jargon without explanation.
- BE THOROUGH: Each point in consensus, majority, and unique_insights should be 2-4 sentences long. Explain WHY it matters, not just WHAT was said.
- DO NOT ADD YOUR OWN OPINIONS. Only report what the reviewers said. If you find yourself writing something no reviewer actually said, delete it.

**PLAIN LANGUAGE RULE:** For any technical term, architecture pattern, or industry concept you mention, include a brief parenthetical or one-sentence explanation of what it means and why it matters.

Bad: "The model-agnostic BYOK approach is smart."
Good: "Letting users bring their own API keys (meaning they connect their own AI accounts rather than you paying for access) and supporting multiple AI providers is smart because it keeps costs flexible and avoids vendor lock-in."

## The Full Conversation

Below is the complete conversation between the user and the Lead AI. This is everything that was discussed — nothing has been summarized or filtered.

{design}

## What the Reviewers Said

{reviews_text}

{stats_section}

{previous_changes}

---

Now produce a synthesis in EXACTLY this JSON format. No other text before or after the JSON.

IMPORTANT INSTRUCTIONS FOR DEPTH:
- Each "point" in consensus should be 2-4 sentences explaining what was agreed and why it matters
- Each "point" in majority should be 2-4 sentences, AND include "against_reasoning" with a genuine 1-2 sentence summary of the dissenter's argument — not a dismissal, but a fair representation of why they disagreed
- Each "insight" in unique_insights should be 2-3 sentences explaining what was caught, why it matters, and what the implications are
- Each "description" in proposed_changes should be 2-3 sentences with enough context that someone reading just the change list would understand the full picture
- Aim for 5-12 proposed changes, not just 3-5. Be comprehensive.

```json
{{
    "consensus": [
        {{"point": "2-4 sentence explanation of what all reviewers agree on and why it matters. Don't just name the topic — explain the reasoning.", "reviewers": ["model1", "model2", "model3"]}}
    ],
    "majority": [
        {{
            "point": "2-4 sentence explanation of the majority position — what they think and why",
            "for": ["model1", "model2"],
            "against": ["model3"],
            "against_reasoning": "1-2 sentence fair summary of why the dissenter disagrees. Steelman their position — present the strongest version of their argument, not a strawman."
        }}
    ],
    "unique_insights": [
        {{"insight": "2-3 sentence explanation of what this reviewer caught that others missed, why it matters, and what the implications are", "reviewer": "model_name", "significance": "high/medium/low"}}
    ],
    "disagreements": [
        {{"topic": "what they disagree about — 1 sentence framing the dispute", "positions": {{"model1": "2-3 sentence summary of their full position", "model2": "2-3 sentence summary of their full position"}}}}
    ],
    "proposed_changes": [
        {{
            "id": "change_001",
            "description": "Write this like you're telling a friend what to do this weekend. Start with a verb. Be specific enough that the person could act on it WITHOUT asking follow-up questions. BAD: 'Develop clear before/after demonstrations that visually highlight improvements.' GOOD: 'Take the MonopolyTrader design, run it through the Council, screenshot v1 vs v3 side by side, and post it as a Twitter thread showing what each AI caught.' If your description could apply to any random software project, it's too vague — rewrite it until it's specific to THIS project.",
            "category": "architecture|risk|cost|ux|strategy|other",
            "source_reviewers": ["model1", "model2"],
            "confidence": "consensus|majority|single",
            "rationale": "One sentence: what happens if they do this, and what happens if they don't."
        }}
    ],
    "overall_verdict": {{
        "ready_to_build": false,
        "another_round_recommended": true,
        "summary": "3-5 sentence assessment. What's the overall state of this design? What are the biggest remaining risks? What should the builder focus on next?"
    }}
}}
```

CRITICAL RULES FOR PROPOSED CHANGES:
- Start every description with an action verb: "Add...", "Remove...", "Change...", "Stop...", "Pick...", "Write...", "Cut..."
- If the change could apply to ANY software project (like "add documentation", "improve onboarding", "implement data portability", "build clean separation between layers", "create modular architecture"), it's too generic. DROP IT. Do not include it. Only include changes that are specific to THIS project and THIS situation.
- Separate strategic decisions (what to do with the project) from tactical improvements (how to make it better). Put strategic decisions first.
- Maximum 5 proposed changes. Quality over quantity. If you only have 3 strong ones, stop at 3. Do NOT pad with generic software advice to fill a quota.
- No corporate-speak. No "leverage", "enhance", "optimize", "streamline", "robust", "comprehensive", "actionable insights", "value proposition", "data portability", "clean separation", "modular architecture". Write like a human.
- SELF-CHECK: Before including each proposed change, ask yourself: "Could a consultant say this about literally any project without reading the design?" If yes, delete it. Only keep changes that prove you actually read and understood THIS specific project.
- Test each description: would a normal person read this and know exactly what to do tomorrow morning? If not, rewrite it.
- DEDUPLICATION: If a "Previously Decided Changes" section appears above, DO NOT re-propose any change that was already accepted — it's done. DO NOT re-propose rejected changes unless a reviewer explicitly argues for reconsideration with new evidence not available in the previous round. Focus only on NEW issues found in this round."""


async def synthesize_reviews(
    dispatcher: ModelDispatcher,
    lead_model: str,
    design: str,
    reviews: dict[str, dict],
    previous_changelog: list[dict] = None,
    reviewer_stats: dict = None,
) -> dict:
    """Lead reads all reviews and produces structured synthesis."""
    # Format reviews
    reviews_text = ""
    for model_key, review_data in reviews.items():
        name = MODELS[model_key]["name"]
        content = review_data.get("content", review_data) if isinstance(review_data, dict) else review_data
        reviews_text += f"### {name}'s Review\n\n{content}\n\n---\n\n"

    # Stats section
    stats_section = ""
    if reviewer_stats:
        stats_section = "## Reviewer Track Records\n\n"
        for model, stats in reviewer_stats.items():
            name = MODELS.get(model, {}).get("name", model)
            acc = stats.get("acceptance_rate", "N/A")
            stats_section += f"- {name}: {acc}% acceptance rate\n"

    previous_changes = ""
    if previous_changelog:
        previous_changes = "## Previously Decided Changes\n\n"
        previous_changes += "These changes were proposed in earlier rounds. The user has already made decisions on them.\n\n"
        for entry in previous_changelog:
            status = "ACCEPTED" if entry.get("accepted") else "REJECTED"
            reason = ""
            if not entry.get("accepted") and entry.get("rejection_reason"):
                reason = f" (reason: {entry['rejection_reason']})"
            reviewers = ", ".join(entry.get("source_reviewers", []))
            previous_changes += f"- [{status}] {entry['description']} (from: {reviewers}){reason}\n"
        previous_changes += "\n"

    prompt = SYNTHESIS_PROMPT.format(
        council_count=len(reviews),
        design=design,
        reviews_text=reviews_text,
        stats_section=stats_section,
        previous_changes=previous_changes,
    )

    messages = [{"role": "user", "content": prompt}]
    system = "You are a neutral synthesis reporter. You MUST respond with valid JSON only. No markdown fences, no explanatory text — just the JSON object."

    # Try up to 3 times to get valid JSON
    for attempt in range(3):
        result = await dispatcher.chat(lead_model, messages, system=system)
        content = result["content"]
        parsed = _parse_json(content)
        if parsed:
            return parsed

        # Retry with stricter prompt
        messages = [{"role": "user", "content": prompt + "\n\nPREVIOUS ATTEMPT HAD INVALID JSON. Return ONLY the JSON object, no other text."}]

    # Final fallback: return a minimal valid structure
    return {
        "consensus": [],
        "majority": [],
        "unique_insights": [],
        "disagreements": [],
        "proposed_changes": [],
        "overall_verdict": {
            "ready_to_build": False,
            "another_round_recommended": True,
            "summary": f"Synthesis failed after 3 attempts. Raw response: {content[:500]}",
        },
        "_raw_response": content,
    }


def _parse_json(text: str) -> dict | None:
    """Try to extract JSON from model response."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fence
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding the first { ... } block
    depth = 0
    start = None
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = None

    return None
