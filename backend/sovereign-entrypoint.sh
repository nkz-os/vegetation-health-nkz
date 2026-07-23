#!/bin/bash
set -e

echo "Vegetation Prime Sovereign Worker — Starting..."

# Run migrations with advisory locks (idempotent, shared DB with API)
echo "Running database migrations..."
python /app/scripts/run_migrations.py

if [ $? -ne 0 ]; then
    echo "ERROR: Migration failed"
    exit 1
fi

echo "Migrations completed successfully"

# Start Celery worker for download + processing tasks only
# Concurrency=2 to limit memory (GDAL/rasterio is heavy per-worker)
echo "Starting Celery worker (concurrency=2, prefetch=1)..."
exec celery -A app.celery_app worker \
    --loglevel=info \
    --concurrency=2 \
    --prefetch-multiplier=1 \
    --max-tasks-per-child=10 \
    -Q vegetation_download,vegetation_process
