"""
Internal endpoints for DataHub federation — Apache Arrow IPC timeseries export.

Called exclusively by the DataHub BFF scatter-gather engine, never directly by users.
Auth: The BFF forwards the original user JWT + X-Tenant-ID header, so standard
require_auth applies.

Contract (ADAPTER_SPEC.md):
  POST /api/internal/timeseries/export-arrow
  Body:  { series: [{entity_id, attribute}], start_time, end_time, resolution }
  Response: Arrow IPC stream, Content-Type: application/vnd.apache.arrow.stream
  Timestamp column: Float64 Unix SECONDS (not milliseconds)
  Value columns:
    - Single series  → "value"
    - Multiple series → "value_0", "value_1", ...
"""

import io
import logging
import os
from datetime import date, datetime, timezone
from typing import List, Optional

import pyarrow as pa
import pyarrow.ipc
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth, get_tenant_id
from app.models import VegetationIndexCache, VegetationScene

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])

ARROW_MIME = "application/vnd.apache.arrow.stream"

# Maps DataHub attribute names → vegetation_indices_cache.index_type
# Extend this dict when new indices are added.
ATTRIBUTE_TO_INDEX: dict[str, str] = {
    "ndviMean": "NDVI",
    "eviMean":  "EVI",
    "saviMean": "SAVI",
    "gndviMean": "GNDVI",
    "ndreMean": "NDRE",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SeriesRequest(BaseModel):
    entity_id: str
    attribute: str


class ArrowExportRequest(BaseModel):
    series: List[SeriesRequest]
    start_time: str   # ISO-8601, e.g. "2026-01-01T00:00:00Z"
    end_time: str     # ISO-8601
    resolution: int = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_to_epoch_seconds(d: date) -> float:
    """Convert a date to Float64 Unix epoch seconds (midnight UTC).

    NOTE: The DataHub contract (ADAPTER_SPEC.md) requires seconds, not
    milliseconds. uPlot and the BFF Polars code both expect seconds.
    """
    return float(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _build_arrow_response(series_data: list[tuple[list, list]]) -> bytes:
    """
    Build an Arrow IPC stream from a list of (timestamps, values) tuples.

    Single series  → columns: timestamp, value
    Multiple series → columns: timestamp, value_0, value_1, ...

    All types are Float64 as required by the DataHub contract.
    """
    if not series_data:
        schema = pa.schema([
            pa.field("timestamp", pa.float64()),
            pa.field("value", pa.float64()),
        ])
        table = pa.table({"timestamp": pa.array([], type=pa.float64()),
                          "value":     pa.array([], type=pa.float64())},
                         schema=schema)
    elif len(series_data) == 1:
        ts, vals = series_data[0]
        table = pa.table({
            "timestamp": pa.array(ts,   type=pa.float64()),
            "value":     pa.array(vals, type=pa.float64()),
        })
    else:
        # Align multiple series on a shared timestamp axis (union, NaN for gaps).
        all_ts = sorted({t for ts, _ in series_data for t in ts})
        columns: dict[str, pa.Array] = {
            "timestamp": pa.array(all_ts, type=pa.float64()),
        }
        for i, (ts, vals) in enumerate(series_data):
            ts_to_val = dict(zip(ts, vals))
            aligned = [ts_to_val.get(t, float("nan")) for t in all_ts]
            columns[f"value_{i}"] = pa.array(aligned, type=pa.float64())
        table = pa.table(columns)

    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()


def _downsample(ts: list, vals: list, resolution: int) -> tuple[list, list]:
    """Thin a series to at most `resolution` points by uniform stride."""
    if resolution <= 0 or len(ts) <= resolution:
        return ts, vals
    step = max(1, len(ts) // resolution)
    return ts[::step], vals[::step]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/api/internal/timeseries/export-arrow")
async def export_arrow(
    body: ArrowExportRequest,
    _auth: dict = Depends(require_auth),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db_with_tenant),
):
    """Export timeseries as Arrow IPC for DataHub scatter-gather.

    Queries vegetation_indices_cache (zonal statistics — mean_value) joined
    with vegetation_scenes for the sensing date.  Raw pixel arrays are never
    transmitted.
    """
    if not body.series:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="series array must not be empty",
        )

    try:
        start_dt = datetime.fromisoformat(body.start_time.replace("Z", "+00:00")).date()
        end_dt   = datetime.fromisoformat(body.end_time.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ISO-8601 time: {exc}",
        )

    series_data: list[tuple[list, list]] = []

    for serie in body.series:
        index_type = ATTRIBUTE_TO_INDEX.get(serie.attribute)
        if not index_type:
            logger.warning(
                "Unknown attribute '%s' for entity '%s' — returning empty series.",
                serie.attribute, serie.entity_id,
            )
            series_data.append(([], []))
            continue

        rows = (
            db.query(VegetationScene.sensing_date, VegetationIndexCache.mean_value)
            .join(VegetationIndexCache, VegetationIndexCache.scene_id == VegetationScene.id)
            .filter(
                VegetationIndexCache.tenant_id == tenant_id,
                VegetationIndexCache.entity_id == serie.entity_id,
                VegetationIndexCache.index_type == index_type,
                VegetationScene.sensing_date >= start_dt,
                VegetationScene.sensing_date <= end_dt,
            )
            .order_by(VegetationScene.sensing_date.asc())
            .all()
        )

        ts   = [_date_to_epoch_seconds(r.sensing_date) for r in rows]
        vals = [float(r.mean_value) if r.mean_value is not None else float("nan")
                for r in rows]

        ts, vals = _downsample(ts, vals, body.resolution)
        series_data.append((ts, vals))

    arrow_bytes = _build_arrow_response(series_data)
    return Response(content=arrow_bytes, media_type=ARROW_MIME)
