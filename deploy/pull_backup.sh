#!/bin/bash
# Pull latest Council database backup to local machine
# Run manually or schedule with Task Scheduler / cron
# Usage: bash deploy/pull_backup.sh

BACKUP_DIR="$HOME/council-backups"
SSH_KEY="$HOME/.ssh/digitalocean"
SERVER="root@159.203.126.156"
REMOTE_DB="/var/lib/council/council.db"
DATE=$(date +%Y%m%d)

mkdir -p "$BACKUP_DIR"

echo "Pulling Council database backup..."
scp -i "$SSH_KEY" "$SERVER:$REMOTE_DB" "$BACKUP_DIR/council-$DATE.db"

if [ $? -eq 0 ]; then
    echo "Saved to $BACKUP_DIR/council-$DATE.db"
    # Keep only last 30 backups
    ls -t "$BACKUP_DIR"/council-*.db 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null
    echo "Local backups: $(ls "$BACKUP_DIR"/council-*.db 2>/dev/null | wc -l)"
else
    echo "Backup failed!"
    exit 1
fi
