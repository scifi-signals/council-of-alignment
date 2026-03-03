"""SQLite database — schema, init, async context manager."""

import os
import aiosqlite
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    lead_model TEXT NOT NULL,
    council_models TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'designing'
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    version_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_from TEXT
);

CREATE TABLE IF NOT EXISTS review_rounds (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    round_number INTEGER NOT NULL,
    briefing TEXT NOT NULL,
    dispatched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    round_id TEXT REFERENCES review_rounds(id),
    model_name TEXT NOT NULL,
    response TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_estimate REAL,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS synthesis_results (
    id TEXT PRIMARY KEY,
    round_id TEXT REFERENCES review_rounds(id),
    full_synthesis TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS changelog (
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

CREATE TABLE IF NOT EXISTS reviewer_stats (
    id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    change_id TEXT REFERENCES changelog(id),
    was_accepted BOOLEAN,
    survived_next_round BOOLEAN,
    category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    size_bytes INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    github_id INTEGER UNIQUE NOT NULL,
    github_login TEXT NOT NULL,
    display_name TEXT NOT NULL,
    avatar_url TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS github_repos (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    repo_url TEXT NOT NULL,
    owner TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    default_branch TEXT DEFAULT 'main',
    tree_json TEXT,
    tree_fetched_at TIMESTAMP,
    chat_files_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db():
    """Create all tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Idempotent migrations for existing DBs
        try:
            await db.execute("ALTER TABLE github_repos ADD COLUMN chat_files_json TEXT")
        except Exception:
            pass  # Column already exists
        try:
            await db.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT REFERENCES users(id)")
        except Exception:
            pass  # Column already exists
        try:
            await db.execute("ALTER TABLE users ADD COLUMN openrouter_key_encrypted TEXT")
        except Exception:
            pass  # Column already exists
        try:
            await db.execute("ALTER TABLE users ADD COLUMN free_convenes_used INTEGER DEFAULT 0")
        except Exception:
            pass  # Column already exists
        await db.commit()


async def get_db() -> aiosqlite.Connection:
    """Get a database connection. Caller must close it."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db
