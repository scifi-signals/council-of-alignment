"""Fetch and print the latest synthesis for a session."""
import asyncio
import json
import sys
import aiosqlite

DB_PATH = "/root/council-of-alignment/data/council.db"
SESSION = sys.argv[1] if len(sys.argv) > 1 else "b5ec87be"


async def main():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT sr.full_synthesis FROM synthesis_results sr "
        "JOIN review_rounds rr ON sr.round_id = rr.id "
        "WHERE rr.session_id = ? ORDER BY rr.round_number DESC LIMIT 1",
        (SESSION,),
    )
    row = await cur.fetchone()
    await db.close()
    if row:
        s = json.loads(row[0])
        print(json.dumps(s, indent=2))
    else:
        print("No synthesis found")


asyncio.run(main())
