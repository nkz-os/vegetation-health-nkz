"""Copernicus raster materialization: Sentinel Hub Process API → COG → MinIO.

Provides `ensure_copernicus_raster(db, job)` — idempotent, advisory-locked
generation of a cloud-optimized GeoTIFF from Copernicus (SH Process API) for
a calculate_index job whose result carries raster_pending=true.
"""

import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path

from shapely.geometry import shape as _shape

from app.engines.selector import _resolve_credentials
from app.services.evalscripts import build_index_float
from app.services.sentinel_hub_client import SentinelHubClient
from app.services.storage import create_storage_service, generate_tenant_bucket_name

logger = logging.getLogger(__name__)

_TARGET_RES = 0.0001      # ~10 m in degrees
_MAX_SIDE_PX = 2048       # Process API safe cap (< ~2500)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _dynamic_res(bbox):
    minx, miny, maxx, maxy = bbox
    w = max(abs(maxx - minx), 1e-9)
    h = max(abs(maxy - miny), 1e-9)
    px_w = w / _TARGET_RES
    px_h = h / _TARGET_RES
    largest = max(px_w, px_h)
    if largest <= _MAX_SIDE_PX:
        return _TARGET_RES, _TARGET_RES
    factor = largest / _MAX_SIDE_PX
    return _TARGET_RES * factor, _TARGET_RES * factor


def _lock_for(job_id: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(job_id)
        if lk is None:
            lk = threading.Lock()
            _locks[job_id] = lk
        return lk


def _process_and_cog(tenant_id, geometry, index_type, date_str, resx, resy) -> bytes:
    """Fetch FLOAT32 GeoTIFF from SH Process API and return COG bytes."""
    cid, csec = _resolve_credentials(tenant_id)
    if not (cid and csec):
        raise RuntimeError("No Copernicus credentials")
    evalscript = build_index_float(index_type)

    async def _fetch():
        client = SentinelHubClient(cid, csec)
        return await client.process_raster(geometry, evalscript, date_str, resx, resy)

    tiff = asyncio.run(_fetch())

    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "raw.tif"
        cog = Path(td) / "cog.tif"
        raw.write_bytes(tiff)
        try:
            from rio_cogeo.cogeo import cog_translate
            from rio_cogeo.profiles import cog_profiles
            cog_translate(str(raw), str(cog), cog_profiles.get("deflate"), in_memory=True, quiet=True)
            return cog.read_bytes()
        except Exception as e:
            logger.warning("COG conversion failed (%s); using raw GeoTIFF", e)
            return raw.read_bytes()


def _upload(cog_bytes: bytes, bucket: str, remote_path: str):
    storage = create_storage_service(
        storage_type=os.getenv("STORAGE_TYPE", "s3"),
        default_bucket=bucket,
    )
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=True) as tmp:
        tmp.write(cog_bytes)
        tmp.flush()
        storage.upload_file(tmp.name, remote_path, bucket)


def ensure_copernicus_raster(db, job) -> str | None:
    result = job.result or {}
    if result.get("raster_path"):
        return result["raster_path"]

    lock = _lock_for(str(job.id))
    with lock:
        db.refresh(job)  # another worker may have finished while we waited
        result = job.result or {}
        if result.get("raster_path"):
            return result["raster_path"]

        index_type = result.get("index_type")
        date_str = result.get("sensing_date")
        geometry = result.get("geometry")
        if not (index_type and date_str and geometry):
            logger.warning("Copernicus job %s missing index/date/geometry — cannot materialize", job.id)
            return None

        bbox = list(_shape(geometry).bounds)
        resx, resy = _dynamic_res(bbox)
        bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(job.tenant_id)
        remote_path = f"{job.tenant_id}/entities/{job.entity_id or 'unknown'}/copernicus/{date_str}/{index_type}.tif"

        try:
            cog_bytes = _process_and_cog(job.tenant_id, geometry, index_type, date_str, resx, resy)
            _upload(cog_bytes, bucket, remote_path)
        except Exception as e:
            logger.warning("Copernicus raster materialization failed for job %s: %s", job.id, e)
            return None

        new_result = dict(result)
        new_result["raster_path"] = remote_path
        new_result["raster_pending"] = False
        new_result["raster_resolution_deg"] = resx
        job.result = new_result
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(job, "result")
        db.commit()
        return remote_path
