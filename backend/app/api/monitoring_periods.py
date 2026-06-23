"""
Monitoring periods API — assign date ranges to a parcel for vegetation monitoring.
"""
import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models.crop_seasons import VegetationMonitoringPeriod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/monitoring-periods", tags=["monitoring-periods"])


class MonitoringPeriodCreate(BaseModel):
    start_date: date
    end_date: Optional[date] = None
    label: Optional[str] = None
    monitoring_enabled: bool = False


class MonitoringPeriodUpdate(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    label: Optional[str] = None
    monitoring_enabled: Optional[bool] = None
    is_active: Optional[bool] = None


def _serialize(period: VegetationMonitoringPeriod) -> dict:
    return {
        "id": str(period.id),
        "entity_id": period.entity_id,
        "start_date": period.start_date.isoformat() if period.start_date else None,
        "end_date": period.end_date.isoformat() if period.end_date else None,
        "label": period.label,
        "monitoring_enabled": period.monitoring_enabled,
        "is_active": period.is_active,
        "created_at": period.created_at.isoformat() if period.created_at else None,
        "updated_at": period.updated_at.isoformat() if period.updated_at else None,
    }


@router.get("/{entity_id}")
async def list_monitoring_periods(
    entity_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """List all (non-soft-deleted) monitoring periods for a parcel."""
    tenant_id = current_user["tenant_id"]
    periods = (
        db.query(VegetationMonitoringPeriod)
        .filter(
            VegetationMonitoringPeriod.tenant_id == tenant_id,
            VegetationMonitoringPeriod.entity_id == entity_id,
            VegetationMonitoringPeriod.deleted_at.is_(None),
        )
        .order_by(VegetationMonitoringPeriod.start_date.desc())
        .all()
    )
    return {"seasons": [_serialize(p) for p in periods]}  # Keep "seasons" key for backward compat


@router.post("/{entity_id}", status_code=status.HTTP_201_CREATED)
async def create_monitoring_period(
    entity_id: str,
    data: MonitoringPeriodCreate,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Create a monitoring period (date range) for a parcel."""
    tenant_id = current_user["tenant_id"]

    if data.end_date and data.end_date < data.start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be on or after start_date",
        )

    period = VegetationMonitoringPeriod(
        tenant_id=tenant_id,
        entity_id=entity_id,
        start_date=data.start_date,
        end_date=data.end_date,
        label=data.label or f"Season {data.start_date.year}",
        monitoring_enabled=data.monitoring_enabled,
    )
    db.add(period)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        err_text = str(exc.orig)
        if "vegetation_monitoring_periods_no_overlap" in err_text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"This period overlaps an existing one on the same parcel. "
                    f"Two monitoring periods cannot share calendar days. Pick non-conflicting dates "
                    f"or close/delete the conflicting period first."
                ),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not create monitoring period due to a database constraint.",
        ) from exc
    db.refresh(period)

    logger.info(
        "Created monitoring period %s for entity %s (%s → %s)",
        str(period.id),
        entity_id,
        period.start_date,
        period.end_date or "ongoing",
    )

    return {"season": _serialize(period)}


@router.patch("/{entity_id}/{period_id}")
async def update_monitoring_period(
    entity_id: str,
    period_id: str,
    data: MonitoringPeriodUpdate,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Update a monitoring period."""
    tenant_id = current_user["tenant_id"]
    period = (
        db.query(VegetationMonitoringPeriod)
        .filter(
            VegetationMonitoringPeriod.id == period_id,
            VegetationMonitoringPeriod.tenant_id == tenant_id,
            VegetationMonitoringPeriod.entity_id == entity_id,
            VegetationMonitoringPeriod.deleted_at.is_(None),
        )
        .first()
    )
    if not period:
        raise HTTPException(status_code=404, detail="Monitoring period not found")

    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(period, key, value)

    if period.end_date and period.start_date and period.end_date < period.start_date:
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
            detail="Update conflicts with an existing monitoring period for this parcel.",
        ) from exc
    db.refresh(period)
    return {"season": _serialize(period)}


@router.post("/{entity_id}/{period_id}/stop")
async def stop_monitoring_period(
    entity_id: str,
    period_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Stop monitoring for a period. Cancels pending jobs."""
    tenant_id = current_user["tenant_id"]
    period = (
        db.query(VegetationMonitoringPeriod)
        .filter(
            VegetationMonitoringPeriod.id == period_id,
            VegetationMonitoringPeriod.tenant_id == tenant_id,
            VegetationMonitoringPeriod.entity_id == entity_id,
            VegetationMonitoringPeriod.deleted_at.is_(None),
        )
        .first()
    )
    if not period:
        raise HTTPException(status_code=404, detail="Monitoring period not found")

    period.monitoring_enabled = False

    # Cancel pending jobs
    from app.models.jobs import VegetationJob
    pending_jobs = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.crop_season_id == period_id,
            # Real CHECK constraint values: pending, running, completed, failed, cancelled
            VegetationJob.status.in_(["pending", "running"]),
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
    logger.info("Stopped period %s, cancelled %s jobs", period_id, cancelled)
    return {"status": "stopped", "period_id": period_id, "cancelled_jobs": cancelled}


@router.delete("/{entity_id}/{period_id}")
async def delete_monitoring_period(
    entity_id: str,
    period_id: str,
    cascade: bool = True,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Delete a monitoring period. Soft-deletes in PostgreSQL.

    With cascade=true (default): also deletes associated jobs (soft-delete),
    EOProduct entities from Orion-LD, and raster files from MinIO.
    """
    tenant_id = current_user["tenant_id"]
    period = (
        db.query(VegetationMonitoringPeriod)
        .filter(
            VegetationMonitoringPeriod.id == period_id,
            VegetationMonitoringPeriod.tenant_id == tenant_id,
            VegetationMonitoringPeriod.entity_id == entity_id,
            VegetationMonitoringPeriod.deleted_at.is_(None),
        )
        .first()
    )
    if not period:
        return {"deleted": True, "id": period_id}

    deleted_jobs = 0
    deleted_rasters = 0

    if cascade:
        from app.models.jobs import VegetationJob
        from app.services.fiware_integration import delete_eo_product
        import os
        from app.services.storage import create_storage_service

        jobs = (
            db.query(VegetationJob)
            .filter(
                VegetationJob.crop_season_id == period_id,
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
                    parts = raster_url.replace("s3://", "").split("/", 1)
                    bucket = parts[0]
                    key = parts[1] if len(parts) > 1 else ""
                    from app.services.storage import create_storage_service
                    storage = create_storage_service(
                        storage_type=os.getenv('STORAGE_TYPE', 's3'),
                        default_bucket=bucket
                    )
                    storage.delete_file(key, bucket)
                    deleted_rasters += 1
                except Exception as e:
                    logger.warning("Failed to delete raster %s: %s", raster_url, e)

    period.deleted_at = datetime.now(timezone.utc)
    period.is_active = False
    period.monitoring_enabled = False
    db.commit()

    logger.info("Deleted period %s: %s jobs, %s rasters", period_id, deleted_jobs, deleted_rasters)
    return {
        "deleted": True,
        "id": period_id,
        "cascade": cascade,
        "deleted_jobs": deleted_jobs,
        "deleted_rasters": deleted_rasters,
    }
