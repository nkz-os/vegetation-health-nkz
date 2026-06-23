# backend/app/api/jobs.py
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from uuid import UUID
from typing import List, Optional
from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationJob
from app.tasks import download_sentinel2_scene, calculate_vegetation_index
from app.services.limits import LimitsValidator
from app.services.usage_tracker import UsageTracker
from app.schemas import JobCreateRequest, JobResponse
import logging
import os

router = APIRouter(prefix="/api/vegetation/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: JobCreateRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant)
):
    """Crea una nueva tarea de procesamiento (Descarga o Cálculo)."""
    try:
        validator = LimitsValidator(db, current_user['tenant_id'])
        
        # Validar límites (hectáreas)
        is_allowed, error_message, usage_info = validator.check_all_limits(
            job_type=request.job_type,
            bounds=request.bounds,
            ha_to_process=request.ha_to_process
        )
        
        if not is_allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": "Limit exceeded", "message": error_message}
            )
        
        job = VegetationJob(
            tenant_id=current_user['tenant_id'],
            job_type=request.job_type,
            entity_id=request.entity_id,
            entity_type=request.entity_type,
            parameters=request.parameters,
            created_by=current_user.get('user_id')
        )
        
        db.add(job)
        db.commit()
        db.refresh(job)
        
        # Incrementar uso
        UsageTracker.record_job_usage(
            db=db,
            tenant_id=current_user['tenant_id'],
            job_id=str(job.id),
            job_type=request.job_type,
            bounds=request.bounds
        )
        
        # Disparar tarea Celery
        if request.job_type == 'download':
            download_sentinel2_scene.delay(str(job.id), current_user['tenant_id'], request.parameters)
        elif request.job_type == 'calculate_index':
            calculate_vegetation_index.delay(
                str(job.id), 
                current_user['tenant_id'], 
                request.parameters.get('scene_id'),
                request.parameters.get('index_type')
            )
        
        return job
    except Exception as e:
        logger.error(f"Job creation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("")
async def list_jobs(
    entity_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant)
):
    """Lista tareas de procesamiento filtradas por entidad y estado."""
    query = db.query(VegetationJob).filter(VegetationJob.tenant_id == current_user['tenant_id'])
    if entity_id:
        query = query.filter(VegetationJob.entity_id == entity_id)
    if status:
        query = query.filter(VegetationJob.status == status)
    total = query.count()
    jobs = query.order_by(VegetationJob.created_at.desc()).offset(offset).limit(limit).all()
    return {"jobs": [JobResponse.model_validate(j) for j in jobs], "total": total}

@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, current_user: dict = Depends(require_auth), db: Session = Depends(get_db_with_tenant)):
    job = db.query(VegetationJob).filter(
        VegetationJob.id == job_id,
        VegetationJob.tenant_id == current_user['tenant_id']
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/zoning/{parcel_id}/geojson")
async def get_zoning_geojson(
    parcel_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Get latest zoning result as GeoJSON for a parcel."""
    from sqlalchemy import text

    job = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == current_user["tenant_id"],
            VegetationJob.entity_id == parcel_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            # Only VRA_ZONES jobs have zoning geojson
            text("result->>'index_type' = 'VRA_ZONES'"),
        )
        .order_by(VegetationJob.created_at.desc())
        .first()
    )
    if not job or not job.result or "geojson" not in job.result:
        raise HTTPException(status_code=404, detail="No zoning data available")
    return job.result["geojson"]


@router.get("/parcels/{entity_id}/available-raster-dates")
async def get_available_raster_dates(
    entity_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """List sensing dates with computed index rasters for a parcel.

    Returns dates grouped by index_type, ordered newest first.
    Used by the VRA date selector in the frontend.
    """
    tenant_id = current_user["tenant_id"]

    rows = (
        db.query(
            VegetationJob.result["sensing_date"].astext,
            VegetationJob.result["index_type"].astext,
            func.max(VegetationJob.completed_at),
        )
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == entity_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.deleted_at.is_(None),
            VegetationJob.result["raster_path"].astext.isnot(None),
        )
        .group_by(
            VegetationJob.result["sensing_date"].astext,
            VegetationJob.result["index_type"].astext,
        )
        .order_by(func.max(VegetationJob.completed_at).desc())
        .all()
    )

    return {
        "dates": [
            {
                "sensing_date": r[0],
                "index_type": r[1],
                "completed_at": r[2].isoformat() if r[2] else None,
            }
            for r in rows if r[0]
        ]
    }


@router.get("/{job_id}/details")
async def get_job_details(job_id: UUID, current_user: dict = Depends(require_auth), db: Session = Depends(get_db_with_tenant)):
    """Get job with extended details (index stats, scene info)."""
    from app.models import VegetationScene, VegetationIndexCache

    job = db.query(VegetationJob).filter(
        VegetationJob.id == job_id,
        VegetationJob.tenant_id == current_user['tenant_id']
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = {"job": JobResponse.model_validate(job)}

    # If job has a scene, get index stats
    scene_id = (job.result or {}).get("scene_id")
    if scene_id:
        cache = db.query(VegetationIndexCache).filter(
            VegetationIndexCache.scene_id == scene_id,
            VegetationIndexCache.tenant_id == current_user['tenant_id'],
        ).first()
        if cache:
            result["index_stats"] = {
                "mean": float(cache.mean_value) if cache.mean_value else None,
                "min": float(cache.min_value) if cache.min_value else None,
                "max": float(cache.max_value) if cache.max_value else None,
                "std_dev": float(cache.std_dev) if cache.std_dev else None,
                "pixel_count": int(cache.pixel_count) if hasattr(cache, 'pixel_count') and cache.pixel_count else None,
            }

    return result


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: UUID,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Delete any job regardless of status."""
    job = db.query(VegetationJob).filter(
        VegetationJob.id == job_id,
        VegetationJob.tenant_id == current_user['tenant_id']
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    db.delete(job)
    db.commit()


class BulkDeleteRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=100)


@router.post("/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_jobs(
    body: BulkDeleteRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Delete multiple jobs. Soft-deletes in PostgreSQL, cleans EOProducts
    from Orion-LD and rasters from MinIO. Max 100 per request."""
    tenant_id = current_user["tenant_id"]
    deleted = 0
    failed: list[dict] = []

    for job_id in body.ids:
        try:
            job = db.query(VegetationJob).filter(
                VegetationJob.id == job_id,
                VegetationJob.tenant_id == tenant_id,
            ).first()
            if not job:
                failed.append({"id": job_id, "reason": "Not found"})
                continue

            # Clean Orion-LD EOProduct
            if job.sensing_date:
                from app.services.fiware_integration import delete_eo_product, _entity_id_for_optical_eo_product
                try:
                    index_type = (job.parameters or {}).get("index", "NDVI")
                    sensing_str = job.sensing_date.isoformat() if hasattr(job.sensing_date, 'isoformat') else str(job.sensing_date)
                    eo_id = _entity_id_for_optical_eo_product(tenant_id, job.entity_id, index_type, sensing_str)
                    delete_eo_product(tenant_id, eo_id)
                except Exception as e:
                    logger.warning("EOProduct cleanup failed for job %s: %s", job_id, e)

            # Clean MinIO raster
            raster_url = (job.parameters or {}).get("raster_url", "")
            if raster_url and raster_url.startswith("s3://"):
                try:
                    parts = raster_url.replace("s3://", "").split("/", 1)
                    bucket = parts[0]
                    key = parts[1] if len(parts) > 1 else ""
                    from app.services.storage import create_storage_service
                    storage = create_storage_service(
                        storage_type=os.getenv('STORAGE_TYPE', 's3'),
                        default_bucket=bucket
                    )
                    storage.delete_file(key, bucket)
                except Exception as e:
                    logger.warning("Raster cleanup failed for job %s: %s", job_id, e)

            job.deleted_at = datetime.now(timezone.utc)
            deleted += 1
        except Exception as e:
            failed.append({"id": job_id, "reason": str(e)})

    db.commit()
    return {"deleted": deleted, "failed": failed}


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_failed_jobs(
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Delete all failed and stuck jobs for the tenant."""
    from datetime import datetime, timedelta
    stuck_threshold = datetime.now(timezone.utc) - timedelta(hours=1)
    deleted = db.query(VegetationJob).filter(
        VegetationJob.tenant_id == current_user['tenant_id'],
        (VegetationJob.status.in_(['failed', 'cancelled'])) |
        ((VegetationJob.status == 'running') & (VegetationJob.started_at < stuck_threshold))
    ).delete(synchronize_session='fetch')
    db.commit()
    return {"deleted": deleted}
