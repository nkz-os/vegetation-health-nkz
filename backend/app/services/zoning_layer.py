"""The parcel's zoning layer: the zoning in force.

The map layer, the index pill and the prescription exports all read it from
here, so the pill is selectable exactly when the layer has zones to draw.

The zoning in force is the parcel's current version (published to the broker).
A parcel with no recorded versions (zoned before versions existed) falls back
to its latest completed VRA_ZONES job.
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


def _job_features(job: Optional[Any]) -> list[dict]:
    result = getattr(job, "result", None) or {}
    return list((result.get("geojson") or {}).get("features") or [])


def zoning_drawable(job: Optional[Any]) -> bool:
    """True when the job's result carries at least one zone feature."""
    return len(_job_features(job)) > 0


def _v(entity: dict, name: str) -> Any:
    attr = entity.get(name)
    return attr.get("value") if isinstance(attr, dict) else attr


def _feature(run_id: str, entity: dict, accepted: bool) -> dict:
    from shapely.geometry import shape

    from app.jobs.zoning_multidate.run import prescription_rate
    geometry = entity["location"]["value"]
    geom = shape(geometry)
    largest = max(getattr(geom, "geoms", [geom]), key=lambda g: g.area)
    anchor = largest.representative_point()
    rank, level, mean = _v(entity, "zoneRank"), _v(entity, "zoneLevel"), float(_v(entity, "meanValue") or 0)
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            # Legacy keys: exports and older readers.
            "zone_id": rank,
            "cluster_id": rank - 1,
            "zone_class": level,
            "mean_value": mean,
            "area_ha": _v(entity, "areaHa"),
            "prescription_rate": prescription_rate(mean),
            # Multi-date zoning.
            "zone_level": level,
            "relative_pct": _v(entity, "relativeVigorPct"),
            "confirming_dates": _v(entity, "confirmingDates"),
            "total_dates": _v(entity, "totalDates"),
            "label_point": [round(anchor.x, 7), round(anchor.y, 7)],
            # Versions.
            "entity_id": entity.get("id"),
            "run_id": run_id,
            "accepted": accepted,
            "technician_cause": _v(entity, "technicianCause"),
        },
    }


def zoning_features(db, tenant_id: str, entity_id: str) -> list[dict]:
    """GeoJSON features of the zoning in force (empty when there is none)."""
    from app.jobs.zoning_multidate import versions
    cur = versions.current(db, tenant_id, entity_id)
    if cur is not None:
        entities = sorted(cur.entities or [], key=lambda e: _v(e, "zoneRank") or 0)
        return [_feature(cur.run_id, e, cur.accepted_at is not None) for e in entities]
    if versions.has_versions(db, tenant_id, entity_id):
        return []  # the last zoning was withdrawn (no zones); older jobs are not in force
    return _job_features(latest_zoning_job(db, tenant_id, entity_id))
