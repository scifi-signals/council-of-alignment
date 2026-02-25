"""Test full Council convene via API."""
import requests
import json
import sys
import re
import time

BASE = "http://localhost:8890"

# Option 1: Use an existing session (pass as arg)
# Option 2: Create fresh session with Lead analysis first
session_id = sys.argv[1] if len(sys.argv) > 1 else None

if not session_id:
    # Create fresh session, connect GitHub, run Lead analysis
    print("=== Creating session ===")
    resp = requests.post(BASE + "/new", data={"title": "Full Council Test - MonopolyTrader", "lead": "claude"}, allow_redirects=False)
    if resp.status_code in (302, 303):
        loc = resp.headers.get("location", "")
        session_id = loc.split("/session/")[-1] if "/session/" in loc else None
        print("Session created: %s" % session_id)
    else:
        print("Failed to create session: %d %s" % (resp.status_code, resp.text[:200]))
        sys.exit(1)

    print("\n=== Connecting GitHub repo ===")
    resp = requests.post(BASE + "/api/github/%s" % session_id, json={
        "repo_url": "https://github.com/scifi-signals/MonopolyTrader"
    })
    print("GitHub connect: %d" % resp.status_code)
    data = resp.json()
    print("Files in tree: %s" % data.get("file_count", "?"))

    print("\n=== Sending Lead directive ===")
    directive = "First, list every file you have loaded. Then trace every data flow from input to output. Report only broken chains where data is collected but never used, or functions defined but never called. Ignore features that are disabled via config — those are intentional toggles, not broken chains."
    resp = requests.post(
        BASE + "/api/chat/%s" % session_id,
        data={"message": directive},
        timeout=180,
    )
    print("Lead response: %d, length: %d chars" % (resp.status_code, len(resp.text)))

    if resp.status_code != 200:
        print("Lead failed! Aborting.")
        sys.exit(1)

print("\n=== Convening the Council on session %s ===" % session_id)
print("This will take 1-3 minutes (3 models reviewing in parallel + synthesis)...")
start = time.time()

resp = requests.post(
    BASE + "/api/convene/%s" % session_id,
    timeout=300,
)
elapsed = time.time() - start
print("Convene response: %d, length: %d chars, took: %.0fs" % (resp.status_code, len(resp.text), elapsed))

if resp.status_code != 200:
    print("Convene failed!")
    print(resp.text[:500])
    sys.exit(1)

# Parse HTML to extract text content
html = resp.text

# Extract reviewer sections
print("\n" + "=" * 80)
print("COUNCIL REVIEW RESULTS")
print("=" * 80)

# Strip HTML tags for readability
text = re.sub(r'<[^>]+>', '\n', html)
text = re.sub(r'\n{3,}', '\n\n', text).strip()
# Remove excessive whitespace
text = re.sub(r'[ \t]{2,}', ' ', text)

print(text[:8000])

if len(text) > 8000:
    print("\n... [truncated, total %d chars]" % len(text))

print("\n=== Session ID: %s ===" % session_id)
print("View at: http://159.203.126.156:8890/session/%s" % session_id)
