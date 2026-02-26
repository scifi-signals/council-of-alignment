"""Round 2 dedup test: convene again on an existing session, verify no re-proposals."""
import requests
import json
import re
import time
import sys
import aiosqlite
import asyncio

BASE = "http://localhost:8890"
SESSION = "9db93c88"
DB_PATH = "/root/council-of-alignment/data/council.db"


async def get_changelog():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT description, accepted, rejection_reason, source_reviewers FROM changelog WHERE session_id = ? ORDER BY created_at",
        (SESSION,),
    )
    rows = await cursor.fetchall()
    await db.close()
    return [dict(r) for r in rows]


async def get_synthesis(round_number):
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        "SELECT sr.full_synthesis FROM synthesis_results sr JOIN review_rounds rr ON sr.round_id = rr.id WHERE rr.session_id = ? AND rr.round_number = ?",
        (SESSION, round_number),
    )
    row = await cursor.fetchone()
    await db.close()
    if row:
        return json.loads(row["full_synthesis"])
    return None


def main():
    print("=" * 70)
    print("ROUND 2 DEDUP TEST")
    print("=" * 70)

    # Step 1: Get Round 1 changelog
    changelog = asyncio.run(get_changelog())
    round1_descriptions = []
    print("\nRound 1 changelog (%d entries):" % len(changelog))
    for entry in changelog:
        status = "ACCEPTED" if entry["accepted"] else "REJECTED"
        desc = entry["description"]
        round1_descriptions.append(desc)
        reason = ""
        if not entry["accepted"] and entry["rejection_reason"]:
            reason = " (reason: %s)" % entry["rejection_reason"]
        print("  [%s] %s%s" % (status, desc[:100], reason))

    if not changelog:
        print("ERROR: No changelog found for session %s" % SESSION)
        sys.exit(1)

    # Step 2: Convene Round 2
    print("\n" + "=" * 70)
    print("Convening Round 2...")
    print("=" * 70)
    start = time.time()
    resp = requests.post(BASE + "/api/convene/" + SESSION, timeout=420)
    elapsed = time.time() - start
    print("Convene response: %d (%d chars, %.0fs)" % (resp.status_code, len(resp.text), elapsed))

    if resp.status_code != 200:
        print("FAILED:", resp.text[:500])
        sys.exit(1)

    # Step 3: Get Round 2 synthesis from DB
    synthesis = asyncio.run(get_synthesis(2))
    if not synthesis:
        print("ERROR: No Round 2 synthesis found in DB")
        sys.exit(1)

    proposed = synthesis.get("proposed_changes", [])
    round2_descriptions = []
    print("\nRound 2 proposed %d changes:" % len(proposed))
    for ch in proposed:
        desc = ch.get("description", "")
        round2_descriptions.append(desc)
        print("  [%s] %s" % (ch.get("id", "?"), desc[:120]))

    # Step 4: Check for overlaps
    print("\n" + "=" * 70)
    print("DEDUP ANALYSIS")
    print("=" * 70)

    overlaps = []
    for r2_desc in round2_descriptions:
        r2_words = set(r2_desc.lower().split())
        for r1_desc in round1_descriptions:
            r1_words = set(r1_desc.lower().split())
            if len(r2_words) > 3 and len(r1_words) > 3:
                common = r2_words & r1_words
                overlap_ratio = len(common) / min(len(r2_words), len(r1_words))
                if overlap_ratio > 0.5:
                    overlaps.append((r1_desc[:80], r2_desc[:80], overlap_ratio))

    if overlaps:
        print("\nWARNING: Possible duplicates found!")
        for r1, r2, ratio in overlaps:
            print("  R1: %s" % r1)
            print("  R2: %s" % r2)
            print("  Overlap: %.0f%%" % (ratio * 100))
            print()
    else:
        print("\nNo duplicates detected between rounds.")

    # Step 5: Verdict
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    print("Round 1 decided: %d changes" % len(round1_descriptions))
    print("Round 2 proposed: %d new changes" % len(round2_descriptions))
    if not overlaps:
        print("PASS — Dedup is working. Round 2 proposed only new changes.")
    else:
        print("REVIEW NEEDED — %d potential overlaps detected." % len(overlaps))

    print("\nView at: http://159.203.126.156:8890/session/%s" % SESSION)


if __name__ == "__main__":
    main()
