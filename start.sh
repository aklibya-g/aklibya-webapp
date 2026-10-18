#!/bin/sh
set -e

# Use /data if volume is mounted, otherwise use local
DATA_DIR="/data"
if [ ! -d "$DATA_DIR" ]; then
    DATA_DIR="/app"
fi

# Copy db.sqlite3 to data dir if not exists (first run)
if [ ! -f "$DATA_DIR/db.sqlite3" ]; then
    cp /app/db.sqlite3 "$DATA_DIR/db.sqlite3"
    echo "Database copied to $DATA_DIR"
fi

# Run migrations
DATABASE_PATH="$DATA_DIR/db.sqlite3" python manage.py migrate --noinput

# Create admin user
DATABASE_PATH="$DATA_DIR/db.sqlite3" python manage.py create_admin

# Start server
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000
