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
    try:
        conn = psycopg2.connect(_get_postgres_url(), cursor_factory=RealDictCursor)
        cur = conn.cursor()
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
