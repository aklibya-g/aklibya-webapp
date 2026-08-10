#!/bin/sh
set -e

# Copy db.sqlite3 to /data if not exists (first run)
if [ ! -f /data/db.sqlite3 ]; then
    cp /app/db.sqlite3 /data/db.sqlite3
    echo "Database copied to /data"
fi

# Run migrations
python manage.py migrate --noinput

# Create admin user
python manage.py create_admin

# Start server
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000
