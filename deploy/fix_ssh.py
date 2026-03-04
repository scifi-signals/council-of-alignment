"""Fix SSH hardening: disable password auth, X11, expired key, enable fail2ban."""
import pathlib
import subprocess

# 1. Remove cloud-init override that enables password auth
cloud_init = pathlib.Path("/etc/ssh/sshd_config.d/50-cloud-init.conf")
if cloud_init.exists():
    cloud_init.unlink()
    print("Removed 50-cloud-init.conf (was enabling password auth)")
else:
    print("50-cloud-init.conf already removed")

# 2. Add hardening config
hardening = pathlib.Path("/etc/ssh/sshd_config.d/99-hardening.conf")
hardening.write_text("X11Forwarding no\n")
print("Added X11Forwarding no")

# 3. Remove expired DigitalOcean SSH key from authorized_keys
ak = pathlib.Path("/root/.ssh/authorized_keys")
lines = ak.read_text().splitlines()
new_lines = [l for l in lines if "cdobbins@nas.edu" not in l and "ecdsa-sha2-nistp256" not in l]
if len(new_lines) < len(lines):
    ak.write_text("\n".join(new_lines) + "\n")
    print(f"Removed {len(lines) - len(new_lines)} expired SSH key(s)")
else:
    print("No expired keys found")

# 4. Restart SSH
subprocess.run(["systemctl", "restart", "ssh"], check=True)
print("SSH restarted")

# 5. Verify
result = subprocess.run(["sshd", "-T"], capture_output=True, text=True)
for line in result.stdout.splitlines():
    if "passwordauthentication" in line or "x11forwarding" in line:
        print(f"  {line}")

# 6. Enable fail2ban
subprocess.run(["systemctl", "enable", "fail2ban"], check=True)
subprocess.run(["systemctl", "start", "fail2ban"], check=True)
print("fail2ban enabled and started")

# 7. Verify fail2ban
result = subprocess.run(["fail2ban-client", "status"], capture_output=True, text=True)
print(result.stdout.strip())
