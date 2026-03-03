"""Test Lead analysis quality via API — round 7 verification."""
import requests
import json
import sys
import re

BASE = "http://localhost:8890"

# 1. Create session
print("=== Creating session ===")
resp = requests.post(BASE + "/new", data={"title": "Lead Test Round 7 - MonopolyTrader", "lead": "claude"}, allow_redirects=False)
if resp.status_code in (302, 303):
    loc = resp.headers.get("location", "")
    session_id = loc.split("/session/")[-1] if "/session/" in loc else None
    print("Session created: %s" % session_id)
else:
    print("Failed to create session: %d %s" % (resp.status_code, resp.text[:200]))
    sys.exit(1)

# 2. Connect GitHub repo
print("\n=== Connecting GitHub repo ===")
resp = requests.post(BASE + "/api/github/%s" % session_id, json={
    "repo_url": "https://github.com/scifi-signals/MonopolyTrader"
})
print("GitHub connect: %d" % resp.status_code)
data = resp.json()
print(json.dumps(data, indent=2)[:1000])

# 3. Send the directive message (what the auto-directive would send)
directive = "First, list every file you have loaded. Then trace every data flow from input to output. Report only broken chains where data is collected but never used, or functions defined but never called. Ignore features that are disabled via config — those are intentional toggles, not broken chains."

print("\n=== Sending directive ===")
resp = requests.post(
    BASE + "/api/chat/%s" % session_id,
    data={"message": directive},  # form data, not JSON
    timeout=120,
)
print("Chat response: %d, length: %d" % (resp.status_code, len(resp.text)))

# Parse HTML to extract the actual text content
html = resp.text
# Strip HTML tags for readability
text = re.sub(r'<[^>]+>', '\n', html)
# Clean up whitespace
text = re.sub(r'\n{3,}', '\n\n', text).strip()
print("\n--- LEAD RESPONSE ---")
print(text)

print("\n=== Session ID: %s ===" % session_id)
print("View at: %s/session/%s" % (BASE, session_id))
