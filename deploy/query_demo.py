import sqlite3
c = sqlite3.connect("/var/lib/council/council.db")
rows = c.execute("""
    SELECT s.id, s.title, s.user_id
    FROM sessions s
    JOIN review_rounds rr ON rr.session_id = s.id
    JOIN synthesis_results sr ON sr.round_id = rr.id
    GROUP BY s.id
    ORDER BY s.created_at DESC
    LIMIT 10
""").fetchall()
for r in rows:
    print(f"{r[0]} | {r[1]} | owner={r[2]}")
