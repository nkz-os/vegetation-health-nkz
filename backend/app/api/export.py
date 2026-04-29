"""
Export API — GeoJSON, Shapefile, CSV prescription maps and job result downloads.
"""
import csv
import io
import logging
import os
import tempfile
import uuid as uuid_mod
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models import VegetationJob, VegetationConfig
from app.services.export_service import exporter
from app.services.storage import create_storage_service, generate_tenant_bucket_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation", tags=["export"])


def _get_latest_zoning_job(db: Session, tenant_id: str, parcel_id: str) -> VegetationJob:
    """Get the latest completed VRA_ZONES job for a parcel."""
    job = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.entity_id == parcel_id,
            VegetationJob.job_type == "calculate_index",
            VegetationJob.status == "completed",
            text("result->>'index_type' = 'VRA_ZONES'"),
        )
        .order_by(VegetationJob.created_at.desc())
        .first()
    )
    return job


def _get_features_from_job(job: VegetationJob) -> List[dict]:
    """Extract GeoJSON features from a completed VRA_ZONES job result."""
    if not job or not job.result:
        return []
    geojson = job.result.get("geojson")
    if not geojson:
        return []
    return geojson.get("features", [])


# ── Prescription map export endpoints ──────────────────────────────


@router.get("/export/{parcel_id}/geojson")
async def export_geojson(
    parcel_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Export prescription zones as GeoJSON."""
    tenant_id = current_user["tenant_id"]
    job = _get_latest_zoning_job(db, tenant_id, parcel_id)
    features = _get_features_from_job(job)
    if not features:
        raise HTTPException(status_code=404, detail="No prescription data available for export")

    data = exporter.export_geojson(features)
    return Response(
        content=data,
        media_type="application/geo+json",
        headers={
            "Content-Disposition": f'attachment; filename="prescription_{parcel_id}.geojson"'
        },
    )


@router.get("/export/{parcel_id}/shapefile")
async def export_shapefile(
    parcel_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Export prescription zones as zipped Shapefile."""
    tenant_id = current_user["tenant_id"]
    job = _get_latest_zoning_job(db, tenant_id, parcel_id)
    features = _get_features_from_job(job)
    if not features:
        raise HTTPException(status_code=404, detail="No prescription data available for export")

    try:
        data = exporter.export_shapefile(features)
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Shapefile export requires fiona and shapely packages",
        )

    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="prescription_{parcel_id}.zip"'
        },
    )


@router.get("/export/{parcel_id}/csv")
async def export_csv(
    parcel_id: str,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Export prescription zones as CSV."""
    tenant_id = current_user["tenant_id"]
    job = _get_latest_zoning_job(db, tenant_id, parcel_id)
    features = _get_features_from_job(job)
    if not features:
        raise HTTPException(status_code=404, detail="No prescription data available for export")

    data = exporter.export_csv(features)
    return Response(
        content=data,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="prescription_{parcel_id}.csv"'
        },
    )


# ── Job result download endpoint ────────────────────────────────────


@router.get("/jobs/{job_id}/download")
async def download_job_result(
    job_id: str,
    format: str = Query(..., description="Output format: geotiff, png, or csv"),
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Download job result in specified format (geotiff, png, csv).

    For raster-based formats (geotiff, png), streams the COG from object storage.
    For CSV, extracts job statistics as a tabular file.
    """
    tenant_id = current_user["tenant_id"]
    try:
        job_uuid = uuid_mod.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job ID")

    job = (
        db.query(VegetationJob)
        .filter(
            VegetationJob.id == job_uuid,
            VegetationJob.tenant_id == tenant_id,
            VegetationJob.status == "completed",
        )
        .first()
    )

    if not job or not job.result:
        raise HTTPException(status_code=404, detail="Job not found or not completed")

    if format == "csv":
        stats = job.result.get("statistics", {})
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["metric", "value"])
        for key, value in stats.items():
            writer.writerow([key, value])
        return Response(
            content=output.getvalue().encode("utf-8"),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="job_{job_id}.csv"'
            },
        )

    if format in ("geotiff", "png"):
        raster_path = job.result.get("raster_path")
        if not raster_path:
            raise HTTPException(status_code=404, detail="No raster available for this job")

        config = (
            db.query(VegetationConfig)
            .filter(VegetationConfig.tenant_id == tenant_id)
            .first()
        )
        storage_type = config.storage_type if config else os.getenv("STORAGE_TYPE", "s3")
        bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(tenant_id)
        storage = create_storage_service(
            storage_type=storage_type,
            default_bucket=bucket,
        )

        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
            storage.download_file(raster_path, tmp.name, bucket)
            tmp_path = tmp.name

        try:
            if format == "geotiff":
                with open(tmp_path, "rb") as f:
                    data = f.read()
                return Response(
                    content=data,
                    media_type="image/tiff",
                    headers={
                        "Content-Disposition": f'attachment; filename="index_{job_id}.tiff"'
                    },
                )

            # PNG rendering with normalized colormap
            try:
                import numpy as np
                from PIL import Image
            except ImportError:
                raise HTTPException(
                    status_code=501,
                    detail="PNG export requires numpy and Pillow packages",
                )

            import rasterio

            with rasterio.open(tmp_path) as src:
                arr = src.read(1)

            arr = np.ma.masked_invalid(arr)
            if arr.count() > 0:
                vmin = np.nanpercentile(arr.filled(np.nan), 2)
                vmax = np.nanpercentile(arr.filled(np.nan), 98)
            else:
                vmin, vmax = -1, 1

            normalized = np.clip((arr.filled(vmin) - vmin) / (vmax - vmin + 1e-10), 0, 1)
            img_data = (normalized * 255).astype(np.uint8)
            img = Image.fromarray(img_data, "L")

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return Response(
                content=buf.read(),
                media_type="image/png",
                headers={
                    "Content-Disposition": f'attachment; filename="index_{job_id}.png"'
                },
            )
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")
