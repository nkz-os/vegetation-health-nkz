"""
Timeseries writer for vegetation indices.

Writes aggregated vegetation index values to the shared TimescaleDB hypertable
used by the NKZ telemetry/DataHub pipeline. Uses exact STAC acquisition datetime;
failures propagate so the worker can retry (dual persistence is critical).
"""

import logging
import os
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


def _get_postgres_url() -> str:
    """
    Resolve the PostgreSQL/TimescaleDB connection URL.

    Prefer a dedicated TIMESERIES_DATABASE_URL when present, otherwise fall
    back to the module DATABASE_URL (same cluster).
    """
    url = os.getenv("TIMESERIES_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("TIMESERIES_DATABASE_URL or DATABASE_URL must be set")
    return url


def write_index_timeseries_point(
    tenant_id: str,
    entity_id: Optional[str],
    index_type: str,
    observed_at: datetime,
    value: Optional[float],
    device_id: Optional[str] = None,
    unit: str = "index",
) -> None:
    """
    Persist a single vegetation index observation into the TimescaleDB
    hypertable `telemetry` using the exact acquisition datetime from STAC.

    The satellite behaves as a virtual device; each (entity_id, time,
    index_type) becomes a timeseries point that DataHub can consume.
    Failures are not caught: the caller (Celery task) must fail so retries
    can run; idempotency protects MinIO from duplicate uploads on retry.
    """
    if value is None or entity_id is None:
        return

    postgres_url = _get_postgres_url()
    conn = psycopg2.connect(postgres_url)
    cur = conn.cursor()

    metric_name = index_type.upper()
    device = device_id or "vegetation_prime"

    rows = [
        (
            observed_at,
            tenant_id,
            entity_id,
            device,
            metric_name,
            float(value),
            None,
            unit,
        )
    ]

    execute_values(
        cur,
        """
        INSERT INTO telemetry (
            time,
            tenant_id,
            entity_id,
            device_id,
            metric_name,
            value_numeric,
            value_text,
            unit
        )
        VALUES %s
        ON CONFLICT DO NOTHING
        """,
        rows,
    )

    conn.commit()
    cur.close()
    conn.close()
    logger.info(
        "Wrote vegetation timeseries point: tenant=%s entity=%s index=%s at=%s value=%s",
        tenant_id,
        entity_id,
        index_type,
        observed_at.isoformat(),
        value,
    )

