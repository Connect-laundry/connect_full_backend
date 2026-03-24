#!/bin/bash
# Database Backup Script for Connect Laundry

# Load environment variables if .env exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="./backups"
FILENAME="backup_${TIMESTAMP}.sql"

mkdir -p ${BACKUP_DIR}

echo "Starting backup to ${BACKUP_DIR}/${FILENAME}..."

# If using Docker
if [ -z "$DB_HOST" ] || [ "$DB_HOST" == "localhost" ] || [ "$DB_HOST" == "db" ]; then
    docker compose exec db pg_dump -U ${DB_USER} ${DB_NAME} > ${BACKUP_DIR}/${FILENAME}
else
    # Remote DB (like Neon)
    PGPASSWORD=${DB_PASSWORD} pg_dump -h ${DB_HOST} -U ${DB_USER} -d ${DB_NAME} > ${BACKUP_DIR}/${FILENAME}
fi

if [ $? -eq 0 ]; then
    echo "Backup successful: ${FILENAME}"
    # Keep only last 7 days of backups
    find ${BACKUP_DIR} -type f -name "*.sql" -mtime +7 -delete
else
    echo "Backup failed!"
    exit 1
fi
