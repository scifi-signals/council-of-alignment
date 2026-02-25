"""Reviewer tracker — tracks Council member suggestion/acceptance stats."""

import json
import uuid
from database import get_db
from config import MODELS


class ReviewerTracker:
    async def record_suggestion(self, model_name: str, change_id: str, category: str):
        """Record that a model contributed to a proposed change."""
        db = await get_db()
        try:
            stat_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO reviewer_stats (id, model_name, change_id, category) VALUES (?, ?, ?, ?)",
                (stat_id, model_name, change_id, category),
            )
            await db.commit()
        finally:
            await db.close()

    async def record_decision(self, change_id: str, accepted: bool):
        """Update whether a suggestion was accepted."""
        db = await get_db()
        try:
            await db.execute(
                "UPDATE reviewer_stats SET was_accepted = ? WHERE change_id = ?",
                (accepted, change_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_stats(self, model_name: str = None) -> dict:
        """Get performance stats, optionally filtered by model."""
        db = await get_db()
        try:
            if model_name:
                cursor = await db.execute(
                    "SELECT * FROM reviewer_stats WHERE model_name = ?",
                    (model_name,),
                )
            else:
                cursor = await db.execute("SELECT * FROM reviewer_stats")
            rows = await cursor.fetchall()

            # Aggregate by model
            stats = {}
            for r in rows:
                name = r["model_name"]
                if name not in stats:
                    stats[name] = {
                        "total_suggestions": 0,
                        "accepted": 0,
                        "rejected": 0,
                        "pending": 0,
                        "categories": {},
                    }
                s = stats[name]
                s["total_suggestions"] += 1
                cat = r["category"] or "other"
                s["categories"][cat] = s["categories"].get(cat, 0) + 1

                if r["was_accepted"] is None:
                    s["pending"] += 1
                elif r["was_accepted"]:
                    s["accepted"] += 1
                else:
                    s["rejected"] += 1

            # Calculate rates
            for name, s in stats.items():
                decided = s["accepted"] + s["rejected"]
                s["acceptance_rate"] = round(s["accepted"] / decided * 100, 1) if decided > 0 else 0
                s["display_name"] = MODELS.get(name, {}).get("name", name)

            return stats
        finally:
            await db.close()

    async def get_stats_for_synthesis(self, council_models: list[str]) -> dict:
        """Get stats formatted for the synthesis prompt."""
        all_stats = await self.get_stats()
        return {m: all_stats[m] for m in council_models if m in all_stats}
