"""Fix the systemd service — adjust sandboxing to allow app execution."""
import pathlib
import subprocess
import time

APP_DIR = "/opt/council-of-alignment"
DATA_DIR = "/var/lib/council"
USER = "council"

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
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
ProtectSystem=full
ReadWritePaths={DATA_DIR}
ProtectHome=yes

[Install]
WantedBy=multi-user.target
""")
print("Updated service (ProtectSystem=full instead of strict)")

subprocess.run(["systemctl", "daemon-reload"], check=True)
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
    # Verify running as council user
    result = subprocess.run(["ps", "-eo", "user,comm,args"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if "8890" in line:
            print(f"Process: {line.strip()}")

    # Quick HTTP check
    import urllib.request
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8890/", timeout=5)
        print(f"App responding: HTTP {resp.status}")
    except Exception as e:
        print(f"App check: {e}")
