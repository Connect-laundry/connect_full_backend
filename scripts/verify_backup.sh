#!/bin/bash

# Configuration
BACKUP_DIR=${BACKUP_DIR:-"/var/backups/postgres"}
LOG_FILE="$BACKUP_DIR/backup_verification.log"

# Find latest backup
LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/*.sql.gz | head -1)

if [ -z "$LATEST_BACKUP" ]; then
    echo "[$(date)] ERROR: No backup files found to verify!" >> "$LOG_FILE"
    exit 1
fi

echo "[$(date)] Verifying latest backup: $LATEST_BACKUP" >> "$LOG_FILE"

# 1. Check if file is not empty
if [ ! -s "$LATEST_BACKUP" ]; then
    echo "[$(date)] ERROR: Backup file is empty!" >> "$LOG_FILE"
    exit 1
fi

# 2. Verify compression integrity
if gzip -t "$LATEST_BACKUP"; then
    echo "[$(date)] Integrity check passed" >> "$LOG_FILE"
else
    echo "[$(date)] ERROR: Backup file is corrupted!" >> "$LOG_FILE"
    exit 1
fi

echo "[$(date)] Verification complete" >> "$LOG_FILE"
