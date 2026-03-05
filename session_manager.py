"""Session manager — CRUD for sessions, messages, versions, rounds, changelog."""

import json
import uuid
from datetime import datetime
from database import get_db
from config import MODELS, get_council_models


class SessionManager:
    async def create_session(self, title: str, lead_model: str, council_models: list[str] = None, user_id: str = None) -> dict:
        """Create a new design session."""
        if lead_model not in MODELS:
            raise ValueError(f"Unknown model: {lead_model}. Choose from: {list(MODELS.keys())}")

        if council_models is None:
            council_models = get_council_models(lead_model)

        session_id = uuid.uuid4().hex[:16]
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO sessions (id, title, lead_model, council_models, user_id) VALUES (?, ?, ?, ?, ?)",
                (session_id, title, lead_model, json.dumps(council_models), user_id),
            )
            await db.commit()
            return {
                "id": session_id,
                "title": title,
                "lead_model": lead_model,
                "council_models": council_models,
                "user_id": user_id,
            }
        finally:
            await db.close()

    async def get_session(self, session_id: str) -> dict | None:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "title": row["title"],
                "lead_model": row["lead_model"],
                "council_models": json.loads(row["council_models"]),
                "created_at": row["created_at"],
                "status": row["status"],
                "user_id": row["user_id"],
            }
        finally:
            await db.close()

    async def delete_session(self, session_id: str):
        """Delete a session and all related data."""
        db = await get_db()
        try:
            # Delete in dependency order
            await db.execute(
                """DELETE FROM reviewer_stats WHERE change_id IN
                   (SELECT id FROM changelog WHERE session_id = ?)""", (session_id,))
            await db.execute("DELETE FROM changelog WHERE session_id = ?", (session_id,))
            await db.execute(
                """DELETE FROM synthesis_results WHERE round_id IN
                   (SELECT id FROM review_rounds WHERE session_id = ?)""", (session_id,))
            await db.execute(
                """DELETE FROM reviews WHERE round_id IN
                   (SELECT id FROM review_rounds WHERE session_id = ?)""", (session_id,))
            await db.execute("DELETE FROM review_rounds WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM versions WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM attachments WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM github_repos WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await db.commit()
        finally:
            await db.close()

    async def list_sessions(self, user_id: str = None) -> list[dict]:
        db = await get_db()
        try:
            if user_id:
                cursor = await db.execute(
                    "SELECT * FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                )
            else:
                cursor = await db.execute("SELECT * FROM sessions ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "lead_model": r["lead_model"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "user_id": r["user_id"],
                }
                for r in rows
            ]
        finally:
            await db.close()

    async def save_version(self, session_id: str, content: str, created_from: str = "design_extraction") -> dict:
        """Save a design version snapshot."""
        db = await get_db()
        try:
            # Get next version number
            cursor = await db.execute(
                "SELECT MAX(version_number) as max_v FROM versions WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            version_number = (row["max_v"] or 0) + 1

            version_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO versions (id, session_id, version_number, content, created_from) VALUES (?, ?, ?, ?, ?)",
                (version_id, session_id, version_number, content, created_from),
            )
            await db.commit()
            return {"id": version_id, "version_number": version_number}
        finally:
            await db.close()

    async def get_latest_version(self, session_id: str) -> dict | None:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM versions WHERE session_id = ? ORDER BY version_number DESC LIMIT 1",
                (session_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "version_number": row["version_number"],
                "content": row["content"],
                "created_at": row["created_at"],
                "created_from": row["created_from"],
            }
        finally:
            await db.close()

    async def save_review_round(self, session_id: str, round_number: int, briefing: str) -> str:
        """Create a review round record. Returns round_id."""
        round_id = str(uuid.uuid4())
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO review_rounds (id, session_id, round_number, briefing) VALUES (?, ?, ?, ?)",
                (round_id, session_id, round_number, briefing),
            )
            await db.commit()
            return round_id
        finally:
            await db.close()

    async def complete_review_round(self, round_id: str):
        db = await get_db()
        try:
            await db.execute(
                "UPDATE review_rounds SET completed_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), round_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def save_review(self, round_id: str, model_name: str, response: str, tokens_in: int = 0, tokens_out: int = 0, cost: float = 0) -> str:
        review_id = str(uuid.uuid4())
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO reviews (id, round_id, model_name, response, tokens_in, tokens_out, cost_estimate) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (review_id, round_id, model_name, response, tokens_in, tokens_out, cost),
            )
            await db.commit()
            return review_id
        finally:
            await db.close()

    async def save_synthesis(self, round_id: str, synthesis: dict) -> str:
        synth_id = str(uuid.uuid4())
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO synthesis_results (id, round_id, full_synthesis) VALUES (?, ?, ?)",
                (synth_id, round_id, json.dumps(synthesis)),
            )
            await db.commit()
            return synth_id
        finally:
            await db.close()

    async def get_round_number(self, session_id: str) -> int:
        """Get the next round number for a session."""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT MAX(round_number) as max_r FROM review_rounds WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            return (row["max_r"] or 0) + 1
        finally:
            await db.close()

    async def get_reviews(self, session_id: str, round_number: int = None) -> list[dict]:
        """Get reviews for a session, optionally filtered by round."""
        db = await get_db()
        try:
            if round_number:
                cursor = await db.execute(
                    """SELECT r.*, rr.round_number FROM reviews r
                       JOIN review_rounds rr ON r.round_id = rr.id
                       WHERE rr.session_id = ? AND rr.round_number = ?
                       ORDER BY r.received_at""",
                    (session_id, round_number),
                )
            else:
                cursor = await db.execute(
                    """SELECT r.*, rr.round_number FROM reviews r
                       JOIN review_rounds rr ON r.round_id = rr.id
                       WHERE rr.session_id = ?
                       ORDER BY rr.round_number, r.received_at""",
                    (session_id,),
                )
            rows = await cursor.fetchall()
            return [
                {
                    "model_name": r["model_name"],
                    "response": r["response"],
                    "round_number": r["round_number"],
                    "tokens_in": r["tokens_in"],
                    "tokens_out": r["tokens_out"],
                    "cost_estimate": r["cost_estimate"],
                }
                for r in rows
            ]
        finally:
            await db.close()

    async def get_latest_synthesis(self, session_id: str) -> dict | None:
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT sr.* FROM synthesis_results sr
                   JOIN review_rounds rr ON sr.round_id = rr.id
                   WHERE rr.session_id = ?
                   ORDER BY sr.created_at DESC LIMIT 1""",
                (session_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return json.loads(row["full_synthesis"])
        finally:
            await db.close()

    async def get_all_syntheses(self, session_id: str) -> list[dict]:
        """Get all syntheses for a session, ordered by round number."""
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT sr.full_synthesis, rr.round_number FROM synthesis_results sr
                   JOIN review_rounds rr ON sr.round_id = rr.id
                   WHERE rr.session_id = ?
                   ORDER BY rr.round_number""",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [
                {"round_number": r["round_number"], "synthesis": json.loads(r["full_synthesis"])}
                for r in rows
            ]
        finally:
            await db.close()

    async def save_changelog_entry(self, session_id: str, round_number: int, change: dict, accepted: bool, rejection_reason: str = None) -> str:
        # Prefix with round number to avoid ID collisions across rounds
        # (synthesis generates generic IDs like change_001 each round)
        raw_id = change.get("id", str(uuid.uuid4()))
        entry_id = f"r{round_number}_{raw_id}"
        db = await get_db()
        try:
            await db.execute(
                """INSERT OR REPLACE INTO changelog (id, session_id, round_number, category, description,
                   source_reviewers, confidence, accepted, rejection_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id, session_id, round_number,
                    change.get("category", "other"),
                    change["description"],
                    json.dumps(change.get("source_reviewers", [])),
                    change.get("confidence", "single"),
                    accepted,
                    rejection_reason,
                ),
            )
            await db.commit()
            return entry_id
        finally:
            await db.close()

    async def get_changelog(self, session_id: str) -> list[dict]:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM changelog WHERE session_id = ? ORDER BY round_number, created_at",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": r["id"],
                    "round_number": r["round_number"],
                    "category": r["category"],
                    "description": r["description"],
                    "source_reviewers": json.loads(r["source_reviewers"]),
                    "confidence": r["confidence"],
                    "accepted": bool(r["accepted"]),
                    "rejection_reason": r["rejection_reason"],
                }
                for r in rows
            ]
        finally:
            await db.close()

    async def get_timeline(self, session_id: str) -> list[dict]:
        """Get the version timeline for a session."""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM versions WHERE session_id = ? ORDER BY version_number",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "version_number": r["version_number"],
                    "created_at": r["created_at"],
                    "created_from": r["created_from"],
                    "content_preview": r["content"][:200] + "..." if len(r["content"]) > 200 else r["content"],
                }
                for r in rows
            ]
        finally:
            await db.close()

    async def get_timeline_data(self, session_id: str) -> list[dict]:
        """Get evolution timeline: rounds with changelog entries and per-model attribution."""
        db = await get_db()
        try:
            # Get all completed review rounds
            cursor = await db.execute(
                """SELECT id, round_number, dispatched_at, completed_at
                   FROM review_rounds WHERE session_id = ? AND completed_at IS NOT NULL
                   ORDER BY round_number""",
                (session_id,),
            )
            rounds = [dict(r) for r in await cursor.fetchall()]

            # Get all changelog entries for this session
            cl_cursor = await db.execute(
                "SELECT * FROM changelog WHERE session_id = ? ORDER BY round_number, created_at",
                (session_id,),
            )
            all_changes = [dict(r) for r in await cl_cursor.fetchall()]

            # Group changes by round_number
            changes_by_round: dict[int, list[dict]] = {}
            for c in all_changes:
                rn = c["round_number"]
                if rn not in changes_by_round:
                    changes_by_round[rn] = []
                changes_by_round[rn].append({
                    "id": c["id"],
                    "category": c["category"],
                    "description": c["description"],
                    "source_reviewers": json.loads(c["source_reviewers"]),
                    "confidence": c["confidence"],
                    "accepted": bool(c["accepted"]),
                    "rejection_reason": c["rejection_reason"],
                })

            result = []
            for rnd in rounds:
                rn = rnd["round_number"]
                changes = changes_by_round.get(rn, [])

                # Compute per-model attribution
                attribution: dict[str, dict] = {}
                for ch in changes:
                    for reviewer in ch["source_reviewers"]:
                        if reviewer not in attribution:
                            attribution[reviewer] = {"proposed": 0, "accepted": 0, "rejected": 0}
                        attribution[reviewer]["proposed"] += 1
                        if ch["accepted"]:
                            attribution[reviewer]["accepted"] += 1
                        else:
                            attribution[reviewer]["rejected"] += 1

                accepted_count = sum(1 for c in changes if c["accepted"])
                rejected_count = sum(1 for c in changes if not c["accepted"])

                result.append({
                    "round_number": rn,
                    "dispatched_at": rnd["dispatched_at"],
                    "completed_at": rnd["completed_at"],
                    "changes_proposed": len(changes),
                    "changes_accepted": accepted_count,
                    "changes_rejected": rejected_count,
                    "changes_pending": 0,
                    "changes": changes,
                    "attribution": attribution,
                })
            return result
        finally:
            await db.close()

    # ─── GitHub repo management ──────────────────────────────

    async def connect_github_repo(self, session_id: str, repo_url: str, owner: str,
                                   repo_name: str, branch: str, tree_json: str) -> dict:
        """Connect a GitHub repo to a session."""
        repo_id = str(uuid.uuid4())[:8]
        db = await get_db()
        try:
            # Remove any existing repo for this session first
            await db.execute("DELETE FROM github_repos WHERE session_id = ?", (session_id,))
            await db.execute(
                """INSERT INTO github_repos (id, session_id, repo_url, owner, repo_name,
                   default_branch, tree_json, tree_fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (repo_id, session_id, repo_url, owner, repo_name, branch, tree_json,
                 datetime.utcnow().isoformat()),
            )
            await db.commit()
            return {
                "id": repo_id, "owner": owner, "repo_name": repo_name,
                "default_branch": branch, "file_count": len(json.loads(tree_json)),
            }
        finally:
            await db.close()

    async def get_github_repo(self, session_id: str) -> dict | None:
        """Get the connected GitHub repo for a session."""
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM github_repos WHERE session_id = ?", (session_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "repo_url": row["repo_url"],
                "owner": row["owner"],
                "repo_name": row["repo_name"],
                "default_branch": row["default_branch"],
                "tree_json": row["tree_json"],
                "tree_fetched_at": row["tree_fetched_at"],
            }
        finally:
            await db.close()

    async def update_github_tree(self, session_id: str, tree_json: str):
        """Update the cached tree for a session's repo."""
        db = await get_db()
        try:
            await db.execute(
                "UPDATE github_repos SET tree_json = ?, tree_fetched_at = ?, chat_files_json = NULL WHERE session_id = ?",
                (tree_json, datetime.utcnow().isoformat(), session_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def disconnect_github_repo(self, session_id: str):
        """Remove the GitHub repo connection for a session."""
        db = await get_db()
        try:
            await db.execute("DELETE FROM github_repos WHERE session_id = ?", (session_id,))
            await db.commit()
        finally:
            await db.close()
