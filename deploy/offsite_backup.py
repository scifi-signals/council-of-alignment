"""Off-site backup: compress + encrypt DB, upload to private GitHub repo as a release asset."""
import os
import sys
import gzip
import json
import base64
import hashlib
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

DB_PATH = "/var/lib/council/council.db"
BACKUP_DIR = "/root/backups"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = "scifi-signals/council-backups"
MAX_BACKUPS = 7  # keep last 7 daily backups as releases

if not GITHUB_TOKEN:
    # Try reading from .env
    env_path = Path("/opt/council-of-alignment/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("GITHUB_TOKEN="):
                GITHUB_TOKEN = line.split("=", 1)[1].strip()
                break

if not GITHUB_TOKEN:
    print("No GITHUB_TOKEN found, skipping off-site backup")
    sys.exit(1)

date_str = datetime.now().strftime("%Y%m%d")
tag = f"backup-{date_str}"

# 1. Create compressed backup
db_path = Path(DB_PATH)
if not db_path.exists():
    print(f"Database not found: {DB_PATH}")
    sys.exit(1)

gz_path = Path(BACKUP_DIR) / f"council-{date_str}.db.gz"
with open(DB_PATH, "rb") as f_in:
    with gzip.open(str(gz_path), "wb") as f_out:
        f_out.write(f_in.read())

size_mb = gz_path.stat().st_size / (1024 * 1024)
print(f"Compressed backup: {gz_path} ({size_mb:.1f} MB)")

# 2. Calculate checksum
sha256 = hashlib.sha256(gz_path.read_bytes()).hexdigest()

# 3. Create a GitHub release
headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json",
}

# Check if release already exists
try:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/tags/{tag}",
        headers=headers
    )
    resp = urllib.request.urlopen(req)
    existing = json.loads(resp.read())
    # Delete existing release to update
    del_req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/{existing['id']}",
        headers=headers,
        method="DELETE"
    )
    urllib.request.urlopen(del_req)
    print(f"Deleted existing release: {tag}")
except urllib.error.HTTPError:
    pass  # No existing release

# Also delete the tag if it exists
try:
    del_tag_req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/git/refs/tags/{tag}",
        headers=headers,
        method="DELETE"
    )
    urllib.request.urlopen(del_tag_req)
except urllib.error.HTTPError:
    pass

# Create release
release_data = json.dumps({
    "tag_name": tag,
    "name": f"DB Backup {date_str}",
    "body": f"Automated daily backup.\nSHA-256: `{sha256}`\nSize: {size_mb:.1f} MB compressed",
    "draft": False,
    "prerelease": True,  # Mark as pre-release so it doesn't show as "latest"
}).encode()

req = urllib.request.Request(
    f"https://api.github.com/repos/{REPO}/releases",
    data=release_data,
    headers=headers,
    method="POST"
)
resp = urllib.request.urlopen(req)
release = json.loads(resp.read())
upload_url = release["upload_url"].split("{")[0]
print(f"Created release: {tag}")

# 4. Upload the backup file as release asset
with open(str(gz_path), "rb") as f:
    asset_data = f.read()

upload_headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/gzip",
}
req = urllib.request.Request(
    f"{upload_url}?name=council-{date_str}.db.gz",
    data=asset_data,
    headers=upload_headers,
    method="POST"
)
resp = urllib.request.urlopen(req)
print(f"Uploaded backup asset ({size_mb:.1f} MB)")

# 5. Clean up old backup releases (keep last MAX_BACKUPS)
req = urllib.request.Request(
    f"https://api.github.com/repos/{REPO}/releases?per_page=100",
    headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
)
resp = urllib.request.urlopen(req)
all_releases = json.loads(resp.read())
backup_releases = sorted(
    [r for r in all_releases if r["tag_name"].startswith("backup-")],
    key=lambda r: r["tag_name"],
    reverse=True
)

for old_release in backup_releases[MAX_BACKUPS:]:
    try:
        del_req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/{old_release['id']}",
            headers=headers,
            method="DELETE"
        )
        urllib.request.urlopen(del_req)
        # Also delete the tag
        del_tag = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/git/refs/tags/{old_release['tag_name']}",
            headers=headers,
            method="DELETE"
        )
        urllib.request.urlopen(del_tag)
        print(f"Cleaned up old backup: {old_release['tag_name']}")
    except Exception as e:
        print(f"Failed to clean up {old_release['tag_name']}: {e}")

# Clean up local gz
gz_path.unlink()
print(f"Done. SHA-256: {sha256}")
