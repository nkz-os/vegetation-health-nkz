"""LocalProcessingEngine — sovereign fallback using existing Celery pipeline.

Wraps download_tasks.download_sentinel2_scene and
processing_tasks.calculate_vegetation_index behind the BaseVegetationEngine
interface WITHOUT modifying the existing task code.
"""

import asyncio
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from celery import Celery

from .base import BaseVegetationEngine, IndexResult, EngineHealth

logger = logging.getLogger(__name__)

CELERY_BROKER = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")

# Default poll config — values chosen to be patient enough for Sentinel-2
# download (~2-5 min) plus processing (~30-60s)
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 600  # 10 minutes max wait


class LocalProcessingEngine(BaseVegetationEngine):
    """Sovereign engine wrapping existing Celery download + process pipeline.

    This engine does NOT own any processing logic — it delegates entirely
    to the existing Celery tasks (download_tasks.py, processing_tasks.py)
    and polls for completion. The goal is to provide a consistent interface
    without touching the battle-tested processing code.
    """

    def __init__(self):
        self._celery = Celery(broker=CELERY_BROKER)

    @property
    def engine_name(self) -> str:
        return "local_processing"

    async def compute_indices(
        self,
        tenant_id: str,
        parcel_id: str,
        parcel_geometry: dict,
        date_range: tuple[date, date],
        index_types: list[str],
        cloud_cover_max: float = 50.0,
    ) -> list[IndexResult]:
        """Dispatch download + calculate tasks and await results.

        For each index type, this downloads the scene and processes it.
        Results are aggregated into IndexResult objects.
        """
        from app.database import SessionLocal
        from app.models import VegetationJob

        # Extract bounding box from geometry for the download task
        bbox = _geometry_to_bbox(parcel_geometry)
        if not bbox:
            raise ValueError("Could not extract bbox from parcel geometry")

        job_params: dict[str, Any] = {
            "bounds": {"type": "Polygon", "coordinates": [[
                [bbox[0], bbox[1]], [bbox[2], bbox[1]],
                [bbox[2], bbox[3]], [bbox[0], bbox[3]],
                [bbox[0], bbox[1]],
            ]]},
            "cloud_cover_max": cloud_cover_max,
            "start_date": date_range[0].isoformat(),
            "end_date": date_range[1].isoformat(),
            "index_types": index_types,
        }

        db = SessionLocal()
        try:
            # Create download job
            download_job = VegetationJob(
                tenant_id=tenant_id,
                job_type="download",
                entity_id=parcel_id,
                entity_type="AgriParcel",
                parameters=job_params,
                status="pending",
            )
            db.add(download_job)
            db.commit()
            job_id = str(download_job.id)

            # Dispatch download task
            from app.tasks.download_tasks import download_sentinel2_scene
            download_sentinel2_scene.delay(job_id, tenant_id, job_params)

            # Poll for completion
            await self._poll_job_completion(db, download_job.id, job_id)

            # After download completes, dispatch calculate_index tasks
            results: list[IndexResult] = []
            for idx_type in index_types:
                calc_job = VegetationJob(
                    tenant_id=tenant_id,
                    job_type="calculate_index",
                    entity_id=parcel_id,
                    entity_type="AgriParcel",
                    parameters={
                        "scene_id": job_params.get("scene_id"),
                        "index_type": idx_type,
                    },
                    status="pending",
                )
                db.add(calc_job)
                db.commit()

                from app.tasks.processing_tasks import calculate_vegetation_index
                calculate_vegetation_index.delay(
                    str(calc_job.id), tenant_id,
                    job_params.get("scene_id"), idx_type,
                )

                await self._poll_job_completion(db, calc_job.id, str(calc_job.id))

                # Read result from completed job
                db.refresh(calc_job)
                job_result = calc_job.result or {}
                results.append(self._job_result_to_index_result(
                    idx_type, job_result,
                ))

            return results
        finally:
            db.close()

    async def get_tile(
        self,
        tenant_id: str,
        index_type: str,
        z: int,
        x: int,
        y: int,
        date_str: str | None = None,
        color_ramp: str = "agronomic",
    ) -> bytes:
        """Render tile from locally stored COGs in MinIO.

        NOTE: Phase 3 (tile proxy adaptation) will wire the existing
        rio-tiler rendering from api/tiles.py through this method.
        For now, the tiles.py endpoint calls EngineSelector directly,
        and local tiles continue working through the existing code path.
        """
        raise NotImplementedError(
            "Local tile rendering through engine interface pending Phase 3. "
            "Existing api/tiles.py code path is unchanged and still functional."
        )

    async def health_check(self) -> EngineHealth:
        """Check if Celery broker is reachable."""
        try:
            self._celery.control.ping(timeout=2)
            return EngineHealth(status="ok")
        except Exception as e:
            return EngineHealth(
                status="degraded",
                reason=f"Celery broker unreachable: {e}",
            )

    async def _poll_job_completion(self, db, job_uuid, job_id_str, timeout=POLL_TIMEOUT_SECONDS):
        """Poll VegetationJob status until completed or timeout."""
        from app.models import VegetationJob

        start = datetime.now(timezone.utc)
        while True:
            db.refresh(db.query(VegetationJob).get(job_uuid))
            job = db.query(VegetationJob).get(job_uuid)
            if not job:
                raise RuntimeError(f"Job {job_id_str} vanished")

            if job.status == "completed":
                return
            if job.status in ("failed", "cancelled"):
                raise RuntimeError(
                    f"Job {job_id_str} {job.status}: {job.error_message}"
                )

            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            if elapsed > timeout:
                raise TimeoutError(f"Job {job_id_str} timed out after {timeout}s")

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    @staticmethod
    def _job_result_to_index_result(index_type: str, job_result: dict) -> IndexResult:
        """Convert a VegetationJob.result dict into an IndexResult."""
        stats = job_result.get("index_stats", {})
        try:
            sensing_date = date.fromisoformat(job_result.get("sensing_date", ""))
        except (ValueError, TypeError):
            sensing_date = date.today()

        return IndexResult(
            index_type=index_type,
            sensing_date=sensing_date,
            mean=float(stats.get("mean", 0)),
            std=float(stats.get("std_dev", 0)),
            min=float(stats.get("min", 0)),
            max=float(stats.get("max", 0)),
            p10=float(stats.get("p10", 0)),
            p90=float(stats.get("p90", 0)),
            valid_pixels=int(stats.get("valid_pixels", 0)),
            total_pixels=int(stats.get("total_pixels", 0)),
            data_fidelity="local_full",
        )


def _geometry_to_bbox(geometry: dict) -> list[float] | None:
    """Extract [minLon, minLat, maxLon, maxLat] from GeoJSON geometry.

    Handles Polygon and MultiPolygon types.
    """
    coords = geometry.get("coordinates", [])
    if not coords:
        return None

    if geometry.get("type") == "Polygon":
        rings = coords
    elif geometry.get("type") == "MultiPolygon":
        rings = [ring for poly in coords for ring in poly]
    else:
        return None

    all_points = [pt for ring in rings for pt in ring]
    if not all_points:
        return None

    lons = [p[0] for p in all_points]
    lats = [p[1] for p in all_points]
    return [min(lons), min(lats), max(lons), max(lats)]
