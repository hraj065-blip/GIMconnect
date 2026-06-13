#!/bin/bash
set -e  # Exit immediately on any error

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Collecting static files ==="
python manage.py collectstatic --noinput

echo "=== Running database migrations ==="
if [ -n "$DATABASE_URL" ]; then
    python manage.py migrate --noinput
    echo "Migrations complete."
else
    echo "WARNING: DATABASE_URL not set — skipping migrations."
fi

echo "=== Build complete ==="
