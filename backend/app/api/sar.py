"""
SAR (Sentinel-1 GRD) manual analysis endpoint.
Allows users to trigger SAR backscatter calculation for a parcel on demand.
"""

import logging
import os
import uuid
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from shapely.geometry import shape as shp
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationJob
from app.services.copernicus_client import CopernicusDataSpaceClient
from app.services.platform_credentials import get_copernicus_credentials_with_fallback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/sar", tags=["sar"])


class SarAnalyzeRequest(BaseModel):
    entity_id: str = Field(..., description="AgriParcel ID")
    start_date: Optional[str] = Field(None, description="Start date (ISO format). Default: 30 days ago")
    end_date: Optional[str] = Field(None, description="End date (ISO format). Default: today")
    max_scenes: int = Field(5, ge=1, le=20, description="Max Sentinel-1 GRD scenes to process")


@router.post("/analyze", status_code=status.HTTP_201_CREATED)
async def analyze_sar(
    request: SarAnalyzeRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Search Sentinel-1 GRD scenes for a parcel and trigger SAR backscatter
    calculation.

    Creates download_sar jobs per scene found. Each downloads VV+VH bands
    and produces calculate_index job records (index_type= SAR-VV / SAR-VH)
    that appear in the frontend's available_indices.

    Returns the list of job_ids created so the UI can track progress.
    """
    tenant_id = current_user["tenant_id"]
    entity_id = request.entity_id

    # Date defaults
    end = date.fromisoformat(request.end_date) if request.end_date else date.today()
    start = date.fromisoformat(request.start_date) if request.start_date else (end - timedelta(days=30))
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must be >= start_date")

    # Get parcel geometry from Orion-LD
    orion_url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
    headers = {"Accept": "application/json", "NGSILD-Tenant": tenant_id}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{orion_url}/ngsi-ld/v1/entities/{entity_id}", headers=headers
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=404,
                detail=f"Parcel {entity_id} not found in context broker",
            )
        entity_data = resp.json()
        loc = entity_data.get("location", {})
        geom = loc.get("value") or loc
        if not geom or "coordinates" not in geom:
            raise HTTPException(
                status_code=422,
                detail="Parcel has no location geometry",
            )
        geom_obj = shp(geom)
        bbox = list(geom_obj.bounds)
        # STAC API requires Polygon, not MultiPolygon
        if geom_obj.geom_type == "MultiPolygon":
            largest = max(geom_obj.geoms, key=lambda g: g.area)
            intersects = largest.__geo_interface__
        else:
            intersects = geom_obj.__geo_interface__

    # Search S1 scenes via Copernicus STAC
    creds = get_copernicus_credentials_with_fallback()
    copernicus = CopernicusDataSpaceClient()
    if creds:
        copernicus.set_credentials(creds["client_id"], creds["client_secret"])

    try:
        s1_scenes = copernicus.search_s1_scenes(
            intersects=intersects,
            start_date=start,
            end_date=end,
            limit=request.max_scenes,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Copernicus Sentinel-1 search failed: {e}",
        )

    if not s1_scenes:
        return {
            "entity_id": entity_id,
            "job_ids": [],
            "message": f"No Sentinel-1 scenes found between {start.isoformat()} and {end.isoformat()}",
            "scenes_found": 0,
        }

    # Create and dispatch download_sar jobs
    from app.tasks.sar_tasks import download_sentinel1_scene

    job_ids = []
    for s1_scene in s1_scenes:
        s1_params = {
            "scene_id": s1_scene["id"],
            "bounds": intersects,
            "bbox": bbox,
            "sensing_date": s1_scene["sensing_date"],
            "entity_id": entity_id,
        }
        job = VegetationJob(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            entity_id=entity_id,
            job_type="download_sar",
            status="pending",
            parameters=s1_params,
        )
        db.add(job)
        db.commit()

        try:
            async_result = download_sentinel1_scene.delay(
                job_id=str(job.id),
                tenant_id=tenant_id,
                parameters=s1_params,
            )
            job.celery_task_id = async_result.id
            db.commit()
            job_ids.append(str(job.id))
        except Exception as exc:
            logger.exception("Failed to enqueue SAR task for job %s", job.id)
            job.status = "failed"
            job.error_message = f"Enqueue failed: {exc}"
            db.commit()

    logger.info(
        "SAR analyze: %d scene(s) dispatched for %s (tenant=%s)",
        len(job_ids), entity_id, tenant_id,
    )
    return {
        "entity_id": entity_id,
        "job_ids": job_ids,
        "message": f"{len(job_ids)} SAR analysis job(s) started",
        "scenes_found": len(s1_scenes),
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
    }
