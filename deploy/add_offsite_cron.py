"""Add off-site backup to cron (runs 30min after local backup)."""
import subprocess

# Get current crontab
result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
crontab = result.stdout

offsite_line = "30 3 * * * /usr/bin/python3 /opt/council-of-alignment/deploy/offsite_backup.py >> /var/log/council-offsite-backup.log 2>&1"

if "offsite_backup" not in crontab:
    crontab = crontab.rstrip() + "\n" + offsite_line + "\n"
    proc = subprocess.run(["crontab", "-"], input=crontab, text=True, capture_output=True)
    if proc.returncode == 0:
        print("Added off-site backup cron job (daily at 3:30 AM UTC)")
    else:
        print(f"Failed: {proc.stderr}")
else:
    print("Off-site backup cron already exists")
