"""
Crop seasons API — assign crop + date range to a parcel, toggle monitoring.
"""

import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models.crop_seasons import VegetationCropSeason

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/crop-seasons", tags=["crop-seasons"])


class CropSeasonCreate(BaseModel):
    crop_type: str = Field(..., min_length=1, max_length=50)
    start_date: date
    end_date: Optional[date] = None
    label: Optional[str] = None
    monitoring_enabled: bool = False


class CropSeasonUpdate(BaseModel):
    crop_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    label: Optional[str] = None
    monitoring_enabled: Optional[bool] = None
    is_active: Optional[bool] = None


def _serialize(season: VegetationCropSeason) -> dict:
    return {
        "id": str(season.id),
        "entity_id": season.entity_id,
        "crop_type": season.crop_type,
        "start_date": season.start_date.isoformat() if season.start_date else None,
        "end_date": season.end_date.isoformat() if season.end_date else None,
        "label": season.label,
        "monitoring_enabled": season.monitoring_enabled,
        "is_active": season.is_active,
        "created_at": season.created_at.isoformat() if season.created_at else None,
        "updated_at": season.updated_at.isoformat() if season.updated_at else None,
    }


@router.get("/{entity_id}")
async def list_crop_seasons(
    entity_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """List all (non-soft-deleted) crop seasons for a parcel."""
    tenant_id = current_user["tenant_id"]
    seasons = (
        db.query(VegetationCropSeason)
        .filter(
            VegetationCropSeason.tenant_id == tenant_id,
            VegetationCropSeason.entity_id == entity_id,
            VegetationCropSeason.deleted_at.is_(None),
        )
        .order_by(VegetationCropSeason.start_date.desc())
        .all()
    )
    return {"seasons": [_serialize(s) for s in seasons]}


@router.post("/{entity_id}", status_code=status.HTTP_201_CREATED)
async def create_crop_season(
    entity_id: str,
    data: CropSeasonCreate,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Assign a crop season to a parcel."""
    tenant_id = current_user["tenant_id"]

    if data.end_date and data.end_date < data.start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be on or after start_date",
        )

    season = VegetationCropSeason(
        tenant_id=tenant_id,
        entity_id=entity_id,
        crop_type=data.crop_type,
        start_date=data.start_date,
        end_date=data.end_date,
        label=data.label or f"{data.crop_type} {data.start_date.year}",
        monitoring_enabled=data.monitoring_enabled,
    )
    db.add(season)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        err_text = str(exc.orig)
        if "vegetation_crop_seasons_no_overlap" in err_text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"This season overlaps an existing one on the same parcel. "
                    f"Two crop seasons cannot share calendar days. Pick non-conflicting dates "
                    f"or close/delete the conflicting season first."
                ),
            ) from exc
        if "uq_entity_crop_period" in err_text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"A crop season for '{data.crop_type}' starting on "
                    f"{data.start_date.isoformat()} already exists for this parcel. "
                    "Edit the existing one or pick a different start date."
                ),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not create crop season due to a database constraint.",
        ) from exc
    db.refresh(season)

    logger.info(
        "Created crop season %s for entity %s: %s (%s → %s)",
        str(season.id),
        entity_id,
        season.crop_type,
        season.start_date,
        season.end_date or "ongoing",
    )

    return {"season": _serialize(season)}


@router.patch("/{entity_id}/{season_id}")
async def update_crop_season(
    entity_id: str,
    season_id: str,
    data: CropSeasonUpdate,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Update a crop season."""
    tenant_id = current_user["tenant_id"]
    season = (
        db.query(VegetationCropSeason)
        .filter(
            VegetationCropSeason.id == season_id,
            VegetationCropSeason.tenant_id == tenant_id,
            VegetationCropSeason.entity_id == entity_id,
            VegetationCropSeason.deleted_at.is_(None),
        )
        .first()
    )
    if not season:
        raise HTTPException(status_code=404, detail="Crop season not found")

    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(season, key, value)

    if season.end_date and season.start_date and season.end_date < season.start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be on or after start_date",
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Update conflicts with an existing crop season for this parcel.",
        ) from exc
    db.refresh(season)
    return {"season": _serialize(season)}


@router.post("/{entity_id}/{season_id}/stop")
async def stop_crop_season(
    entity_id: str,
    season_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Stop monitoring for a crop season. Cancels pending jobs."""
    tenant_id = current_user["tenant_id"]
    season = (
        db.query(VegetationCropSeason)
        .filter(
            VegetationCropSeason.id == season_id,
            VegetationCropSeason.tenant_id == tenant_id,
            VegetationCropSeason.entity_id == entity_id,
            VegetationCropSeason.deleted_at.is_(None),
        )
        .first()
    )
    if not season:
        raise HTTPException(status_code=404, detail="Crop season not found")

    season.monitoring_enabled = False

    # Cancel pending jobs
    from app.models.jobs import VegetationJob
    pending_jobs = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.crop_season_id == season_id,
            VegetationJob.status.in_(["pending", "queued", "downloading"]),
            VegetationJob.deleted_at.is_(None),
        )
        .all()
    )
    cancelled = 0
    for job in pending_jobs:
        job.status = "cancelled"
        job.deleted_at = datetime.now(timezone.utc)
        cancelled += 1

    db.commit()
    logger.info("Stopped season %s, cancelled %s jobs", season_id, cancelled)
    return {"status": "stopped", "season_id": season_id, "cancelled_jobs": cancelled}


@router.delete("/{entity_id}/{season_id}")
async def delete_crop_season(
    entity_id: str,
    season_id: str,
    cascade: bool = True,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Delete a crop season. Soft-deletes in PostgreSQL.

    With cascade=true (default): also deletes associated jobs (soft-delete),
    EOProduct entities from Orion-LD, and raster files from MinIO.
    """
    tenant_id = current_user["tenant_id"]
    season = (
        db.query(VegetationCropSeason)
        .filter(
            VegetationCropSeason.id == season_id,
            VegetationCropSeason.tenant_id == tenant_id,
            VegetationCropSeason.entity_id == entity_id,
            VegetationCropSeason.deleted_at.is_(None),
        )
        .first()
    )
    if not season:
        return {"deleted": True, "id": season_id}

    deleted_jobs = 0
    deleted_rasters = 0

    if cascade:
        from app.models.jobs import VegetationJob
        from app.services.fiware_integration import delete_eo_product
        from app.services.storage import storage_service

        jobs = (
            db.query(VegetationJob)
            .filter(
                VegetationJob.crop_season_id == season_id,
                VegetationJob.deleted_at.is_(None),
            )
            .all()
        )

        for job in jobs:
            job.deleted_at = datetime.now(timezone.utc)
            deleted_jobs += 1

            # Delete EOProduct from Orion-LD
            if job.sensing_date:
                index_type = (job.parameters or {}).get("index", "NDVI")
                sensing_str = job.sensing_date.isoformat() if hasattr(job.sensing_date, 'isoformat') else str(job.sensing_date)
                from app.services.fiware_integration import _entity_id_for_optical_eo_product
                eo_id = _entity_id_for_optical_eo_product(tenant_id, entity_id, index_type, sensing_str)
                delete_eo_product(tenant_id, eo_id)

            # Delete raster from MinIO
            raster_url = (job.parameters or {}).get("raster_url", "")
            if raster_url and raster_url.startswith("s3://"):
                try:
                    key = raster_url.replace("s3://vegetation-prime-global/", "")
                    storage_service.delete_object("vegetation-prime-global", key)
                    deleted_rasters += 1
                except Exception as e:
                    logger.warning("Failed to delete raster %s: %s", raster_url, e)

    season.deleted_at = datetime.now(timezone.utc)
    season.is_active = False
    season.monitoring_enabled = False
    db.commit()

    logger.info("Deleted season %s: %s jobs, %s rasters", season_id, deleted_jobs, deleted_rasters)
    return {
        "deleted": True,
        "id": season_id,
        "cascade": cascade,
        "deleted_jobs": deleted_jobs,
        "deleted_rasters": deleted_rasters,
    }
