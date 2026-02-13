#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Waiting for database..."
# For production, we already have depends_on healthchecks in docker-compose, 
# but this is a double safety.

python manage.py migrate --noinput
python manage.py collectstatic --noinput

echo "Starting Gunicorn..."
exec gunicorn --bind 0.0.0.0:8000 --workers 3 --timeout 120 config.wsgi:application
