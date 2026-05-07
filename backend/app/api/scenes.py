# backend/app/api/scenes.py
"""
Scene query endpoints matching frontend API client expectations.
Routes: /api/vegetation/scenes, /api/vegetation/scenes/{entity_id}/stats,
        /api/vegetation/capabilities, /api/vegetation/calculate
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
import logging
import uuid as uuid_mod

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationScene, VegetationIndexCache, VegetationJob, VegetationCustomFormula
from app.tasks import calculate_vegetation_index, download_sentinel2_scene

router = APIRouter(prefix="/api/vegetation", tags=["scenes"])
logger = logging.getLogger(__name__)


def _job_result_matches_scene(job: VegetationJob, scene_uuid: str) -> bool:
    """Match completed calculate_index jobs to a vegetation_scenes.id (legacy rows lack scene_id in JSON)."""
    r = job.result if isinstance(job.result, dict) else {}
    if r.get("scene_id") == scene_uuid:
        return True
    rp = r.get("raster_path") or ""
    needle = f"/scenes/{scene_uuid}/"
    return needle in rp


class CalculateRequest(BaseModel):
    scene_id: Optional[str] = None
    index_type: str = "NDVI"
    entity_id: Optional[str] = None
    formula: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    formula_id: Optional[str] = None
    formula_name: Optional[str] = None
    source_index: Optional[str] = None
    n_zones: int = 3


class ZoningRequest(BaseModel):
    n_zones: int = 3
    source_index: str = 'NDVI'
    delegate_to_intelligence: bool = False
    n8n_callback_url: Optional[str] = None


@router.post("/calculate")
async def calculate_index_endpoint(
    request: CalculateRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Launch a vegetation index calculation job.

    This is the main endpoint called by the frontend to trigger index computation.
    Creates a VegetationJob and dispatches a Celery task.
    """
    tenant_id = current_user["tenant_id"]

    # VRA_ZONES works on already-computed rasters, not Sentinel scenes
    requires_scene = request.index_type != 'VRA_ZONES'
    if requires_scene and not request.scene_id and not (request.start_date and request.end_date):
        raise HTTPException(
            status_code=422,
            detail="Either scene_id or both start_date and end_date are required",
        )

    # Build parameters with formula metadata to prevent CUSTOM key collision
    parameters = {
        "scene_id": request.scene_id,
        "index_type": request.index_type,
        "entity_id": request.entity_id,
        "formula": request.formula,
        "start_date": request.start_date,
        "end_date": request.end_date,
    }
    if request.formula_id:
        parameters["formula_id"] = request.formula_id
        parameters["formula_name"] = request.formula_name or ""
        parameters["formula_expression"] = request.formula
        parameters["result_index_key"] = f"custom:{request.formula_id}"
    if request.index_type == 'VRA_ZONES':
        parameters["source_index"] = request.source_index or 'NDVI'
        parameters["n_zones"] = request.n_zones

    job = VegetationJob(
        tenant_id=tenant_id,
        job_type="calculate_index",
        entity_id=request.entity_id,
        entity_type="AgriParcel",
        parameters=parameters,
        created_by=current_user.get("user_id"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        async_result = calculate_vegetation_index.delay(
            job_id=str(job.id),
            tenant_id=tenant_id,
            scene_id=request.scene_id,
            index_type=request.index_type,
            formula=request.formula,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        job.celery_task_id = async_result.id
        db.commit()
    except Exception as exc:
        logger.exception("Failed to enqueue calculate task for job %s", job.id)
        job.status = "failed"
        job.error_message = f"Could not enqueue Celery task: {exc}"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Job queue unavailable, please retry shortly.",
        ) from exc

    logger.info("Calculate job %s dispatched for tenant %s", job.id, tenant_id)
    return {"job_id": str(job.id), "message": "Calculation started"}


class AnalyzeRequest(BaseModel):
    entity_id: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    indices: Optional[list] = None  # defaults to all main indices
    custom_formulas: Optional[List[str]] = None
    local_cloud_threshold: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Max acceptable cloud coverage (%) over the parcel polygon. "
                    "Below this, the scene is processed; above, it's skipped. "
                    "Server default: 30 (recommended). Suggested: 10 strict (clear-sky regions), "
                    "30 balanced (default), 50 permissive (Atlantic/Cantabrian climate).",
    )


def _resolve_custom_formula_specs(
    db: Session, tenant_id: str, custom_formula_ids: List[str]
) -> List[Dict[str, Any]]:
    """Validate and serialise tenant-scoped custom formulas. Raises 404/422."""
    if not custom_formula_ids:
        return []
    valid_ids = []
    for formula_id in custom_formula_ids:
        try:
            valid_ids.append(uuid_mod.UUID(formula_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid custom formula id: {formula_id}"
            ) from exc

    formula_rows = (
        db.query(VegetationCustomFormula)
        .filter(
            VegetationCustomFormula.tenant_id == tenant_id,
            VegetationCustomFormula.id.in_(valid_ids),
        )
        .all()
    )
    found_ids = {str(row.id) for row in formula_rows}
    missing = [fid for fid in custom_formula_ids if fid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Custom formulas not found for tenant: {', '.join(missing)}",
        )
    return [
        {
            "formula_id": str(row.id),
            "formula_name": row.name,
            "formula_expression": row.formula,
            "index_key": f"custom:{str(row.id)}",
        }
        for row in formula_rows
    ]


async def _dispatch_analyze_for_parcel(
    *,
    db: Session,
    tenant_id: str,
    entity_id: str,
    user_id: Optional[str],
    indices: Optional[List[str]],
    custom_formula_specs: List[Dict[str, Any]],
    start_date: Optional[str],
    end_date: Optional[str],
    local_cloud_threshold: Optional[float],
    crop_season_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Shared analyze pipeline: resolve geometry, search scenes, group into
    dekadal windows, create one download job per window and dispatch the
    Celery task. Optionally binds the resulting jobs to a crop_season_id.

    Used by both the legacy POST /analyze and the new
    POST /parcels/{id}/seasons/{sid}/analyze.
    """
    import os
    from datetime import date as date_type, timedelta

    # Default date range: last 30 days when caller did not constrain it.
    end_date_iso = end_date or date_type.today().isoformat()
    start_date_iso = start_date or (date_type.today() - timedelta(days=30)).isoformat()

    indices_list = indices or ["NDVI", "EVI", "SAVI", "GNDVI", "NDRE"]

    # Get parcel geometry from Orion-LD
    geometry = None
    bbox = None
    try:
        import httpx

        orion_url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
        headers = {"Accept": "application/json", "NGSILD-Tenant": tenant_id}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{orion_url}/ngsi-ld/v1/entities/{entity_id}", headers=headers
            )
            if resp.status_code == 200:
                entity = resp.json()
                loc = entity.get("location", {})
                geom = loc.get("value") or loc
                if geom and "coordinates" in geom:
                    geometry = geom
                    from shapely.geometry import shape as shp
                    geom_obj = shp(geom)
                    bbox = list(geom_obj.bounds)
    except Exception as exc:
        logger.warning("Could not fetch entity geometry from Orion-LD: %s", exc)

    if not bbox:
        raise HTTPException(
            status_code=422,
            detail="Could not determine parcel geometry. Ensure the parcel has a location in the context broker.",
        )

    from app.services.copernicus_client import CopernicusDataSpaceClient
    from app.services.platform_credentials import get_copernicus_credentials_with_fallback
    from app.services.temporal_utils import group_scenes_into_windows

    creds = get_copernicus_credentials_with_fallback()
    if not creds:
        raise HTTPException(status_code=503, detail="Copernicus credentials not configured")
    copernicus = CopernicusDataSpaceClient()
    copernicus.set_credentials(creds["client_id"], creds["client_secret"])

    from shapely.geometry import shape as shp_fn
    geom_obj = shp_fn(geometry)
    intersects_geojson = geometry
    if geom_obj.geom_type == "MultiPolygon":
        largest = max(geom_obj.geoms, key=lambda g: g.area)
        intersects_geojson = largest.__geo_interface__

    from datetime import date as _date_type
    all_scenes = copernicus.search_scenes(
        intersects=intersects_geojson,
        start_date=_date_type.fromisoformat(start_date_iso),
        end_date=_date_type.fromisoformat(end_date_iso),
        cloud_cover_lte=60,
        limit=50,
    )

    if not all_scenes:
        raise HTTPException(status_code=404, detail="No scenes found in the selected date range")

    windows = group_scenes_into_windows(all_scenes, date_key="sensing_date")

    season_uuid = None
    if crop_season_id:
        try:
            season_uuid = uuid_mod.UUID(crop_season_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid crop_season_id") from exc

    # Stage 1: build all VegetationJob rows in memory and commit them in a
    # single transaction. Avoids the per-iteration commit pattern that left
    # the caller with a partial set of rows + 503 when window N failed.
    pending_jobs: List[VegetationJob] = []
    for window in windows:
        best = sorted(window["scenes"], key=lambda s: s.get("cloud_cover", 100))[0]

        job_parameters = {
            "scene_id": best["id"],
            "bbox": bbox,
            "bounds": geometry,
            "entity_id": entity_id,
            "cloud_coverage_threshold": 60,
            "calculate_indices": indices_list,
            "calculate_custom_formulas": custom_formula_specs,
        }
        if local_cloud_threshold is not None:
            job_parameters["local_cloud_threshold"] = float(local_cloud_threshold)

        pending_jobs.append(
            VegetationJob(
                tenant_id=tenant_id,
                job_type="download",
                entity_id=entity_id,
                entity_type="AgriParcel",
                parameters=job_parameters,
                created_by=user_id,
                crop_season_id=season_uuid,
            )
        )

    db.add_all(pending_jobs)
    db.commit()
    for j in pending_jobs:
        db.refresh(j)

    # Stage 2: dispatch each Celery task. If any .delay() fails, mark every
    # already-enqueued job (and the rest) as failed in a single follow-up
    # commit so the response is consistent with the DB state.
    job_ids: List[str] = []
    enqueue_error: Optional[Exception] = None
    for idx, job in enumerate(pending_jobs):
        try:
            async_result = download_sentinel2_scene.delay(
                str(job.id), tenant_id, job.parameters
            )
            job.celery_task_id = async_result.id
            job_ids.append(str(job.id))
        except Exception as exc:
            logger.exception("Failed to enqueue download task for job %s", job.id)
            enqueue_error = exc
            for k in range(idx, len(pending_jobs)):
                pending_jobs[k].status = "failed"
                pending_jobs[k].error_message = (
                    f"Could not enqueue Celery task: {exc}"
                )
            break
    db.commit()

    if enqueue_error is not None:
        raise HTTPException(
            status_code=503,
            detail="Job queue unavailable, please retry shortly.",
        ) from enqueue_error

    logger.info(
        "Multi-scene analysis: %d windows dispatched for entity %s (scenes: %d, indices: %s, season: %s)",
        len(windows), entity_id, len(all_scenes), indices_list, crop_season_id,
    )
    return {
        "job_id": job_ids[0] if job_ids else None,
        "job_ids": job_ids,
        "message": f"Analysis started: {len(windows)} date windows, {len(all_scenes)} scenes found",
        "indices": indices_list,
        "custom_formulas": custom_formula_specs,
        "windows": len(windows),
        "scenes_found": len(all_scenes),
        "date_range": {"start": start_date_iso, "end": end_date_iso},
        "crop_season_id": crop_season_id,
    }


@router.post("/analyze")
async def analyze_parcel(
    request: AnalyzeRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Legacy one-shot analyze (no season binding). Kept for the unified
    viewer slot path. The module page uses the season-bound endpoint
    POST /api/vegetation/parcels/{eid}/seasons/{sid}/analyze instead.
    """
    tenant_id = current_user["tenant_id"]
    custom_formula_specs = _resolve_custom_formula_specs(
        db, tenant_id, request.custom_formulas or []
    )
    return await _dispatch_analyze_for_parcel(
        db=db,
        tenant_id=tenant_id,
        entity_id=request.entity_id,
        user_id=current_user.get("user_id"),
        indices=request.indices,
        custom_formula_specs=custom_formula_specs,
        start_date=request.start_date,
        end_date=request.end_date,
        local_cloud_threshold=request.local_cloud_threshold,
        crop_season_id=None,
    )


@router.get("/results/{entity_id}")
async def get_entity_results(
    entity_id: str,
    scene_id: Optional[str] = Query(
        None,
        description="If set, only jobs tied to this vegetation_scenes.id (UUID) are considered.",
    ),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Get latest completed calculation results per index type for an entity.

    Returns a map of index_type -> { job_id, statistics, raster_path, date }.
    Frontend uses this to populate stats and enable map layer switching.
    Optional scene_id scopes results to one acquisition so the map matches the date selector.
    """
    tenant_id = current_user["tenant_id"]

    if scene_id:
        try:
            uuid_mod.UUID(scene_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid scene_id (expected UUID)") from exc

    # Pull only completed calc_index jobs that produced an actual raster.
    # Filtering skipped:true / raster_path=null at SQL level (instead of
    # post-hoc in Python) ensures the LIMIT does not silently drop the
    # newest 'real' result per index when many recent rows are skipped
    # — which is exactly what happened on the montiko parcel where the
    # newest 50 completed rows were dominated by idempotency-skipped
    # retries and the slot ended up with indexResults={} despite the
    # parcel having 8 usable rasters per index.
    jobs = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
            VegetationJob.result["raster_path"].astext.isnot(None),
        )
        .order_by(desc(VegetationJob.created_at))
        .limit(500 if scene_id else 100)
        .all()
    )

    if scene_id:
        jobs = [j for j in jobs if _job_result_matches_scene(j, scene_id)]

    # Build a map: only keep the latest job per index type
    results = {}
    for job in jobs:
        if not job.result:
            continue
        index_type = job.result.get("index_type")
        index_key = job.result.get("index_key") or index_type
        if not index_type or not index_key or index_key in results:
            continue  # already have a newer one
        stats = job.result.get("statistics", {})
        results[index_key] = {
            "job_id": str(job.id),
            "index_key": index_key,
            "index_type": index_type,
            "is_custom": bool(job.result.get("is_custom", False)),
            "formula_id": job.result.get("formula_id"),
            "formula_name": job.result.get("formula_name"),
            "statistics": {
                "mean": stats.get("mean"),
                "min": stats.get("min"),
                "max": stats.get("max"),
                "std_dev": stats.get("std"),
                "pixel_count": stats.get("pixel_count"),
            },
            "raster_path": job.result.get("raster_path"),
            "is_composite": job.result.get("is_composite", False),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "scene_id": job.result.get("scene_id"),
            "sensing_date": job.result.get("sensing_date"),
        }

    # Also check for pending/running jobs
    active_jobs = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.status.in_(["pending", "running"]),
        )
        .count()
    )

    return {
        "entity_id": entity_id,
        "scene_id": scene_id,
        "indices": results,
        "active_jobs": active_jobs,
        "has_results": len(results) > 0,
    }


@router.post("/jobs/zoning/{parcel_id}")
async def trigger_zoning(
    parcel_id: str,
    request: ZoningRequest = ZoningRequest(),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Trigger a VRA zoning job for a parcel."""
    tenant_id = current_user["tenant_id"]

    job = VegetationJob(
        tenant_id=tenant_id,
        job_type="calculate_index",
        entity_id=parcel_id,
        entity_type="AgriParcel",
        parameters={
            "index_type": "VRA_ZONES",
            "entity_id": parcel_id,
            "n_zones": request.n_zones,
            "source_index": request.source_index,
        },
        created_by=current_user.get("user_id"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # For now, zoning is a calculate_index job with VRA_ZONES type
    calculate_vegetation_index.delay(
        job_id=str(job.id),
        tenant_id=tenant_id,
        index_type="VRA_ZONES",
    )

    return {
        "message": "Zoning job started",
        "task_id": str(job.id),
        "parcel_id": parcel_id,
        "webhook_metadata": {},
    }


@router.get("/scenes")
async def list_scenes(
    entity_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """List vegetation scenes, optionally filtered by entity."""
    tenant_id = current_user["tenant_id"]

    query = (
        db.query(VegetationScene)
        .filter(VegetationScene.tenant_id == tenant_id, VegetationScene.is_valid == True)
    )

    if entity_id:
        # Scenes with at least one cached index for this parcel
        rows_cache = (
            db.query(VegetationIndexCache.scene_id)
            .filter(
                VegetationIndexCache.tenant_id == tenant_id,
                VegetationIndexCache.entity_id == entity_id,
            )
            .distinct()
            .all()
        )
        # Scenes from completed download jobs (indices may still be queued on single worker)
        rows_dl = (
            db.query(VegetationScene.id)
            .join(VegetationJob, VegetationScene.job_id == VegetationJob.id)
            .filter(
                VegetationJob.tenant_id == tenant_id,
                VegetationJob.entity_id == entity_id,
                VegetationJob.job_type == "download",
                VegetationJob.status == "completed",
                VegetationScene.tenant_id == tenant_id,
                VegetationScene.is_valid == True,
            )
            .distinct()
            .all()
        )
        combined_ids = {r[0] for r in rows_cache} | {r[0] for r in rows_dl}
        if not combined_ids:
            return {"scenes": [], "total": 0}
        query = query.filter(VegetationScene.id.in_(combined_ids))

    if start_date:
        query = query.filter(VegetationScene.sensing_date >= start_date)
    if end_date:
        query = query.filter(VegetationScene.sensing_date <= end_date)

    total = query.count()
    scenes = query.order_by(desc(VegetationScene.sensing_date)).limit(limit).all()

    return {
        "scenes": [
            {
                "id": str(s.id),
                "scene_id": s.scene_id,
                "sensing_date": s.sensing_date.isoformat(),
                "acquisition_datetime": s.acquisition_datetime.isoformat() if s.acquisition_datetime else None,
                "cloud_coverage": s.cloud_coverage,
                "platform": s.platform,
                "is_valid": s.is_valid,
            }
            for s in scenes
        ],
        "total": total,
    }


@router.get("/scenes/{entity_id}/stats")
async def get_scene_stats(
    entity_id: str,
    index_type: str = Query("NDVI"),
    months: int = Query(12, le=36),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Aggregated stats for an entity's vegetation index over time."""
    tenant_id = current_user["tenant_id"]
    since = datetime.utcnow() - timedelta(days=months * 30)

    rows = (
        db.query(VegetationScene.sensing_date, VegetationIndexCache)
        .join(VegetationIndexCache, VegetationIndexCache.scene_id == VegetationScene.id)
        .filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.entity_id == entity_id,
            VegetationIndexCache.index_type == index_type.upper(),
            VegetationScene.sensing_date >= since.date(),
            VegetationScene.is_valid == True,
        )
        .order_by(VegetationScene.sensing_date.asc())
        .all()
    )

    data_points = []
    for sensing_date, cache in rows:
        data_points.append({
            "date": sensing_date.isoformat(),
            "mean": float(cache.mean_value) if cache.mean_value else None,
            "min": float(cache.min_value) if cache.min_value else None,
            "max": float(cache.max_value) if cache.max_value else None,
            "std_dev": float(cache.std_dev) if cache.std_dev else None,
        })

    # Overall stats
    agg = (
        db.query(
            func.avg(VegetationIndexCache.mean_value),
            func.min(VegetationIndexCache.min_value),
            func.max(VegetationIndexCache.max_value),
            func.count(VegetationIndexCache.id),
        )
        .join(VegetationScene, VegetationScene.id == VegetationIndexCache.scene_id)
        .filter(
            VegetationIndexCache.tenant_id == tenant_id,
            VegetationIndexCache.entity_id == entity_id,
            VegetationIndexCache.index_type == index_type.upper(),
            VegetationScene.sensing_date >= since.date(),
            VegetationScene.is_valid == True,
        )
        .first()
    )

    return {
        "entity_id": entity_id,
        "index_type": index_type.upper(),
        "months": months,
        "data_points": data_points,
        "summary": {
            "avg": float(agg[0]) if agg[0] else None,
            "min": float(agg[1]) if agg[1] else None,
            "max": float(agg[2]) if agg[2] else None,
            "count": agg[3] or 0,
        },
    }


@router.get("/capabilities")
async def get_capabilities(current_user: dict = Depends(require_auth)):
    """Return module capabilities based on actual configuration state."""
    import os

    client_id = os.getenv("COPERNICUS_CLIENT_ID", "")
    has_secret = bool(os.getenv("COPERNICUS_CLIENT_SECRET"))
    copernicus_available = bool(client_id and has_secret)

    n8n_available = bool(os.getenv("N8N_WEBHOOK_URL"))

    # Check if ISOXML export dependencies are installed
    try:
        import fiona  # noqa: F401
        isobus_available = True
    except ImportError:
        isobus_available = False

    return {
        "copernicus_available": copernicus_available,
        "n8n_available": n8n_available,
        "isobus_available": isobus_available,
        "features": {
            "alerts_webhook": n8n_available,
            "export_isoxml": isobus_available,
            "sentinel_ingest": copernicus_available,
            "index_calculation": copernicus_available,
        },
    }


@router.get("/config")
async def get_config(current_user: dict = Depends(require_auth)):
    """Return tenant vegetation config (defaults for now)."""
    import os
    return {
        "default_index": "NDVI",
        "auto_process": False,
        "cloud_threshold": 30,
        "copernicus_client_id": os.getenv("COPERNICUS_CLIENT_ID", ""),
        "copernicus_client_secret_set": bool(os.getenv("COPERNICUS_CLIENT_SECRET")),
    }


@router.post("/config")
async def update_config(
    config: dict,
    current_user: dict = Depends(require_auth),
):
    """Update tenant vegetation config (stub — returns input as saved)."""
    return {"message": "Config saved", "config": config}


@router.get("/config/credentials-status")
async def get_credentials_status(current_user: dict = Depends(require_auth)):
    """Check if Copernicus credentials are configured."""
    import os
    client_id = os.getenv("COPERNICUS_CLIENT_ID", "")
    has_secret = bool(os.getenv("COPERNICUS_CLIENT_SECRET"))
    available = bool(client_id and has_secret)
    return {
        "available": available,
        "source": "platform" if available else None,
        "message": "Credentials configured" if available else "No Copernicus credentials configured",
        "client_id_preview": client_id[:8] + "..." if client_id else None,
    }


@router.get("/usage/current")
async def get_current_usage(
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Return current usage stats for the tenant."""
    from app.models import VegetationJob
    from datetime import datetime

    tenant_id = current_user["tenant_id"]
    today = datetime.utcnow().date()

    jobs_today = (
        db.query(func.count(VegetationJob.id))
        .filter(
            VegetationJob.tenant_id == tenant_id,
            func.date(VegetationJob.created_at) == today,
        )
        .scalar() or 0
    )

    return {
        "plan": "free",
        "volume": {"used_ha": 0, "limit_ha": 1000},
        "frequency": {"used_jobs_today": jobs_today, "limit_jobs_today": 50},
    }
