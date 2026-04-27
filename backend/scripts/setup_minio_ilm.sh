#!/usr/bin/env bash
# setup_minio_ilm.sh — Apply MinIO ILM lifecycle rules for COG retention.
#
# Tenant COGs older than 60 days are automatically expired by MinIO ILM.
# This avoids custom Celery cleanup code for tenant buckets.
#
# Usage:
#   export MC_HOST_minio=http://ACCESS_KEY:SECRET_KEY@minio-service:9000
#   bash setup_minio_ilm.sh
#
# Or run as a K8s Job with mc (MinIO Client) image.

set -euo pipefail

MC_ALIAS="${MC_ALIAS:-minio}"
EXPIRE_DAYS="${EXPIRE_DAYS:-60}"

echo "=== MinIO ILM Setup for Vegetation Prime ==="
echo "Alias: ${MC_ALIAS}, Expiration: ${EXPIRE_DAYS} days"

# List all vegetation-prime-* buckets (tenant buckets)
BUCKETS=$(mc ls "${MC_ALIAS}/" --json 2>/dev/null | grep -o '"key":"vegetation-prime-[^"]*"' | sed 's/"key":"//;s/\/"$//' || true)

if [ -z "${BUCKETS}" ]; then
    echo "No vegetation-prime-* buckets found. Nothing to configure."
    exit 0
fi

for BUCKET in ${BUCKETS}; do
    # Skip the global bucket (managed by Celery LRU task)
    if [ "${BUCKET}" = "vegetation-prime-global" ]; then
        echo "  [SKIP] ${BUCKET} (global cache — managed by Celery LRU)"
        continue
    fi

    echo "  [ILM] ${MC_ALIAS}/${BUCKET} → expire after ${EXPIRE_DAYS} days"

    # Check if rule already exists
    EXISTING=$(mc ilm rule ls "${MC_ALIAS}/${BUCKET}" --json 2>/dev/null | grep "expire-old-cogs" || true)
    if [ -n "${EXISTING}" ]; then
        echo "    Rule 'expire-old-cogs' already exists, skipping."
        continue
    fi

    mc ilm rule add "${MC_ALIAS}/${BUCKET}" \
        --expire-days "${EXPIRE_DAYS}" \
        --id "expire-old-cogs" \
        2>/dev/null || echo "    WARNING: Failed to add ILM rule for ${BUCKET}"
done

echo "=== ILM setup complete ==="
