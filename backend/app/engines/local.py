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

        Sync DB operations are offloaded to a thread pool via asyncio.to_thread
        to avoid blocking the FastAPI event loop.
        """
        from app.database import SessionLocal
        from app.models import VegetationJob

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

        def _create_download_job() -> str:
            db = SessionLocal()
            try:
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
                return str(download_job.id)
            finally:
                db.close()

        job_id = await asyncio.to_thread(_create_download_job)

        from app.tasks.download_tasks import download_sentinel2_scene
        download_sentinel2_scene.delay(job_id, tenant_id, job_params)

        # Poll for completion, then read scene_id from the download job result
        await self._poll_job_completion(job_id)

        def _read_scene_id() -> str | None:
            db = SessionLocal()
            try:
                dl_job = db.query(VegetationJob).get(job_id)
                if dl_job and dl_job.result:
                    return dl_job.result.get("scene_id")
                return None
            finally:
                db.close()

        scene_id = await asyncio.to_thread(_read_scene_id)
        if not scene_id:
            raise RuntimeError(f"Download job {job_id} completed but returned no scene_id")

        # Dispatch calculate_index tasks
        results: list[IndexResult] = []
        for idx_type in index_types:
            def _create_calc_job() -> tuple[str, str]:
                db = SessionLocal()
                try:
                    calc_job = VegetationJob(
                        tenant_id=tenant_id,
                        job_type="calculate_index",
                        entity_id=parcel_id,
                        entity_type="AgriParcel",
                        parameters={
                            "scene_id": scene_id,
                            "index_type": idx_type,
                        },
                        status="pending",
                    )
                    db.add(calc_job)
                    db.commit()
                    return str(calc_job.id), str(calc_job.id)
                finally:
                    db.close()

            calc_job_id, _ = await asyncio.to_thread(_create_calc_job)

            from app.tasks.processing_tasks import calculate_vegetation_index
            calculate_vegetation_index.delay(
                calc_job_id, tenant_id,
                scene_id, idx_type,
            )

            await self._poll_job_completion(calc_job_id)

            def _read_job_result() -> dict:
                db = SessionLocal()
                try:
                    calc_job = db.query(VegetationJob).get(calc_job_id)
                    return calc_job.result or {} if calc_job else {}
                finally:
                    db.close()

            job_result = await asyncio.to_thread(_read_job_result)
            results.append(self._job_result_to_index_result(idx_type, job_result))

        return results

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
        """Check if Celery broker is reachable (offloaded to thread)."""
        try:
            await asyncio.to_thread(self._celery.control.ping, timeout=2)
            return EngineHealth(status="ok")
        except Exception as e:
            return EngineHealth(
                status="degraded",
                reason=f"Celery broker unreachable: {e}",
            )

    async def _poll_job_completion(self, job_id_str: str, timeout: int = POLL_TIMEOUT_SECONDS):
        """Poll VegetationJob status until completed or timeout.

        Offloads sync DB queries to a thread pool via asyncio.to_thread
        to avoid blocking the event loop.
        """
        from uuid import UUID
        from app.database import SessionLocal
        from app.models import VegetationJob

        start = datetime.now(timezone.utc)
        while True:
            def _check_status() -> str | None:
                db = SessionLocal()
                try:
                    job = db.query(VegetationJob).get(UUID(job_id_str))
                    if not job:
                        return None
                    return job.status
                finally:
                    db.close()

            status = await asyncio.to_thread(_check_status)
            if status is None:
                raise RuntimeError(f"Job {job_id_str} vanished")
            if status == "completed":
                return
            if status in ("failed", "cancelled"):
                raise RuntimeError(f"Job {job_id_str} {status}")

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
