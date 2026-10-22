"""
DataHub adapter: GET /api/timeseries/entities/{entity_id}/data (Phase 5).

Contract: ADAPTER_SPEC.md + Action Plan §11.
- Path: GET /api/timeseries/entities/{entity_id}/data (no /api/vegetation prefix).
- Query: attribute (required), start_time, end_time (ISO 8601), resolution (optional), format (arrow).
- Response: Arrow IPC stream, columns timestamp (float64, epoch seconds), value (float64).
- Empty: 204 No Content.
"""

import io
import logging
import os
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.ipc
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from app.middleware.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["timeseries-adapter"])

ARROW_MIME = "application/vnd.apache.arrow.stream"

# ---------------------------------------------------------------------------
# BFF: JSON historical endpoint for EOProduct entities (reads from
# telemetry_events hypertable populated via NGSI-LD subscription).
# Only SELECT — zero INSERT. Used by the frontend timeseries chart.
# ---------------------------------------------------------------------------


def _get_telemetry_events_url() -> str:
    """TimescaleDB connection for telemetry_events (may differ from legacy telemetry table)."""
    return os.getenv("TELEMETRY_EVENTS_DATABASE_URL") or os.getenv("TIMESERIES_DATABASE_URL") or os.getenv("DATABASE_URL", "")


@router.get("/api/vegetation/bff/history")
async def get_vegetation_history(
    entity_id: str = Query(..., description="AgriParcel entity URN"),
    index_type: str = Query("NDVI", description="Index type: NDVI, EVI, SAVI, GNDVI, NDRE"),
    start: str = Query(..., description="ISO 8601 start date"),
    end: str = Query(..., description="ISO 8601 end date"),
    current_user: dict = Depends(require_auth),
):
    """Return historical vegetation index data from telemetry_events.

    Queries the telemetry_events hypertable for EOProduct entities linked to
    the given parcel (canonical vegetation-index entity, see Task 1/5).
    Returns JSON points for the frontend chart.
    """
    tenant_id = current_user["tenant_id"]

    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date: {e}")

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    points = await _history_from_telemetry_events(
        tenant_id, entity_id, index_type, start_dt, end_dt,
    )

    if not points:
        return {"points": []}

    return {"points": points}


async def _history_from_telemetry_events(
    tenant_id: str,
    parcel_id: str,
    index_type: str,
    start_dt: datetime,
    end_dt: datetime,
) -> list:
    """Read from telemetry_events hypertable: EOProduct entities for this parcel.

    EOProduct id pattern (Task 1 writer): urn:ngsi-ld:EOProduct:{tenant}:{parcel_short10}:{date}
    where parcel_short10 = the parcel URN's last segment truncated to 10 chars.
    The index attribute is the lowercased index name, a Property whose `value`
    is the zonal mean, with sub-Properties min/max/std.
    """
    import json as json_mod

    import psycopg2
    from psycopg2.extras import RealDictCursor

    parcel_short = (parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id)[:10]
    eo_prefix = f"urn:ngsi-ld:EOProduct:{tenant_id}:{parcel_short}:"
    index_key = index_type.lower()

    conn = None
    try:
        conn = psycopg2.connect(_get_telemetry_events_url(), cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT observed_at, payload
            FROM telemetry_events
            WHERE tenant_id = %s
              AND entity_type = 'EOProduct'
              AND entity_id LIKE %s
              AND observed_at >= %s
              AND observed_at < %s
            ORDER BY observed_at ASC
            """,
            (tenant_id, eo_prefix + "%", start_dt, end_dt),
        )
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        logger.exception("BFF telemetry_events query failed: %s", e)
        raise HTTPException(status_code=500, detail="Error querying historical data")
    finally:
        if conn:
            conn.close()

    points = []
    for row in rows:
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            payload = json_mod.loads(payload)

        idx = payload.get(index_key) or {}
        mean_val = idx.get("value") if isinstance(idx, dict) else None
        if mean_val is None:
            continue

        def _sub(key):
            v = idx.get(key)
            return float(v["value"]) if isinstance(v, dict) and v.get("value") is not None else None

        points.append({
            "date": row["observed_at"].isoformat() if hasattr(row["observed_at"], "isoformat") else str(row["observed_at"]),
            "mean": float(mean_val),
            "min": _sub("min"),
            "max": _sub("max"),
            "std": _sub("std"),
        })

    return points


def _get_postgres_url() -> str:
    url = os.getenv("TIMESERIES_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("TIMESERIES_DATABASE_URL or DATABASE_URL must be set")
    return url


@router.get("/api/timeseries/entities/{entity_id}/data")
async def get_timeseries_data(
    entity_id: str,
    attribute: str = Query(..., description="Attribute name (e.g. ndvi, MSAVI)"),
    start_time: str = Query(..., description="ISO 8601 range start (inclusive)"),
    end_time: str = Query(..., description="ISO 8601 range end (exclusive)"),
    resolution: int = Query(None, description="Target number of points (optional downsampling)"),
    format: str = Query("arrow", description="arrow (required) or json"),
    current_user: dict = Depends(require_auth),
):
    """
    Return vegetation timeseries from platform telemetry table as Arrow IPC.

    DataHub BFF calls this with Fiware-Service and Authorization. Tenant from JWT.
    Timestamp column is Unix epoch **seconds** (float64) per ADAPTER_SPEC.

    {entity_id} is the parcel URN (spec §4.1: per-parcel timeseries). EOProduct
    rows are matched by id prefix urn:ngsi-ld:EOProduct:{tenant}:{parcel_short10}:
    — same scheme as _history_from_telemetry_events — so all acquisitions for
    the parcel are returned, not just one exact EOProduct id.
    """
    if format != "arrow":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only format=arrow is supported",
        )

    tenant_id = current_user["tenant_id"]

    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid start_time or end_time: {e}",
        )
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    if start_dt >= end_dt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_time must be before end_time",
        )

    import json as json_mod

    import psycopg2
    from psycopg2.extras import RealDictCursor

    index_key = attribute.lower()
    parcel_short = (entity_id.split(":")[-1] if ":" in entity_id else entity_id)[:10]
    eo_prefix = f"urn:ngsi-ld:EOProduct:{tenant_id}:{parcel_short}:"

    conn = None
    try:
        conn = psycopg2.connect(_get_postgres_url(), cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT observed_at, payload
            FROM telemetry_events
            WHERE tenant_id = %s
              AND entity_type = 'EOProduct'
              AND entity_id LIKE %s
              AND observed_at >= %s
              AND observed_at < %s
            ORDER BY observed_at ASC
            """,
            (tenant_id, eo_prefix + "%", start_dt, end_dt),
        )
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        logger.exception("Timeseries query failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing timeseries request",
        ) from e
    finally:
        if conn:
            conn.close()

    timestamps_sec = []
    values = []
    for r in rows:
        payload = r.get("payload") or {}
        if isinstance(payload, str):
            payload = json_mod.loads(payload)

        idx = payload.get(index_key) or {}
        value_numeric = idx.get("value") if isinstance(idx, dict) else None
        if value_numeric is None:
            continue

        t = r["observed_at"]
        timestamps_sec.append(float(t.timestamp()) if hasattr(t, "timestamp") else float(t))
        values.append(float(value_numeric))

    if not timestamps_sec:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if resolution and resolution > 0 and len(timestamps_sec) > resolution:
        step = max(1, len(timestamps_sec) // resolution)
        timestamps_sec = timestamps_sec[::step]
        values = values[::step]

    table = pa.table(
        {
            "timestamp": pa.array(timestamps_sec, type=pa.float64()),
            "value": pa.array(values, type=pa.float64()),
        }
    )
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    sink.seek(0)
    return Response(
        content=sink.read(),
        media_type=ARROW_MIME,
    )
