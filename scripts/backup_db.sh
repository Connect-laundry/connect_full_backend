#!/bin/bash

# Configuration
DB_NAME=${DB_NAME:-"connectlaundry_db"}
DB_USER=${DB_USER:-"postgres"}
BACKUP_DIR=${BACKUP_DIR:-"/var/backups/postgres"}
RETENTION_DAYS=7
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/${DB_NAME}_$TIMESTAMP.sql.gz"
LOG_FILE="$BACKUP_DIR/backup.log"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting backup: $DB_NAME" >> "$LOG_FILE"

# Run pg_dump and compress
if pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_PATH"; then
    echo "[$(date)] Backup successful: $BACKUP_PATH" >> "$LOG_FILE"
    
    # Retention Policy: Delete backups older than RETENTION_DAYS
    find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete
    echo "[$(date)] Retention policy applied (deleted backups older than $RETENTION_DAYS days)" >> "$LOG_FILE"
else
    echo "[$(date)] ERROR: Backup failed!" >> "$LOG_FILE"
    exit 1
fi
