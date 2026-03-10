# backend/app/api/tiles.py
from fastapi import APIRouter, HTTPException, Depends, Response
from rio_tiler.io import COGReader
from rio_tiler.profiles import img_profiles
from sqlalchemy.orm import Session
from uuid import UUID
from app.middleware.auth import require_auth
from app.database import get_db_with_tenant
from app.models import VegetationJob
from app.services.storage import generate_tenant_bucket_name
import os
import logging
import rasterio

router = APIRouter(prefix="/api/vegetation/tiles", tags=["tiles"])
logger = logging.getLogger(__name__)

# Colormap and rescale ranges per index type
INDEX_RENDER_CONFIG = {
    'NDVI': {'colormap': 'rdylgn', 'rescale': (-0.2, 0.9)},
    'EVI': {'colormap': 'rdylgn', 'rescale': (-0.2, 0.8)},
    'SAVI': {'colormap': 'rdylgn', 'rescale': (-0.2, 0.8)},
    'GNDVI': {'colormap': 'rdylgn', 'rescale': (-0.2, 0.8)},
    'NDRE': {'colormap': 'rdylgn', 'rescale': (-0.1, 0.6)},
    'NDWI': {'colormap': 'rdbu', 'rescale': (-1, 1)},
    'NDMI': {'colormap': 'rdbu', 'rescale': (-1, 1)},
    'LAI': {'colormap': 'greens', 'rescale': (0, 6)},
    'CIre': {'colormap': 'greens', 'rescale': (0, 5)},
}
DEFAULT_RENDER = {'colormap': 'rdylgn', 'rescale': (-1, 1)}


@router.get("/{job_id}/{z}/{x}/{y}.png")
async def get_tile(
    job_id: str, z: int, x: int, y: int,
    index: str = "NDVI",
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Render an XYZ tile from a COG stored in MinIO (S3 compatible)."""
    tenant_id = current_user['tenant_id']

    # Look up the job to find the actual COG path
    try:
        job = db.query(VegetationJob).filter(
            VegetationJob.id == UUID(job_id),
            VegetationJob.tenant_id == tenant_id,
        ).first()
    except (ValueError, Exception):
        job = None

    if not job or not job.result:
        raise HTTPException(status_code=404, detail="Job not found or has no result")

    raster_path = job.result.get('raster_path')
    if not raster_path:
        raise HTTPException(status_code=404, detail="Job has no raster output")

    bucket = generate_tenant_bucket_name(tenant_id)
    s3_url = f"s3://{bucket}/{raster_path}"

    # GDAL Configuration for S3 access
    s3_endpoint = os.getenv("S3_ENDPOINT_URL", "http://minio-service:9000")
    gdal_config = {
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "AWS_S3_ENDPOINT": s3_endpoint.replace("http://", "").replace("https://", ""),
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "AWS_HTTPS": "FALSE" if "http://" in s3_endpoint else "TRUE",
    }

    # Get render config for this index type
    index_type = (job.result.get('index_type') or index or 'NDVI').upper()
    render = INDEX_RENDER_CONFIG.get(index_type, DEFAULT_RENDER)

    try:
        with rasterio.Env(**gdal_config):
            with COGReader(s3_url) as cog:
                img = cog.tile(x, y, z)
                content = img.render(
                    img_profiles.get("png"),
                    colormap=render['colormap'],
                    rescale=[render['rescale']],
                )
                return Response(
                    content,
                    media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"},
                )

    except Exception as e:
        logger.error("Tile rendering failed for %s: %s", s3_url, str(e))
        raise HTTPException(status_code=500, detail=f"Tile rendering failed: {str(e)}")
