"""Create dedicated council user, transfer ownership, update systemd service."""
import pathlib
import subprocess
import os
import shutil

APP_DIR = "/opt/council-of-alignment"
DATA_DIR = "/var/lib/council"
OLD_DIR = "/root/council-of-alignment"
USER = "council"

# 1. Create system user (no login shell, no home dir)
result = subprocess.run(["id", USER], capture_output=True)
if result.returncode != 0:
    subprocess.run([
        "useradd", "--system", "--shell", "/usr/sbin/nologin",
        "--create-home", "--home-dir", f"/home/{USER}", USER
    ], check=True)
    print(f"Created system user: {USER}")
else:
    print(f"User {USER} already exists")

# 2. Copy app to /opt (separate from /root)
if not pathlib.Path(APP_DIR).exists():
    shutil.copytree(OLD_DIR, APP_DIR, symlinks=True)
    print(f"Copied app to {APP_DIR}")
else:
    # Sync updated files
    subprocess.run(["rsync", "-a", "--exclude", "data/", f"{OLD_DIR}/", f"{APP_DIR}/"], check=True)
    print(f"Synced app to {APP_DIR}")

# 3. Create data directory
os.makedirs(DATA_DIR, exist_ok=True)

# Move database if it's still in old location
old_db = pathlib.Path(f"{OLD_DIR}/data/council.db")
new_db = pathlib.Path(f"{DATA_DIR}/council.db")
app_data_dir = pathlib.Path(f"{APP_DIR}/data")
app_data_dir.mkdir(exist_ok=True)

if old_db.exists() and not new_db.exists():
    shutil.copy2(str(old_db), str(new_db))
    print(f"Copied database to {DATA_DIR}")
elif old_db.exists() and new_db.exists():
    # Use the newer one
    if old_db.stat().st_mtime > new_db.stat().st_mtime:
        shutil.copy2(str(old_db), str(new_db))
        print("Copied newer database from old location")
    else:
        print("Database already in new location and is newer")

# Create symlink so the app finds the DB at its expected path
db_link = pathlib.Path(f"{APP_DIR}/data/council.db")
if db_link.is_symlink():
    db_link.unlink()
elif db_link.exists():
    db_link.unlink()  # remove the copy
db_link.symlink_to(str(new_db))
print(f"Symlinked {db_link} -> {new_db}")

# 4. Copy .env
old_env = pathlib.Path(f"{OLD_DIR}/.env")
new_env = pathlib.Path(f"{APP_DIR}/.env")
if old_env.exists():
    shutil.copy2(str(old_env), str(new_env))
    print("Copied .env")

# 5. Set ownership
subprocess.run(["chown", "-R", f"{USER}:{USER}", APP_DIR], check=True)
subprocess.run(["chown", "-R", f"{USER}:{USER}", DATA_DIR], check=True)
os.chmod(str(new_env), 0o600)
os.chmod(str(new_db), 0o600)
print(f"Ownership set to {USER}")

# 6. Update systemd service
service = pathlib.Path("/etc/systemd/system/council-of-alignment.service")
service.write_text(f"""[Unit]
Description=Council of Alignment
After=network.target

[Service]
Type=simple
User={USER}
Group={USER}
WorkingDirectory={APP_DIR}
ExecStart={APP_DIR}/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8890
Restart=on-failure
RestartSec=5
EnvironmentFile={APP_DIR}/.env
MemoryMax=300M
CPUQuota=50%

# Security sandboxing
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
ReadWritePaths={DATA_DIR}

[Install]
WantedBy=multi-user.target
""")
print("Updated systemd service with sandboxing")

# 7. Also update backup script to use new DB path
backup_script = pathlib.Path("/root/council-backup.sh")
if backup_script.exists():
    txt = backup_script.read_text()
    txt = txt.replace(str(old_db), str(new_db))
    # Also make sure the backup user can read the DB
    backup_script.write_text(txt)
    print("Updated backup script with new DB path")

# 8. Reload and restart
subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(["systemctl", "restart", "council-of-alignment"], check=True)
print("Service restarted")

# 9. Verify
import time
time.sleep(2)
result = subprocess.run(["systemctl", "is-active", "council-of-alignment"], capture_output=True, text=True)
status = result.stdout.strip()
print(f"Service status: {status}")

if status != "active":
    # Show logs for debugging
    result = subprocess.run(["journalctl", "-u", "council-of-alignment", "-n", "20", "--no-pager"],
                          capture_output=True, text=True)
    print(f"Logs:\\n{result.stdout}")

# 10. Verify the process is running as council user
result = subprocess.run(["ps", "-eo", "user,comm,args"], capture_output=True, text=True)
for line in result.stdout.splitlines():
    if "8890" in line:
        print(f"Process: {line.strip()}")
