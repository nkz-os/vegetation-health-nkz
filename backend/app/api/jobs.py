# backend/app/api/jobs.py
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from uuid import UUID
from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationJob
from app.tasks import download_sentinel2_scene, calculate_vegetation_index
from app.services.limits import LimitsValidator
from app.services.usage_tracker import UsageTracker
from app.schemas import JobCreateRequest, JobResponse
import logging

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

@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, current_user: dict = Depends(require_auth), db: Session = Depends(get_db_with_tenant)):
    job = db.query(VegetationJob).filter(
        VegetationJob.id == job_id,
        VegetationJob.tenant_id == current_user['tenant_id']
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
