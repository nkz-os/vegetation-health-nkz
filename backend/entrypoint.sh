#!/bin/bash
set -e

echo "Vegetation Prime Backend - Starting..."

# Run migrations with advisory locks (idempotent)
echo "Running database migrations..."
python /app/scripts/run_migrations.py

if [ $? -ne 0 ]; then
    echo "ERROR: Migration failed"
    exit 1
fi

echo "Migrations completed successfully"

# Start the application
echo "Starting FastAPI application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000



