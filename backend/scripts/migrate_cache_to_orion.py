#!/usr/bin/env python3
"""
Migrate vegetation_indices_cache data to VegetationIndex entities in Orion-LD.

Run BEFORE switching FIWARE_NATIVE_MODE to 'true'.
This ensures all historical data flows through Orion-LD → telemetry-worker → TimescaleDB.

Usage:
    python scripts/migrate_cache_to_orion.py [--dry-run] [--limit N]

Requires:
    - DATABASE_URL env var pointing to the vegetation module DB
    - FIWARE_CONTEXT_BROKER_URL env var (default: http://orion-ld-service:1026)
    - CONTEXT_URL env var (default: http://api-gateway-service:5000/ngsi-ld-context.json)
"""

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import date

import psycopg2
from psycopg2.extras import RealDictCursor

# Add parent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.fiware_integration import upsert_vegetation_index_entity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Migrate vegetation cache to Orion-LD")
    parser.add_argument("--dry-run", action="store_true", help="Log what would be done without writing")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to process (0=all)")
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # Get all cache entries, ordered by entity + index_type + calculated_at desc
    # We only need the LATEST entry per (entity_id, index_type) to create/update the entity
    query = """
        SELECT DISTINCT ON (tenant_id, entity_id, index_type)
            tenant_id, entity_id, index_type, formula,
            mean_value, min_value, max_value, std_dev, pixel_count,
            result_raster_path, calculated_at,
            s.sensing_date
        FROM vegetation_indices_cache vic
        LEFT JOIN vegetation_scenes s ON s.id = vic.scene_id AND s.tenant_id = vic.tenant_id
        WHERE vic.entity_id IS NOT NULL
          AND vic.mean_value IS NOT NULL
        ORDER BY tenant_id, entity_id, index_type, calculated_at DESC
    """
    if args.limit > 0:
        query += f" LIMIT {args.limit}"

    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    logger.info("Found %d unique (entity, index_type) combinations to migrate", len(rows))

    # Group by (tenant_id, entity_id) to batch updates per entity
    by_entity = defaultdict(list)
    for row in rows:
        key = (row["tenant_id"], row["entity_id"])
        by_entity[key].append(row)

    success = 0
    failed = 0

    for (tenant_id, entity_id), entries in by_entity.items():
        for entry in entries:
            index_type = entry["index_type"]
            sensing_date_val = entry.get("sensing_date")
            if not sensing_date_val:
                # Fallback: parse from calculated_at
                calc_at = entry.get("calculated_at")
                if calc_at and hasattr(calc_at, "date"):
                    sensing_date_val = calc_at.date()
                else:
                    sensing_date_val = date.today()

            if isinstance(sensing_date_val, str):
                sensing_date_val = date.fromisoformat(sensing_date_val)

            stats = {
                "mean": float(entry["mean_value"]),
                "min": float(entry["min_value"]) if entry["min_value"] else 0,
                "max": float(entry["max_value"]) if entry["max_value"] else 0,
                "std": float(entry["std_dev"]) if entry["std_dev"] else 0,
                "pixel_count": int(entry["pixel_count"]) if entry["pixel_count"] else 0,
            }

            raster_url = entry.get("result_raster_path") or ""
            if raster_url and not raster_url.startswith("s3://"):
                bucket = os.getenv("VEGETATION_COG_BUCKET", "vegetation-prime")
                raster_url = f"s3://{bucket}/{raster_url}"

            # Custom attr name for CUSTOM indices
            custom_attr = None
            if index_type == "CUSTOM" and entry.get("formula"):
                import hashlib
                formula_hash = hashlib.md5(entry["formula"].encode()).hexdigest()[:8]
                custom_attr = f"custom_{formula_hash}"

            if args.dry_run:
                logger.info(
                    "[DRY-RUN] Would upsert VegetationIndex: tenant=%s, parcel=%s, "
                    "index=%s, mean=%.4f, date=%s",
                    tenant_id, entity_id, index_type,
                    stats["mean"], sensing_date_val,
                )
                success += 1
                continue

            result = upsert_vegetation_index_entity(
                tenant_id=tenant_id,
                parcel_id=entity_id,
                index_type=index_type,
                statistics=stats,
                raster_url=raster_url,
                sensing_date=sensing_date_val,
                custom_attr_name=custom_attr,
            )

            if result:
                success += 1
                logger.info(
                    "Migrated: %s %s %s → %s",
                    tenant_id, entity_id, index_type, result,
                )
            else:
                failed += 1
                logger.error(
                    "Failed: %s %s %s",
                    tenant_id, entity_id, index_type,
                )

    logger.info("Migration complete: %d succeeded, %d failed", success, failed)


if __name__ == "__main__":
    main()
