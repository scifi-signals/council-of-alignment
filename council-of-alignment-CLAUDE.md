# Council of Alignment — Build Spec (CLAUDE.md)

## What You're Building

A web application where users design things with an AI "Lead" and then send their design to a council of other AI models for iterative review.

Think of it like this: the user picks a Lead AI (Claude, ChatGPT, Gemini, or Grok) and works with it to build a design — chatting back and forth just like a normal AI conversation. When the design is ready, the user clicks "Convene the Council" and the Lead automatically packages the design and sends it to the other 3 AI models for independent critical review. The reviews come back, the Lead synthesizes the feedback (what they agree on, where they disagree, unique insights), proposes specific changes, and the user approves or rejects each one. The Lead updates the design. The user can run as many rounds as they want. When done, they download the finished design files.

That's the whole product.

## The User Experience (Step by Step)

```
1. User opens the Council of Alignment website
2. User starts a new session:
   - Names their project ("MonopolyTrader Design")
   - Picks their Lead AI (Claude, ChatGPT, Gemini, or Grok)
   - Optionally picks which other models sit on the Council
3. User chats with their Lead AI to build a design
   - Normal back-and-forth conversation
   - "I want to build an AI trading bot that learns from mistakes"
   - Lead helps flesh out the idea, asks questions, creates the design
   - This can take as long as needed — minutes or hours
4. When the design feels ready, user clicks "Convene the Council"
   - Lead auto-generates a clean review briefing from the conversation
   - Briefing is sent to the other 3 Council members simultaneously
   - User waits ~30 seconds
5. Reviews appear on screen (color-coded by reviewer)
   - User can read each review in full
   - Below the reviews: the Lead's synthesis
     - Consensus: "All 3 agree you need X"
     - Majority: "2 of 3 think Y, but Grok disagrees because..."
     - Unique: "Gemini caught Z that nobody else mentioned"
     - Disagreements: "ChatGPT says cheap model, Gemini says expensive"
   - Proposed changes as a checklist: Accept / Reject each one
6. User accepts/rejects changes
7. Lead updates the design and the conversation continues
   - User can keep chatting with the Lead to refine further
   - Changelog tracks every change with attribution
8. Want another round? Click "Convene the Council" again
   - This time the briefing includes what changed since last round
   - Reviewers see the evolution and dig deeper
9. When done, user downloads the finished design files
   - Just like downloading files from any AI chat
   - Design doc, changelog, supporting specs — whatever was produced
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  WEB FRONTEND                    │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │           CHAT INTERFACE                  │   │
│  │  User <-> Lead AI conversation            │   │
│  │  [Convene the Council] button             │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │           COUNCIL VIEW                    │   │
│  │  Reviewer responses (color-coded)         │   │
│  │  Synthesis panel                          │   │
│  │  Change checklist (accept/reject)         │   │
│  │  Evolution Timeline                       │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │           FILES / DOWNLOADS               │   │
│  │  Download design docs when ready          │   │
│  └──────────────────────────────────────────┘   │
├─────────────────────────────────────────────────┤
│                 PYTHON BACKEND                   │
│                                                  │
│  Session Manager    — Projects + versions        │
│  Chat Engine        — Manages Lead AI convo      │
│  Briefing Generator — Packages design for review │
│  Dispatcher         — Sends to Council members   │
│  Synthesis Engine   — Lead reads all reviews     │
│  Changelog          — Tracks changes+attribution │
│  Reviewer Tracker   — Performance stats          │
│  File Manager       — Generates downloadable docs│
├─────────────────────────────────────────────────┤
│  APIs: OpenRouter (preferred) or direct          │
│  Anthropic / OpenAI / Google / xAI               │
├─────────────────────────────────────────────────┤
│  SQLite: sessions, messages, versions, reviews   │
└─────────────────────────────────────────────────┘
```

## Tech Stack

- **Backend**: Python 3.11+ / FastAPI
- **Frontend**: HTML5 + HTMX + vanilla JS (no React, no build step)
- **Templates**: Jinja2 (server-rendered)
- **Database**: SQLite (single file, zero config)
- **APIs**: OpenRouter as unified gateway (one API key, all models)
- **Markdown rendering**: marked.js in browser
- **Diff views**: diff2html (for version comparisons)

## Core Modules

### 1. `config.py` — Configuration

```python
# Load from .env
OPENROUTER_API_KEY = "..."

# Available models (user picks their Lead from these)
MODELS = {
    "claude": {
        "id": "anthropic/claude-sonnet-4-20250514",
        "name": "Claude",
        "color": "#7C3AED"  # Purple
    },
    "chatgpt": {
        "id": "openai/gpt-5",
        "name": "ChatGPT",
        "color": "#10A37F"  # Green
    },
    "gemini": {
        "id": "google/gemini-2.5-pro",
        "name": "Gemini",
        "color": "#4285F4"  # Blue
    },
    "grok": {
        "id": "x-ai/grok-3",
        "name": "Grok",
        "color": "#F97316"  # Orange
    }
}
```

### 2. `dispatcher.py` — Multi-Model API Manager

Handles all communication with AI models via OpenRouter.

```python
class ModelDispatcher:
    async def chat(self, model_id: str, messages: list[dict]) -> str:
        """Send a conversation to a model and get a response.
        Used for both Lead AI chat and Council reviews."""

    async def dispatch_to_council(
        self,
        council_models: list[str],
        system_prompt: str,
        briefing: str
    ) -> dict[str, str]:
        """Send briefing to all Council members in parallel.
        Returns: {model_name: review_response}"""
```

Use OpenRouter's OpenAI-compatible endpoint:
- Base URL: `https://openrouter.ai/api/v1/chat/completions`
- Auth: Bearer token with OPENROUTER_API_KEY
- Track tokens and estimate cost per call

### 3. `chat_engine.py` — Lead AI Conversation Manager

Manages the ongoing conversation between the user and their Lead AI.

```python
class ChatEngine:
    async def send_message(self, session_id: str, user_message: str) -> str:
        """Send user message to Lead AI, get response.
        Maintains full conversation history in the session."""

    async def get_design_state(self, session_id: str) -> str:
        """Ask the Lead AI to extract/summarize the current design
        from the conversation so far. Used before convening the Council."""

    async def inject_synthesis(self, session_id: str, synthesis: dict, accepted_changes: list) -> str:
        """After the user accepts/rejects changes, tell the Lead AI
        what was accepted and have it update the design accordingly.
        The conversation continues naturally from here."""
```

**Key insight**: The Lead AI conversation is a normal chat with full history. When the user clicks "Convene the Council," the Lead is asked to extract the current design state from the conversation. After reviews come back, the synthesis results and accepted changes are injected back into the conversation so the Lead can update the design and the user can keep chatting.

### 4. `briefing_generator.py` — Package Design for Review

```python
async def generate_briefing(
    design: str,
    round_number: int,
    previous_changelog: list[dict] = None,
    custom_questions: list[str] = None,
    lead_model: str = None
) -> str:
    """Use the Lead AI to generate a review briefing from the current design.

    Round 1: Design + auto-generated review questions
    Round 2+: Design + changelog + targeted questions + "here's what changed"

    Ends with: "Be direct and critical. This is round N."
    """
```

### 5. `synthesis_engine.py` — Lead Reads All Reviews

```python
async def synthesize_reviews(
    design: str,
    reviews: dict[str, str],
    previous_changelog: list[dict] = None,
    reviewer_stats: dict = None,
    lead_model: str = None
) -> dict:
    """The Lead AI reads all Council reviews and produces structured synthesis.

    Returns:
    {
        "consensus": [...],
        "majority": [...],
        "unique_insights": [...],
        "disagreements": [...],
        "proposed_changes": [
            {
                "id": "change_001",
                "description": "Switch from fixed stops to ATR-based",
                "category": "risk",
                "source_reviewers": ["chatgpt", "gemini"],
                "confidence": "consensus"
            }
        ],
        "overall_verdict": {
            "ready_to_build": bool,
            "another_round_recommended": bool,
            "summary": "string"
        }
    }
    """
```

**Bias mitigation**: The synthesis prompt instructs the Lead to steelman all suggestions equally, even ones that contradict its own earlier advice.

### 6. `session_manager.py` — Project Lifecycle

```python
class SessionManager:
    def create_session(self, title: str, lead_model: str, council_models: list[str]) -> Session
    def save_message(self, session_id: str, role: str, content: str) -> None
    def save_version(self, session_id: str, design_content: str, created_from: str) -> Version
    def save_review_round(self, session_id: str, briefing: str, reviews: dict, synthesis: dict) -> ReviewRound
    def apply_changes(self, session_id: str, accepted: list, rejected: list) -> None
    def get_changelog(self, session_id: str) -> list[ChangeEntry]
    def get_timeline(self, session_id: str) -> list[Version]
    def list_sessions(self) -> list[Session]
```

### 7. `reviewer_tracker.py` — Council Member Performance

```python
class ReviewerTracker:
    def record_suggestion(self, model: str, change_id: str, category: str) -> None
    def record_decision(self, change_id: str, accepted: bool) -> None
    def get_stats(self, model: str = None) -> dict
```

Over time reveals: "Gemini catches cost issues, Grok finds structural problems, ChatGPT spots edge cases."

### 8. `file_manager.py` — Generate Downloadable Files

```python
class FileManager:
    def generate_design_doc(self, session: Session) -> tuple[str, str]:
        """Returns (filename, content) for the main design document."""

    def generate_changelog(self, session: Session) -> tuple[str, str]:
        """Returns (filename, content) for the changelog with attribution."""

    def generate_all_files(self, session: Session) -> list[tuple[str, str]]:
        """Returns all downloadable files."""
```

No zip files, no builder-specific packaging for MVP. Just download individual files like you would from any AI chat.

### 9. `database.py` — SQLite Schema

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    lead_model TEXT NOT NULL,
    council_models TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'designing'
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE versions (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    version_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_from TEXT
);

CREATE TABLE review_rounds (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    round_number INTEGER NOT NULL,
    briefing TEXT NOT NULL,
    dispatched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE reviews (
    id TEXT PRIMARY KEY,
    round_id TEXT REFERENCES review_rounds(id),
    model_name TEXT NOT NULL,
    response TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_estimate REAL,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE synthesis_results (
    id TEXT PRIMARY KEY,
    round_id TEXT REFERENCES review_rounds(id),
    full_synthesis TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE changelog (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    round_number INTEGER NOT NULL,
    version_from INTEGER,
    version_to INTEGER,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    source_reviewers TEXT NOT NULL,
    confidence TEXT NOT NULL,
    accepted BOOLEAN,
    rejection_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE reviewer_stats (
    id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    change_id TEXT REFERENCES changelog(id),
    was_accepted BOOLEAN,
    survived_next_round BOOLEAN,
    category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Build Order

### Phase 1 — Core Pipeline (CLI)

1. `config.py` — Load .env, define models
2. `database.py` — SQLite schema + connection helpers
3. `dispatcher.py` — Send prompts to models via OpenRouter
4. `chat_engine.py` — Manage Lead AI conversation + extract design state
5. `briefing_generator.py` — Package design into review briefing
6. `synthesis_engine.py` — Lead reads all reviews, produces structured output
7. `session_manager.py` — Track everything in SQLite
8. `reviewer_tracker.py` — Track Council member performance
9. `file_manager.py` — Generate downloadable design files
10. `cli.py` — Interactive CLI:

```bash
python cli.py new "MonopolyTrader" --lead claude
python cli.py chat <session_id>
python cli.py convene <session_id>
python cli.py reviews <session_id>
python cli.py synthesis <session_id>
python cli.py decide <session_id>
python cli.py chat <session_id>          # Continue with Lead
python cli.py convene <session_id>       # Another round
python cli.py changelog <session_id>
python cli.py timeline <session_id>
python cli.py export <session_id> --output ./my-project/
python cli.py stats
```

**Phase 1 Test**: Run a real design through the full loop. Chat with Lead, convene Council, accept changes, run round 2. Verify synthesis quality, changelog accuracy, and file export.

### Phase 2 — Web Interface

FastAPI + HTMX + Jinja2. Server-rendered, no build step.

**Pages:**

**/ (Home)** — Project list + "New Council Session" button

**/new** — Name project, pick Lead AI, pick Council members

**/session/{id}** — Main view with two modes:
- **Design Mode**: Chat with Lead AI + "Convene the Council" button
- **Council Mode**: Reviews + synthesis + accept/reject changes

**Sidebar**: Timeline, changelog, files, stats

**/stats** — Reviewer performance dashboard

### Phase 3 — Polish

- Interactive Evolution Timeline with diffs
- Attribution charts
- Import existing designs (paste/upload)
- Custom review questions
- Better file generation

### Phase 4 — Business

- User accounts + auth
- Stripe ($15/month)
- Landing page
- Analytics

---

## File Structure

```
council-of-alignment/
├── CLAUDE.md
├── the-council-design-v2.md
├── council-of-alignment-business-plan.md
├── .env
├── requirements.txt
├── config.py
├── database.py
├── dispatcher.py
├── chat_engine.py
├── briefing_generator.py
├── synthesis_engine.py
├── session_manager.py
├── reviewer_tracker.py
├── file_manager.py
├── cli.py
├── app.py
├── data/
│   └── council.db
├── templates/
│   ├── base.html
│   ├── home.html
│   ├── new_session.html
│   ├── session.html
│   ├── timeline.html
│   └── stats.html
└── static/
    ├── style.css
    └── app.js
```

## Dependencies

```
httpx
fastapi
uvicorn
jinja2
python-dotenv
aiosqlite
python-multipart
```

## Key Principles

1. **It's a chat with superpowers.** Core experience = talking to your Lead AI. Council review = the superpower.
2. **Simple file downloads.** Just download docs. No zip ceremony.
3. **Attribution is sacred.** Every change traced to its source.
4. **The Evolution Timeline sells the product.** Make it visual.
5. **OpenRouter for everything.** One API key, all models.
6. **CLI first, web second.** Get the pipeline right before HTML.
7. **No React, no build step.** HTMX + server rendering. Ship fast.

## Cost Per Session (~$1.50-2.00)

Lead AI chat (20 messages): ~$0.50-1.00
3 Council rounds × $0.23: ~$0.70
**Total typical session: ~$1.50-2.00**
