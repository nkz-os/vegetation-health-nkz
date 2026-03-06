# backend/app/api/tiles.py
from fastapi import APIRouter, HTTPException, Depends, Response
from rio_tiler.io import COGReader
from rio_tiler.profiles import img_profiles
from app.middleware.auth import require_auth
from app.services.storage import generate_tenant_bucket_name
import os
import rasterio

router = APIRouter(prefix="/api/vegetation/tiles", tags=["tiles"])

@router.get("/{job_id}/{z}/{x}/{y}.png")
async def get_tile(
    job_id: str, z: int, x: int, y: int,
    current_user: dict = Depends(require_auth)
):
    """
    Renderiza un tile XYZ desde un COG almacenado en MinIO (S3 compatible).
    """
    tenant_id = current_user['tenant_id']
    bucket = generate_tenant_bucket_name(tenant_id)
    cog_path = f"{job_id}/result.tif"
    
    # URL interna S3 para MinIO en K8s
    s3_url = f"s3://{bucket}/{cog_path}"
    
    # GDAL Configuration for S3 access without modifying global env
    # rio-tiler uses rasterio.Env context manager for thread-safe access
    s3_endpoint = os.getenv("S3_ENDPOINT_URL", "http://minio-service:9000")
    gdal_config = {
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "AWS_S3_ENDPOINT": s3_endpoint.replace("http://", "").replace("https://", ""),
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "AWS_HTTPS": "FALSE" if "http://" in s3_endpoint else "TRUE"
    }
    
    try:
        with rasterio.Env(**gdal_config):
            with COGReader(s3_url) as cog:
                # Read tile and rescale from NDVI range (-1.0 to 1.0) to (0, 255)
                # Important: Rescale maps -1..1 to 0..255 for the PNG renderer
                img = cog.tile(x, y, z)
                
                # Render using a standard NDVI colormap ('rdylgn')
                content = img.render(
                    img_profiles.get("png"),
                    colormap="rdylgn",
                    rescale=[(-1, 1)]
                )
                
                return Response(content, media_type="image/png")
                
    except Exception as e:
        # Halt and Log as per Circuit Breaker/Agent directives
        import logging
        logging.error(f"Tile rendering failed for {s3_url}: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail=f"Tile rendering failed: {str(e)}"
        )
