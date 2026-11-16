"""The parcel's zoning layer: the latest completed VRA_ZONES job.

A zoning job writes no raster, so it never shows up among the raster-backed
indices. The map layer and the index pill both read it from here, so the pill
is selectable exactly when the layer has zones to draw.
"""
from typing import Any, Optional

from sqlalchemy import text

from app.models import VegetationJob


def latest_zoning_query(db, tenant_id: str, entity_id: str):
    return (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
            text("vegetation_jobs.result->>'index_type' = 'VRA_ZONES'"),
        )
        .order_by(VegetationJob.created_at.desc())
    )


def latest_zoning_job(db, tenant_id: str, entity_id: str) -> Optional[VegetationJob]:
    return latest_zoning_query(db, tenant_id, entity_id).first()


def zoning_drawable(job: Optional[Any]) -> bool:
    """True when the job's result carries at least one zone feature."""
    result = getattr(job, "result", None) or {}
    features = (result.get("geojson") or {}).get("features") or []
    return len(features) > 0
