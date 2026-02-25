"""End-to-end test: create → chat → convene → accept/reject → timeline → export."""
import requests
import json
import sys
import re
import time

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://159.203.126.156:8890"

def strip_html(html):
    text = re.sub(r'<[^>]+>', '\n', html)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text

# ── Step 1: Create session ──
print("=" * 70)
print("STEP 1: Create session")
print("=" * 70)
resp = requests.post(BASE + "/new", data={
    "title": "E2E Test - MonopolyTrader",
    "lead": "claude",
}, allow_redirects=False)
session_id = resp.headers.get("location", "").split("/session/")[-1]
print("Session: %s" % session_id)

# ── Step 2: Connect GitHub ──
print("\n" + "=" * 70)
print("STEP 2: Connect GitHub repo")
print("=" * 70)
resp = requests.post(BASE + "/api/github/%s" % session_id, json={
    "repo_url": "https://github.com/scifi-signals/MonopolyTrader"
})
data = resp.json()
print("Connected: %s/%s (%d files in tree)" % (data["owner"], data["repo_name"], data["file_count"]))

# ── Step 3: Send directive to Lead ──
print("\n" + "=" * 70)
print("STEP 3: Lead analysis (directive -> verified response)")
print("=" * 70)
directive = (
    "First, list every file you have loaded. Then trace every data flow "
    "from input to output. Report only broken chains where data is collected "
    "but never used, or functions defined but never called. Ignore features "
    "that are disabled via config — those are intentional toggles, not broken chains."
)
start = time.time()
resp = requests.post(BASE + "/api/chat/%s" % session_id, data={"message": directive}, timeout=180)
lead_time = time.time() - start
print("Lead response: %d (%d chars, %.0fs)" % (resp.status_code, len(resp.text), lead_time))
lead_text = strip_html(resp.text)
# Count findings
finding_count = lead_text.lower().count("broken")
print("Approximate findings mentioned: %d" % finding_count)

# ── Step 4: Convene the Council ──
print("\n" + "=" * 70)
print("STEP 4: Convene the Council (3 models review in parallel)")
print("=" * 70)
start = time.time()
resp = requests.post(BASE + "/api/convene/%s" % session_id, timeout=360)
convene_time = time.time() - start
print("Convene response: %d (%d chars, %.0fs)" % (resp.status_code, len(resp.text), convene_time))

if resp.status_code != 200:
    print("CONVENE FAILED!")
    print(resp.text[:500])
    sys.exit(1)

convene_text = strip_html(resp.text)

# ── Step 5: Get synthesis to find proposed_changes ──
# The synthesis is embedded in the convene HTML. We need the change IDs.
# Let's fetch via the session data instead.
print("\n" + "=" * 70)
print("STEP 5: Accept/reject proposed changes")
print("=" * 70)

# Extract change IDs from the HTML (they're in data attributes or form fields)
# Pattern: change_001, change_002, etc.
change_ids = re.findall(r'(change_\d+)', resp.text)
change_ids = list(dict.fromkeys(change_ids))  # dedupe preserving order
print("Found %d proposed changes: %s" % (len(change_ids), change_ids))

if not change_ids:
    print("No proposed changes found in convene response!")
    print("First 2000 chars of response:")
    print(convene_text[:2000])
    sys.exit(1)

# Accept all but the last one, reject the last one (to test both paths)
decisions = []
for i, cid in enumerate(change_ids):
    if i < len(change_ids) - 1:
        decisions.append({"id": cid, "accepted": True})
    else:
        decisions.append({"id": cid, "accepted": False, "reason": "Testing reject path"})

print("Decisions: %d accepted, 1 rejected" % (len(decisions) - 1))

resp = requests.post(
    BASE + "/api/decide/%s" % session_id,
    json={"decisions": decisions},
    timeout=120,
)
print("Decide response: %d (%d chars)" % (resp.status_code, len(resp.text)))
decide_text = strip_html(resp.text)
print(decide_text[:1000])

# ── Step 6: Timeline ──
print("\n" + "=" * 70)
print("STEP 6: Evolution Timeline")
print("=" * 70)
resp_tl = requests.get(BASE + "/api/timeline/%s" % session_id, timeout=10)
print("Timeline response: %d" % resp_tl.status_code)
if resp_tl.status_code == 200:
    tl_data = resp_tl.json()
    rounds = tl_data.get("rounds", [])
    print("Rounds in timeline: %d" % len(rounds))
    for r in rounds:
        print("  Round %d: %d proposed, %d accepted, %d rejected" % (
            r["round_number"], r["changes_proposed"], r["changes_accepted"], r["changes_rejected"]))
        if r.get("attribution"):
            for model, attr in r["attribution"].items():
                print("    %s: %d proposed, %d accepted, %d rejected" % (
                    model, attr["proposed"], attr["accepted"], attr["rejected"]))
    timeline_ok = len(rounds) >= 1 and rounds[0]["changes_proposed"] > 0
else:
    print("Timeline FAILED: %s" % resp_tl.text[:300])
    timeline_ok = False

# ── Step 7: Export ──
print("\n" + "=" * 70)
print("STEP 7: Export design document")
print("=" * 70)
resp = requests.get(BASE + "/api/export/%s" % session_id, timeout=30)
print("Export response: %d (%d chars)" % (resp.status_code, len(resp.text)))
if resp.status_code == 200:
    # Show first section
    print("Export preview (first 500 chars):")
    print(resp.text[:500])
else:
    print("Export failed: %s" % resp.text[:300])

# ── Summary ──
print("\n" + "=" * 70)
print("END-TO-END SUMMARY")
print("=" * 70)
print("Session:     %s" % session_id)
print("Lead time:   %.0fs" % lead_time)
print("Convene time: %.0fs" % convene_time)
print("Changes:     %d proposed, %d accepted, 1 rejected" % (len(change_ids), len(change_ids) - 1))
print("Timeline:    %s" % ("OK" if timeline_ok else "FAILED"))
print("Export:      %s" % ("OK" if resp.status_code == 200 else "FAILED"))

all_pass = resp.status_code == 200 and timeline_ok and len(change_ids) > 0
print("\nRESULT:      %s" % ("ALL PASS" if all_pass else "SOME FAILURES"))
print("View at: %s/session/%s" % (BASE, session_id))
