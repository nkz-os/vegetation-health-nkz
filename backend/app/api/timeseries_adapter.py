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
# BFF: JSON historical endpoint for VegetationIndex entities (reads from
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

    Queries the telemetry_events hypertable for VegetationIndex entities
    linked to the given parcel. Returns JSON points for the frontend chart.

    Available when FIWARE_NATIVE_MODE is 'dual' or 'true'. Falls back to
    the legacy vegetation_indices_cache table when 'false'.
    """
    import json as json_mod

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

    fiware_mode = os.getenv("FIWARE_NATIVE_MODE", "false").lower().strip()

    if fiware_mode in ("dual", "true"):
        points = await _history_from_telemetry_events(
            tenant_id, entity_id, index_type, start_dt, end_dt,
        )
    else:
        points = await _history_from_legacy_cache(
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
    """Read from telemetry_events hypertable (FIWARE native path)."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    # The entity_id in telemetry_events is the VegetationIndex entity URN.
    # We need to find entities whose payload contains refAgriParcel matching parcel_id.
    # Entity ID pattern: urn:ngsi-ld:VegetationIndex:{tenant}:{parcel_short}
    parcel_short = parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id
    vegetation_entity_pattern = f"urn:ngsi-ld:VegetationIndex:{tenant_id}:{parcel_short}"

    # Attribute name in the payload
    attr_mean = f"{index_type.lower()}Mean"
    attr_min = f"{index_type.lower()}Min"
    attr_max = f"{index_type.lower()}Max"
    attr_std = f"{index_type.lower()}StdDev"

    conn = None
    try:
        conn = psycopg2.connect(_get_telemetry_events_url(), cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT observed_at, payload
            FROM telemetry_events
            WHERE tenant_id = %s
              AND entity_id = %s
              AND entity_type = 'VegetationIndex'
              AND observed_at >= %s
              AND observed_at < %s
            ORDER BY observed_at ASC
            """,
            (tenant_id, vegetation_entity_pattern, start_dt, end_dt),
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
            import json as json_mod
            payload = json_mod.loads(payload)

        mean_prop = payload.get(attr_mean, {})
        min_prop = payload.get(attr_min, {})
        max_prop = payload.get(attr_max, {})
        std_prop = payload.get(attr_std, {})

        mean_val = mean_prop.get("value") if isinstance(mean_prop, dict) else None
        if mean_val is None:
            continue

        points.append({
            "date": row["observed_at"].isoformat() if hasattr(row["observed_at"], "isoformat") else str(row["observed_at"]),
            "mean": float(mean_val),
            "min": float(min_prop.get("value", 0)) if isinstance(min_prop, dict) else None,
            "max": float(max_prop.get("value", 0)) if isinstance(max_prop, dict) else None,
            "std": float(std_prop.get("value", 0)) if isinstance(std_prop, dict) else None,
        })

    return points


async def _history_from_legacy_cache(
    tenant_id: str,
    entity_id: str,
    index_type: str,
    start_dt: datetime,
    end_dt: datetime,
) -> list:
    """Read from vegetation_indices_cache (legacy fallback)."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = None
    try:
        conn = psycopg2.connect(_get_postgres_url(), cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT calculated_at, mean_value, min_value, max_value, std_dev
            FROM vegetation_indices_cache
            WHERE tenant_id = %s
              AND entity_id = %s
              AND index_type = %s
              AND calculated_at >= %s
              AND calculated_at < %s
            ORDER BY calculated_at ASC
            """,
            (tenant_id, entity_id, index_type.upper(), start_dt.isoformat(), end_dt.isoformat()),
        )
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        logger.exception("BFF legacy cache query failed: %s", e)
        raise HTTPException(status_code=500, detail="Error querying historical data")
    finally:
        if conn:
            conn.close()

    points = []
    for row in rows:
        if row.get("mean_value") is None:
            continue
        points.append({
            "date": str(row["calculated_at"]),
            "mean": float(row["mean_value"]),
            "min": float(row["min_value"]) if row.get("min_value") is not None else None,
            "max": float(row["max_value"]) if row.get("max_value") is not None else None,
            "std": float(row["std_dev"]) if row.get("std_dev") is not None else None,
        })

    return points


def _get_postgres_url() -> str:
    url = os.getenv("TIMESERIES_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("TIMESERIES_DATABASE_URL or DATABASE_URL must be set")
    return url


def _attribute_to_metric_name(attribute: str) -> str:
    """Map DataHub attribute to telemetry.metric_name (e.g. ndvi -> NDVI)."""
    return (attribute or "").strip().upper() or "NDVI"


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
    """
    if format != "arrow":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only format=arrow is supported",
        )

    tenant_id = current_user["tenant_id"]
    metric_name = _attribute_to_metric_name(attribute)

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

    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = None
    fiware_mode = os.getenv("FIWARE_NATIVE_MODE", "false").lower().strip()
    try:
        conn = psycopg2.connect(_get_postgres_url(), cursor_factory=RealDictCursor)
        cur = conn.cursor()

        if fiware_mode in ("true", "dual"):
            # SYNC-2: FIWARE native mode → query telemetry_events (canonical source)
            cur.execute(
                """
                SELECT observed_at AS time,
                       (payload->'measurements'->>%s)::float AS value_numeric
                FROM telemetry_events
                WHERE tenant_id = %s
                  AND entity_type = 'VegetationIndex'
                  AND entity_id = %s
                  AND observed_at >= %s
                  AND observed_at < %s
                  AND payload->'measurements'->>%s IS NOT NULL
                ORDER BY observed_at ASC
                """,
                (attribute, tenant_id, entity_id, start_dt, end_dt, attribute),
            )
        else:
            # Legacy mode: query telemetry table
            cur.execute(
                """
                SELECT time, value_numeric
                FROM telemetry
                WHERE tenant_id = %s
                  AND entity_id = %s
                  AND metric_name = %s
                  AND time >= %s
                  AND time < %s
                ORDER BY time ASC
                """,
                (tenant_id, entity_id, metric_name, start_dt, end_dt),
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

    if not rows:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    timestamps_sec = []
    for r in rows:
        t = r["time"]
        if hasattr(t, "timestamp"):
            timestamps_sec.append(float(t.timestamp()))
        else:
            timestamps_sec.append(float(t))
    values = [
        float(r["value_numeric"]) if r["value_numeric"] is not None else float("nan")
        for r in rows
    ]

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
