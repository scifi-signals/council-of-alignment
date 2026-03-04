"""Fix venv shebangs and restart service."""
import pathlib
import subprocess
import time
import os
import glob

APP_DIR = "/opt/council-of-alignment"
OLD_PREFIX = "/root/council-of-alignment/venv"
NEW_PREFIX = f"{APP_DIR}/venv"

# Fix all shebangs in venv/bin that reference the old path
bin_dir = pathlib.Path(f"{APP_DIR}/venv/bin")
fixed = 0
for script in bin_dir.iterdir():
    if script.is_symlink() or not script.is_file():
        continue
    try:
        content = script.read_text()
    except (UnicodeDecodeError, PermissionError):
        continue
    if OLD_PREFIX in content:
        content = content.replace(OLD_PREFIX, NEW_PREFIX)
        script.write_text(content)
        fixed += 1

print(f"Fixed {fixed} shebang(s) in venv/bin")

# Verify uvicorn shebang
uvicorn = pathlib.Path(f"{APP_DIR}/venv/bin/uvicorn")
first_line = uvicorn.read_text().splitlines()[0]
print(f"uvicorn shebang: {first_line}")

# Re-chown since we edited as root
subprocess.run(["chown", "-R", "council:council", APP_DIR], check=True)

# Restart
subprocess.run(["systemctl", "restart", "council-of-alignment"], check=True)
time.sleep(3)

result = subprocess.run(["systemctl", "is-active", "council-of-alignment"], capture_output=True, text=True)
status = result.stdout.strip()
print(f"Service status: {status}")

if status != "active":
    result = subprocess.run(["journalctl", "-u", "council-of-alignment", "-n", "10", "--no-pager"],
                          capture_output=True, text=True)
    print(f"Logs:\\n{result.stdout}")
else:
    result = subprocess.run(["ps", "-eo", "user,comm,args"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "8890" in line:
            print(f"Process: {line.strip()}")
