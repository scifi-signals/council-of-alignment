import sqlite3
c = sqlite3.connect("/root/council-of-alignment/data/council.db")
# Check key_access_log exists
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("key_access_log exists:", "key_access_log" in tables)
if "key_access_log" in tables:
    count = c.execute("SELECT count(*) FROM key_access_log").fetchone()[0]
    print("key_access_log rows:", count)

# Verify app is responding
import urllib.request
try:
    resp = urllib.request.urlopen("http://localhost:8890/")
    print("App responding:", resp.status)
except Exception as e:
    print("App error:", e)
