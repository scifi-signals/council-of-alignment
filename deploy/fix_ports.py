"""Lock down exposed service ports on server."""
import pathlib
import subprocess

# 1. Bind STM API to localhost (same fix as council)
svc = pathlib.Path("/etc/systemd/system/stm-draft-api.service")
txt = svc.read_text()
if "--host 0.0.0.0" in txt:
    txt = txt.replace("--host 0.0.0.0", "--host 127.0.0.1")
    svc.write_text(txt)
    print("STM service: bound to 127.0.0.1")
else:
    print("STM service: already bound to 127.0.0.1")

# 2. Reload and restart STM
subprocess.run(["systemctl", "daemon-reload"], check=True)
subprocess.run(["systemctl", "restart", "stm-draft-api"], check=True)
print("STM service: restarted")

# 3. Remove firewall rules for 8000 (Docker State Science Intel)
# It has no nginx proxy so nobody should be hitting it directly
for rule in ["8000", "8000/tcp"]:
    r = subprocess.run(["ufw", "delete", "allow", rule], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"Firewall: removed {rule}")
    else:
        print(f"Firewall: {rule} - {r.stderr.strip()}")

# 4. Show final state
subprocess.run(["ufw", "status"])
print()
subprocess.run(["ss", "-tlnp"])
