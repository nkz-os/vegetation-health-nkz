# backend/app/api/tiles.py
from fastapi import APIRouter, HTTPException, Depends, Request, Response, Query
from rio_tiler.io import Reader
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.colormap import cmap as colormap_handler
from rasterio.warp import transform_bounds
from sqlalchemy.orm import Session
from uuid import UUID
from typing import Optional
from app.database import get_db_session
from app.models import VegetationJob
from app.middleware.auth import get_tenant_id, require_auth
from app.services.storage import generate_tenant_bucket_name
from app.services.tile_auth import validate_tile_token
import os
import logging

router = APIRouter(prefix="/api/vegetation/tiles", tags=["tiles"])
logger = logging.getLogger(__name__)

# Colormap and rescale ranges per index type.
INDEX_RENDER_CONFIG = {
    'NDVI': {'colormap_name': 'rdylgn', 'rescale': (-0.2, 0.9)},
    'EVI': {'colormap_name': 'rdylgn', 'rescale': (-0.2, 0.8)},
    'SAVI': {'colormap_name': 'rdylgn', 'rescale': (-0.2, 0.8)},
    'GNDVI': {'colormap_name': 'rdylgn', 'rescale': (-0.2, 0.8)},
    'NDRE': {'colormap_name': 'rdylgn', 'rescale': (-0.1, 0.6)},
    'NDWI': {'colormap_name': 'rdbu', 'rescale': (-1, 1)},
    'NDMI': {'colormap_name': 'rdbu', 'rescale': (-1, 1)},
    'LAI': {'colormap_name': 'greens', 'rescale': (0, 6)},
    'CIre': {'colormap_name': 'greens', 'rescale': (0, 5)},
    'CUSTOM': {'colormap_name': 'rdylgn', 'rescale': (-1, 1)},
}
DEFAULT_RENDER = {'colormap_name': 'rdylgn', 'rescale': (-1, 1)}

# Default COG bucket
_default_bucket = os.getenv("VEGETATION_COG_BUCKET", "vegetation-prime-global")

# Set GDAL/S3 env vars once at module load
_s3_endpoint = os.getenv("S3_ENDPOINT_URL", "http://minio-service:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", os.getenv("AWS_ACCESS_KEY_ID", ""))
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", os.getenv("AWS_SECRET_ACCESS_KEY", ""))
os.environ.setdefault("AWS_S3_ENDPOINT", _s3_endpoint.replace("http://", "").replace("https://", ""))
os.environ.setdefault("AWS_VIRTUAL_HOSTING", "FALSE")
os.environ.setdefault("AWS_HTTPS", "NO" if "http://" in _s3_endpoint else "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")


def _render_tile(s3_url: str, z: int, x: int, y: int, index_type: str) -> Response:
    """Shared tile rendering logic.

    Handles nodata / NaN pixels as fully transparent so areas outside
    the parcel polygon are invisible on the map.
    """
    import numpy as np

    render = INDEX_RENDER_CONFIG.get(index_type.upper(), DEFAULT_RENDER)
    cm = colormap_handler.get(render['colormap_name'])

    with Reader(s3_url) as cog:
        img = cog.tile(x, y, z)

        # Mask NaN pixels as transparent (rio-tiler v7: mask is read-only,
        # must mask the underlying MaskedArray directly)
        arr = img.array  # np.ma.MaskedArray
        if arr.dtype.kind == 'f':
            nan_pixels = np.isnan(arr.data[0])
            if nan_pixels.any():
                arr[:, nan_pixels] = np.ma.masked

        vmin, vmax = render['rescale']
        img.rescale(in_range=((vmin, vmax),))

        content = img.render(img_format="PNG", colormap=cm)
        return Response(
            content,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )


def _get_wgs84_bounds(s3_url: str) -> dict:
    """Read COG bounds and return in WGS84."""
    with Reader(s3_url) as cog:
        native_bounds = cog.bounds
        crs = cog.dataset.crs
        if crs and str(crs) != 'EPSG:4326':
            bounds = transform_bounds(crs, 'EPSG:4326', *native_bounds)
        else:
            bounds = native_bounds
        return {
            "bounds": list(bounds),
            "minzoom": getattr(cog, 'minzoom', 10),
            "maxzoom": getattr(cog, 'maxzoom', 18),
        }


# ── Bounds endpoints ────────────────────────────────────────────────

@router.get("/bounds")
async def get_bounds_by_path(
    raster_path: str = Query(..., description="Raster path inside the COG bucket"),
    index: str = Query("NDVI"),
):
    """Return WGS84 bounds for a raster_path (scene-based tiles)."""
    bucket = _default_bucket
    s3_url = f"s3://{bucket}/{raster_path}"
    try:
        return _get_wgs84_bounds(s3_url)
    except Exception as e:
        logger.error("Failed to read bounds for %s: %s", s3_url, str(e))
        raise HTTPException(status_code=500, detail=f"Failed to read COG bounds: {str(e)}")


@router.get("/{job_id}/bounds")
async def get_tile_bounds(
    job_id: str,
    db: Session = Depends(get_db_session),
):
    """Return the WGS84 bounding box of the COG for a job."""
    try:
        job = db.query(VegetationJob).filter(
            VegetationJob.id == UUID(job_id),
        ).first()
    except (ValueError, Exception):
        job = None

    if not job or not job.result:
        raise HTTPException(status_code=404, detail="Job not found or has no result")

    raster_path = job.result.get('raster_path')
    if not raster_path:
        raise HTTPException(status_code=404, detail="Job has no raster output")

    bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(job.tenant_id)
    s3_url = f"s3://{bucket}/{raster_path}"

    try:
        return _get_wgs84_bounds(s3_url)
    except Exception as e:
        logger.error("Failed to read bounds for %s: %s", s3_url, str(e))
        raise HTTPException(status_code=500, detail=f"Failed to read COG bounds: {str(e)}")


# ── Tile rendering endpoints ────────────────────────────────────────

@router.get("/render/{z}/{x}/{y}.png")
async def get_tile_by_path(
    z: int, x: int, y: int,
    raster_path: str = Query(..., description="Raster path inside the COG bucket"),
    index: str = Query("NDVI"),
    tenant_id: str = Depends(get_tenant_id),
):
    """Render a tile directly from a raster_path (internal use, requires auth headers)."""
    bucket = _default_bucket
    s3_url = f"s3://{bucket}/{raster_path}"

    try:
        return _render_tile(s3_url, z, x, y, index)
    except TileOutsideBounds:
        return Response(status_code=204)
    except Exception as e:
        logger.error("Tile rendering failed for %s: %s", s3_url, str(e))
        raise HTTPException(status_code=500, detail=f"Tile rendering failed: {str(e)}")


@router.get("/{job_id}/{z}/{x}/{y}.png")
async def get_tile(
    job_id: str, z: int, x: int, y: int,
    index: str = "NDVI",
    token: Optional[str] = Query(None, description="Tile access token"),
    db: Session = Depends(get_db_session),
):
    """Render an XYZ tile from a COG stored in MinIO.

    Requires a time-limited HMAC token obtained from the job response.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Tile access token required")

    try:
        job = db.query(VegetationJob).filter(
            VegetationJob.id == UUID(job_id),
        ).first()
    except (ValueError, Exception):
        job = None

    if not job or not job.result:
        raise HTTPException(status_code=404, detail="Job not found or has no result")

    if not validate_tile_token(token, job_id, job.tenant_id):
        raise HTTPException(status_code=401, detail="Invalid or expired tile token")

    raster_path = job.result.get('raster_path')
    if not raster_path:
        raise HTTPException(status_code=404, detail="Job has no raster output")

    bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(job.tenant_id)
    s3_url = f"s3://{bucket}/{raster_path}"
    index_type = (index or job.result.get('index_type') or 'NDVI').upper()

    try:
        return _render_tile(s3_url, z, x, y, index_type)
    except TileOutsideBounds:
        return Response(status_code=204)
    except Exception as e:
        logger.error("Tile rendering failed for %s: %s", s3_url, str(e))
        raise HTTPException(status_code=500, detail=f"Tile rendering failed: {str(e)}")


# ── Sentinel Hub tile proxy (Phase 3) ──────────────────────────────

@router.get("/sentinel-hub/{index_type}/{z}/{x}/{y}.png")
async def get_sentinel_hub_tile(
    request: Request,
    index_type: str,
    z: int, x: int, y: int,
    date_str: Optional[str] = Query(None, description="ISO date YYYY-MM-DD, or latest"),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_session),
):
    """Proxy tile from Sentinel Hub Process API with MinIO cache.

    Requires auth (JWT via gateway). Cache-first with tenant isolation.
    Falls back to local COG if Sentinel Hub is unavailable.
    """
    from app.services.tile_cache import get_cached_tile, put_cached_tile

    tenant_id = current_user["tenant_id"]
    index_type = index_type.upper()
    engine_selector = request.app.state.engine_selector

    # 1. Check MinIO cache (tenant-scoped)
    cached = get_cached_tile(tenant_id, index_type, z, x, y, date_str)
    if cached:
        return Response(cached, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600",
                                 "X-Tile-Source": "cache"})

    # 2. Call engine (Sentinel Hub → local fallback on failure)
    try:
        tile_bytes = await engine_selector.get_tile(
            tenant_id=tenant_id,
            index_type=index_type,
            z=z, x=x, y=y,
            date_str=date_str,
        )
        put_cached_tile(tenant_id, index_type, z, x, y, tile_bytes, date_str)
        return Response(tile_bytes, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600",
                                 "X-Tile-Source": "sentinel-hub"})
    except NotImplementedError:
        pass  # Local engine doesn't support tiles yet — fall through to COG
    except Exception as e:
        logger.warning("Sentinel Hub tile failed: %s — trying local COG", e)

    # 3. Local COG fallback
    from app.models import VegetationJob

    job = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            VegetationJob.result["index_type"].astext == index_type,
            VegetationJob.result["raster_path"].astext.isnot(None),
        )
        .order_by(VegetationJob.completed_at.desc())
        .first()
    )

    if not job or not job.result:
        raise HTTPException(status_code=404, detail="No raster available for this index")

    raster_path = job.result.get("raster_path")
    bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(job.tenant_id)
    s3_url = f"s3://{bucket}/{raster_path}"

    try:
        return _render_tile(s3_url, z, x, y, index_type)
    except TileOutsideBounds:
        return Response(status_code=204)
    except Exception as e:
        logger.error("Local tile fallback failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Tile rendering failed: {str(e)}")
