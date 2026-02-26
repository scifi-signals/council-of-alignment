"""Delete round 2 data for session 9db93c88 so we can re-run the dedup test."""
import asyncio
import aiosqlite

DB_PATH = "/root/council-of-alignment/data/council.db"
SESSION = "9db93c88"


async def main():
    db = await aiosqlite.connect(DB_PATH)

    # Find round 2
    cursor = await db.execute(
        "SELECT id FROM review_rounds WHERE session_id = ? AND round_number = 2", (SESSION,)
    )
    row = await cursor.fetchone()
    if not row:
        print("No round 2 found. Nothing to delete.")
        await db.close()
        return

    round_id = row[0]
    print(f"Deleting round 2 data (round_id={round_id})...")

    await db.execute("DELETE FROM synthesis_results WHERE round_id = ?", (round_id,))
    await db.execute("DELETE FROM reviews WHERE round_id = ?", (round_id,))
    await db.execute("DELETE FROM review_rounds WHERE id = ?", (round_id,))
    await db.commit()
    await db.close()
    print("Done. Round 2 deleted. You can re-convene.")


asyncio.run(main())
