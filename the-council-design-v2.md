# Council of Alignment — Design Document (v2)

## What Is This?

The Council of Alignment is an iterative multi-model design review platform. You work with a Primary AI to create a design, then convene the Council — dispatching your design to multiple AI reviewers who independently critique it. Their feedback is synthesized into consensus, disagreements, and proposed changes with full attribution. You approve changes, the design updates, and you can run another round. Each round compounds.

The name is an AI safety joke: are the models aligned yet? Convene the Council of Alignment and find out.

**What it is NOT:** A side-by-side comparison tool. A chatbot. A "ask 4 AIs the same question" novelty. Those exist. This is a design governance workflow.

---

## Changes from v1 → v2

| Change | Category | Source |
|--------|----------|--------|
| Added reviewer performance tracking (acceptance rate, survival rate across rounds) | structural | ChatGPT |
| Added synthesis bias mitigation (Primary AI instructed to steelman opposing views) | quality | Gemini |
| Added optional Roundtable Debate mode (reviewers see and respond to each other once) | feature | Grok |
| Consider HTMX for Evolution Timeline instead of vanilla JS or React | tech | Gemini |
| Added "Devil's Advocate" role option for one reviewer | feature | Gemini |
| Free tier refined: 1 project, 2 rounds, 2 reviewers | pricing | ChatGPT + Grok |
| Pricing set at $15/month BYOK (not $10-20 range) | pricing | All three |
| Kill criteria defined: <50 paying users after 3 months of web UI launch | business | All three (synthesized) |
| Narrowed positioning: "Design Review for Serious Builders" | positioning | All three |
| Added confidence weighting by reviewer historical accuracy | structural | ChatGPT |

---

## The Problem

If you want multiple AI perspectives on a project today, you manually copy-paste between tabs, mentally synthesize feedback, lose track of what changed, and give up after one round.

Existing tools (Council AI, Roundtable, multiple.chat) do side-by-side comparison — ask 4 models at once, see 4 responses. That's a novelty. None do iterative refinement with tracked changes across rounds.

## The Insight

The value isn't in any single model's response. It's in the compounding effect of multiple rounds:
- Round 1: Catches broad structural issues
- Round 2: Catches second-order effects from the v1 fixes
- Round 3: Converges on readiness

Each round gets sharper because reviewers see what changed from their own previous feedback.

## Positioning [v2 CHANGE]

**"Design Review for Serious Builders."**

This is not for casual ChatGPT dabblers. It's for people who:
- Design serious systems (software architecture, APIs, protocols)
- Care about rigor (researchers, technical founders)
- Already feel the pain of tab chaos
- Believe iteration matters

Expand to writing, legal, research, and code review AFTER establishing traction in design review.

---

## How It Works

### User Workflow
```
1. WORK with Primary AI to create a design/plan/document
2. CLICK "Convene the Council" → auto-generates a clean review briefing
3. DISPATCH briefing to 3-4 reviewer models simultaneously
4. REVIEWS come back independently
5. SYNTHESIZE — Primary AI identifies consensus, majority, unique insights, 
   disagreements (with bias mitigation)
6. PROPOSE specific changes with attribution
7. DECIDE — user accepts/rejects/modifies each proposed change
8. UPDATE design, changelog records what changed and why
9. REPEAT or ship
```

### Optional: Roundtable Debate Mode [v2 NEW — Grok suggestion]

For power users who want deeper critique:
```
After step 4 (reviews come back), optionally:
4b. Share anonymized review summaries with all reviewers
4c. Each reviewer gets ONE response to critique the others
4d. Then proceed to step 5 (synthesis) with the richer input
```

This adds ~$0.10/round in API costs and one extra wait cycle. Default is OFF. Toggle it on for high-stakes designs where second-order effects matter.

---

## Architecture

```
┌────────────────────────────────────────────┐
│              WEB FRONTEND                   │
│  Workspace · Council View · Timeline        │
│  (HTMX for reactivity, no React needed)    │
├────────────────────────────────────────────┤
│             PYTHON BACKEND                  │
│                                             │
│  Session Manager — project lifecycle        │
│  Briefing Generator — packages design       │
│  Multi-Model Dispatcher — parallel API      │
│  Synthesis Engine — reads all reviews       │
│     ↳ Bias mitigation prompting [v2]        │
│     ↳ Confidence weighting [v2]             │
│  Reviewer Tracker — performance stats [v2]  │
│  Version Control — stores each version      │
│  Changelog Generator — attribution          │
│  Export Engine — markdown/PDF/CLAUDE.md      │
├────────────────────────────────────────────┤
│  APIs: Anthropic, OpenAI, Google, xAI       │
│  (or OpenRouter as unified gateway)         │
├────────────────────────────────────────────┤
│  SQLite: sessions, versions, reviews,       │
│          reviewer_stats [v2]                │
└────────────────────────────────────────────┘
```

**Tech stack**: Python 3.11+ / FastAPI / HTMX + vanilla JS / SQLite

---

## Core Modules

### 1. session_manager.py — Project Lifecycle

Tracks sessions (projects), versions, review rounds, and changelog. Each session has a title, primary model, reviewer models, current version, and status.

### 2. briefing_generator.py — Package Design for Review

Auto-generates a clean review briefing from the current design. This is critical infrastructure — most users are bad at prompting reviewers.

**Round 1**: Design + generic review questions
**Round 2+**: Design + changelog + targeted questions based on previous gaps + "here's what changed from your last review"

### 3. dispatcher.py — Multi-Model API Manager

Sends briefings to all reviewers in parallel. Handles API differences between providers. Supports both direct API keys and OpenRouter as unified gateway.

**Reviewer personality prompts** (light touch, not heavy-handed):
- Encourage each model's natural strengths
- Customizable by the user
- Optional "Devil's Advocate" role: one reviewer is specifically instructed to find problems, disagree, and stress-test assumptions [v2 NEW]

### 4. synthesis_engine.py — The Brain

Primary AI reads all reviews and produces structured analysis:
- **Consensus**: All reviewers agree → high confidence
- **Majority**: 2-3 agree → note dissenter's reasoning
- **Unique insights**: One reviewer caught something others missed
- **Disagreements**: Reviewers conflict → flag for human decision
- **Proposed changes**: Specific, actionable, categorized, attributed
- **Readiness verdict**: Ready to build, or needs another round?

**Synthesis bias mitigation [v2 NEW — Gemini]:**
The synthesis prompt explicitly instructs the Primary AI to:
- Steelman suggestions that contradict its own tendencies
- Weight all reviewers equally regardless of provider
- Flag when it suspects its own bias might be influencing the synthesis
- Present disagreements as genuine tradeoffs, not "right vs wrong"

**Confidence weighting [v2 NEW — ChatGPT]:**
If a reviewer consistently suggests changes that the user accepts and that survive future rounds, their suggestions get a higher confidence weight in the synthesis. Over time, the system learns which reviewers are most reliable for which types of feedback.

### 5. reviewer_tracker.py [v2 NEW — ChatGPT]

Tracks reviewer performance across all projects:

```python
class ReviewerStats:
    model_name: str
    total_suggestions: int
    accepted_suggestions: int          # User approved
    survived_suggestions: int          # Still in design after 2+ rounds
    acceptance_rate: float
    survival_rate: float
    strongest_categories: list[str]    # "risk", "structural", "cost"
    weakest_categories: list[str]      # Categories where suggestions get rejected most
```

This data is displayed in the dashboard and used to weight confidence in synthesis. Over time, users learn: "Gemini is great at cost analysis, Grok catches structural issues, ChatGPT finds edge cases."

### 6. changelog_generator.py — Track the Evolution

Every change tracked with: round number, category, description, source reviewer(s), confidence level, accepted/rejected, rejection reason.

### 7. export_engine.py — Output Final Deliverables

- Final design (markdown or PDF)
- Full changelog across all rounds
- Attribution summary
- CLAUDE.md export (ready for Claude Code)
- Reviewer performance summary

---

## Frontend

### Workspace View
Chat interface for working with Primary AI. "Convene the Council" button. Model selector for reviewers. Custom questions input. Optional Devil's Advocate toggle. Optional Roundtable Debate toggle.

### Council Review View
- Reviewer responses (color-coded, expandable)
- Synthesis panel (consensus/majority/unique/disagreements)
- Proposed changes checklist (accept/reject/modify)
- Confidence indicators with reviewer track record [v2]
- "Apply Changes and Generate v[N+1]" button

### Evolution Timeline
- Visual version timeline: v1 → v2 → v3
- Click any version to see full content
- Click any change to see attribution
- Diff view between any two versions
- Attribution chart (which reviewer contributed what)
- Reviewer performance dashboard [v2]

### Tech choice: HTMX [v2 CHANGE — Gemini suggestion]
Use HTMX for reactive UI elements (timeline, diff views, expandable panels) instead of vanilla JS or React. Gets the "app-like" feel without the build complexity. Evaluate in Phase 2 — if HTMX feels limiting, upgrade specific components to React.

---

## Data Model (SQLite)

```sql
-- Core tables (unchanged from v1)
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    primary_model TEXT,
    reviewer_models TEXT,
    created_at TIMESTAMP,
    status TEXT
);

CREATE TABLE versions (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    version_number INTEGER,
    content TEXT,
    created_at TIMESTAMP,
    created_from TEXT
);

CREATE TABLE review_rounds (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    round_number INTEGER,
    briefing_sent TEXT,
    dispatched_at TIMESTAMP,
    completed_at TIMESTAMP,
    debate_mode_enabled BOOLEAN DEFAULT FALSE
);

CREATE TABLE reviews (
    id TEXT PRIMARY KEY,
    round_id TEXT REFERENCES review_rounds(id),
    model_name TEXT,
    response TEXT,
    debate_response TEXT,              -- [v2] Optional second response in debate mode
    received_at TIMESTAMP,
    tokens_used INTEGER,
    cost_estimate REAL
);

CREATE TABLE synthesis (
    id TEXT PRIMARY KEY,
    round_id TEXT REFERENCES review_rounds(id),
    consensus TEXT,
    majority TEXT,
    unique_insights TEXT,
    disagreements TEXT,
    proposed_changes TEXT,
    overall_verdict TEXT
);

CREATE TABLE changelog (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    round_number INTEGER,
    version_from INTEGER,
    version_to INTEGER,
    category TEXT,
    description TEXT,
    source_reviewers TEXT,
    confidence TEXT,
    accepted BOOLEAN,
    rejection_reason TEXT
);

-- [v2 NEW] Reviewer performance tracking
CREATE TABLE reviewer_stats (
    id TEXT PRIMARY KEY,
    model_name TEXT,
    session_id TEXT REFERENCES sessions(id),
    round_number INTEGER,
    suggestion_id TEXT REFERENCES changelog(id),
    was_accepted BOOLEAN,
    survived_next_round BOOLEAN,
    category TEXT,
    updated_at TIMESTAMP
);
```

---

## Cost Analysis

### Per Round (4 reviewers, February 2026 pricing)

| Call | Total Cost |
|------|-----------|
| Claude Sonnet 4.5 review | $0.045 |
| GPT-5 review | $0.026 |
| Gemini 2.5 Pro review | $0.026 |
| Grok review | $0.065 |
| Synthesis pass | $0.069 |
| **Standard round** | **~$0.23** |
| **With Debate mode** | **~$0.33** |

### Per Project
| Scenario | Cost |
|----------|------|
| 3 rounds, 4 reviewers, standard | **~$0.70** |
| 3 rounds, 4 reviewers, with debate | **~$1.00** |
| 5 rounds, complex project | **~$1.15-1.65** |

---

## Pricing [v2 REFINED]

| Tier | Price | Includes |
|------|-------|----------|
| **Free** | $0 | 1 project, 2 rounds max, 2 reviewers max |
| **Pro (BYOK)** | $15/month | Unlimited projects + rounds + reviewers. User provides own API keys. |
| **Pro (Managed)** | $29/month | Same as Pro but we handle API billing. Includes $5 of API credits. |
| **Annual** | $144/year ($12/mo) | Pro BYOK, annual discount |

**No per-project pricing.** This is workflow software — the goal is habit formation.

The free tier is designed so users experience the full v1 → v2 cycle once. Once they see the Evolution Timeline with tracked attribution, they understand the value.

---

## Build Order

### Phase 1 — Core Pipeline (MVP, CLI-first)
1. `dispatcher.py` — Multi-model API calls via OpenRouter
2. `briefing_generator.py` — Package design into review briefing
3. `synthesis_engine.py` — Read reviews, produce structured analysis with bias mitigation
4. `session_manager.py` — Track sessions, versions, reviews
5. `changelog_generator.py` — Track changes with attribution
6. `reviewer_tracker.py` — Track reviewer acceptance/survival rates
7. Simple CLI interface for testing the full loop
8. **Test**: Take MonopolyTrader v1 design, run through Council, verify synthesis quality

### Phase 2 — Web Interface (Launch target)
9. FastAPI backend with REST endpoints
10. Workspace view (chat + "Convene the Council" button)
11. Council Review view (responses + synthesis + change checklist)
12. Apply changes flow (accept/reject → generate new version)
13. HTMX for reactive elements (evaluate vs vanilla JS)
14. **Test**: Full round-trip through the web UI

### Phase 3 — Evolution & Polish (Post-launch)
15. Evolution Timeline view with diff
16. Attribution charts
17. Reviewer performance dashboard
18. Changelog export (markdown/PDF)
19. CLAUDE.md export for Claude Code
20. Optional Roundtable Debate mode
21. Devil's Advocate reviewer role
22. Settings UI (API keys, model preferences, personalities)
23. Cost tracking per round and per project

### Phase 4 — Growth
24. User accounts and authentication (only when needed)
25. Stripe integration for paid tier
26. Landing page with MonopolyTrader case study
27. Analytics (what features get used, where users drop off)

---

## Go-to-Market Plan [v2 NEW]

### The Story (your launch hook)

"I built a trading system. I sent the design to three AIs for review. They found 30+ structural flaws — things like hallucinated causality, broken stop-losses, and missing backtesting. No single model caught them all. Three rounds of iterative review transformed it from a toy into a research platform. Total cost: under a dollar. So I built a tool that automates the whole process."

### Launch Sequence

1. **Pre-launch**: Build in public on Twitter/X. Share snippets of the Evolution Timeline. Show v1 vs v3 diffs. Build anticipation.
2. **Week 1**: Twitter/X thread with MonopolyTrader case study + screenshots. Tag AI builders.
3. **Week 1**: Hacker News "Show HN" post same day.
4. **Week 2**: Product Hunt launch.
5. **Week 3+**: Indie Hackers post. Dev tool directories. AI newsletters.
6. **Ongoing**: Weekly "Council Review" content — pick an open-source design, run it through the Council, share the results. Free marketing that demonstrates the product.

### The Demo That Sells

90-second video:
1. Show a messy v1 design (10 seconds)
2. Click "Convene the Council" (5 seconds)
3. Show 4 reviews coming in, color-coded (10 seconds)
4. Show the synthesis panel — consensus, disagreements (15 seconds)
5. Accept changes with one click (10 seconds)
6. Show the Evolution Timeline: v1 → v2 → v3 (20 seconds)
7. Show the attribution chart: "Grok caught the architecture flaw, Gemini found the cost issue" (15 seconds)
8. End card: "Design review for serious builders. 70 cents per project." (5 seconds)

---

## Kill Criteria [v2 NEW]

| Milestone | Timeframe | Action |
|-----------|-----------|--------|
| Phase 1 CLI works, synthesis quality is good | Month 1-2 | Continue to Phase 2 |
| Phase 2 web UI launched | Month 3-4 | Begin marketing push |
| <50 paying users after 3 months of web UI launch | Month 6-7 | Reevaluate. Is it a marketing problem or a product problem? |
| <50 paying users AND low engagement (users don't do round 2) | Month 6-7 | The workflow isn't sticky. Sunset or major pivot. |
| 50-150 paying users, growing | Month 6-12 | Keep going. Invest in Phase 3 features. |
| 150+ paying users | Month 9-12 | Consider expanding to new use cases (writing, research, legal) |

---

## What Makes Council of Alignment Different

| Feature | Existing tools | **Council of Alignment** |
|---------|---------------|--------------------------|
| Ask multiple models | ✓ | ✓ |
| Side-by-side comparison | ✓ | ✓ |
| **Iterative rounds with memory** | ✗ | **✓** |
| **Tracked changelog with attribution** | ✗ | **✓** |
| **Auto-synthesized consensus** | Partial | **✓** |
| **Version control for designs** | ✗ | **✓** |
| **Evolution timeline** | ✗ | **✓** |
| **Briefing auto-generation** | ✗ | **✓** |
| **Reviewer performance tracking** | ✗ | **✓** |
| **Export to dev tools (CLAUDE.md)** | ✗ | **✓** |
| **Optional debate mode** | ✗ | **✓** |

---

## Open Questions (resolved from v1)

| v1 Question | v2 Resolution | Source |
|-------------|---------------|--------|
| Primary AI model-agnostic or specific? | Model-agnostic workspace, user picks Primary | Design decision |
| Heavy or light reviewer personality prompts? | Light touch + optional Devil's Advocate role | Gemini |
| Should user write own prompts? | No — briefing generator handles it | All three (consensus) |
| Auto-apply consensus changes? | No — always human-in-the-loop | Design decision |
| OpenRouter vs direct APIs? | Default OpenRouter, allow direct as advanced | Design decision |
| Structured reviewer output or natural? | Natural responses, synthesis engine handles structure | Design decision |

## Remaining Open Questions

1. **Debate mode pricing**: Should Roundtable Debate be a Pro-only feature, or available on free tier?
2. **Team features**: When (if ever) to add shared projects and team accounts?
3. **API for the Council**: Should there be a programmatic API so developers can integrate Council reviews into CI/CD pipelines?
4. **Model updates**: When a new model drops (e.g., Claude Opus 4.7), how to handle adding it as a reviewer option?
