"""Local download→calc leg of a season analysis.

Shared by the analyze HTTP handler and the Copernicus task's fallback: searches
Sentinel-2 scenes in the date range, creates one download job per dekadal
window and enqueues it; optionally triggers Sentinel-1 SAR downloads.
"""

import logging
from datetime import date as date_type
from typing import Any

from app.models import VegetationJob

logger = logging.getLogger(__name__)


class AnalyzeDispatchError(Exception):
    """A local-pipeline failure the HTTP layer maps to `status_code`."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def dispatch_local_pipeline(
    db,
    *,
    tenant_id: str,
    entity_id: str,
    user_id: str | None,
    geometry: dict[str, Any],
    bbox: list[float],
    indices: list[str],
    custom_formula_specs: list[dict[str, Any]],
    start_date_iso: str,
    end_date_iso: str,
    local_cloud_threshold: float | None,
    crop_season_id: str | None,
    season_uuid,
    include_sar: bool,
) -> dict[str, Any]:
    """Enqueue the local pipeline. Returns {job_ids, windows, scenes_found}."""
    from shapely.geometry import shape as shp_fn

    from app.services.copernicus_client import CopernicusDataSpaceClient
    from app.services.platform_credentials import (
        get_copernicus_credentials_with_fallback,
    )
    from app.services.temporal_utils import group_scenes_into_windows
    from app.tasks import download_sentinel2_scene

    creds = get_copernicus_credentials_with_fallback()
    if not creds:
        raise AnalyzeDispatchError(503, "Copernicus credentials not configured")
    copernicus = CopernicusDataSpaceClient()
    copernicus.set_credentials(creds["client_id"], creds["client_secret"])

    geom_obj = shp_fn(geometry)
    intersects_geojson = geometry
    if geom_obj.geom_type == "MultiPolygon":
        largest = max(geom_obj.geoms, key=lambda g: g.area)
        intersects_geojson = largest.__geo_interface__

    job_ids: list[str] = []
    windows = 0
    scenes_found = 0
    # Sentinel-2 downloads only feed optical indices and custom formulas; a
    # SAR-only request must not download (and discard) optical scenes.
    if indices or custom_formula_specs:
        job_ids, windows, scenes_found = _dispatch_optical(
            db,
            copernicus=copernicus,
            download_task=download_sentinel2_scene,
            group_windows=group_scenes_into_windows,
            tenant_id=tenant_id,
            entity_id=entity_id,
            user_id=user_id,
            geometry=geometry,
            intersects_geojson=intersects_geojson,
            bbox=bbox,
            indices=indices,
            custom_formula_specs=custom_formula_specs,
            start_date_iso=start_date_iso,
            end_date_iso=end_date_iso,
            local_cloud_threshold=local_cloud_threshold,
            crop_season_id=crop_season_id,
            season_uuid=season_uuid,
        )

    if include_sar:
        job_ids += _dispatch_sar(
            db,
            copernicus=copernicus,
            tenant_id=tenant_id,
            entity_id=entity_id,
            intersects_geojson=intersects_geojson,
            bbox=bbox,
            start_date_iso=start_date_iso,
            end_date_iso=end_date_iso,
            crop_season_id=crop_season_id,
            season_uuid=season_uuid,
        )

    logger.info(
        "Multi-scene analysis: %d windows dispatched for entity %s (scenes: %d, indices: %s, sar: %s, season: %s)",
        windows, entity_id, scenes_found, indices, include_sar, crop_season_id,
    )
    return {"job_ids": job_ids, "windows": windows, "scenes_found": scenes_found}


def _dispatch_optical(
    db,
    *,
    copernicus,
    download_task,
    group_windows,
    tenant_id: str,
    entity_id: str,
    user_id: str | None,
    geometry: dict[str, Any],
    intersects_geojson: dict[str, Any],
    bbox: list[float],
    indices: list[str],
    custom_formula_specs: list[dict[str, Any]],
    start_date_iso: str,
    end_date_iso: str,
    local_cloud_threshold: float | None,
    crop_season_id: str | None,
    season_uuid,
) -> tuple[list[str], int, int]:
    """Search Sentinel-2 scenes and enqueue one download job per window.

    Returns (job_ids, windows, scenes_found).
    """
    all_scenes = copernicus.search_scenes(
        intersects=intersects_geojson,
        start_date=date_type.fromisoformat(start_date_iso),
        end_date=date_type.fromisoformat(end_date_iso),
        cloud_cover_lte=50,
        limit=50,
    )

    if not all_scenes:
        raise AnalyzeDispatchError(404, "No scenes found in the selected date range")

    windows = group_windows(all_scenes, date_key="sensing_date")

    # Stage 1: build all VegetationJob rows in memory and commit them in a
    # single transaction. Avoids the per-iteration commit pattern that left
    # the caller with a partial set of rows + 503 when window N failed.
    pending_jobs: list[VegetationJob] = []
    for window in windows:
        best = sorted(window["scenes"], key=lambda s: s.get("cloud_cover", 100))[0]

        job_parameters = {
            "scene_id": best["id"],
            "bbox": bbox,
            "bounds": geometry,
            "entity_id": entity_id,
            "cloud_coverage_threshold": 50,
            "calculate_indices": indices,
            "calculate_custom_formulas": custom_formula_specs,
        }
        if local_cloud_threshold is not None:
            job_parameters["local_cloud_threshold"] = float(local_cloud_threshold)
        # Propagate season binding to the worker so the child calculate_index
        # jobs it spawns inherit crop_season_id (else they orphan into legacy).
        if crop_season_id:
            job_parameters["crop_season_id"] = crop_season_id

        pending_jobs.append(
            VegetationJob(
                tenant_id=tenant_id,
                job_type="download",
                entity_id=entity_id,
                entity_type="AgriParcel",
                parameters=job_parameters,
                created_by=user_id,
                crop_season_id=season_uuid,
            )
        )

    db.add_all(pending_jobs)
    db.commit()
    for j in pending_jobs:
        db.refresh(j)

    # Stage 2: dispatch each Celery task. If any .delay() fails, mark every
    # already-enqueued job (and the rest) as failed in a single follow-up
    # commit so the response is consistent with the DB state.
    job_ids: list[str] = []
    enqueue_error: Exception | None = None
    for idx, job in enumerate(pending_jobs):
        try:
            async_result = download_task.delay(
                str(job.id), tenant_id, job.parameters
            )
            job.celery_task_id = async_result.id
            job_ids.append(str(job.id))
        except Exception as exc:
            logger.exception("Failed to enqueue download task for job %s", job.id)
            enqueue_error = exc
            for k in range(idx, len(pending_jobs)):
                pending_jobs[k].status = "failed"
                pending_jobs[k].error_message = (
                    f"Could not enqueue Celery task: {exc}"
                )
            break
    db.commit()

    if enqueue_error is not None:
        raise AnalyzeDispatchError(
            503, "Job queue unavailable, please retry shortly."
        ) from enqueue_error

    return job_ids, len(windows), len(all_scenes)


def _dispatch_sar(
    db,
    *,
    copernicus,
    tenant_id: str,
    entity_id: str,
    intersects_geojson: dict[str, Any],
    bbox: list[float],
    start_date_iso: str,
    end_date_iso: str,
    crop_season_id: str | None,
    season_uuid,
) -> list[str]:
    """Trigger Sentinel-1 downloads; returns the enqueued job ids.

    Non-fatal: failures are logged only.
    """
    job_ids: list[str] = []
    try:
        from app.tasks.sar_tasks import download_sentinel1_scene

        s1_scenes = copernicus.search_s1_scenes(
            intersects=intersects_geojson,
            start_date=date_type.fromisoformat(start_date_iso),
            end_date=date_type.fromisoformat(end_date_iso),
            limit=3,
        )

        for s1_scene in s1_scenes:
            s1_params = {
                "scene_id": s1_scene["id"],
                "bounds": intersects_geojson,
                "bbox": bbox,
                "sensing_date": s1_scene["sensing_date"],
                "entity_id": entity_id,
            }
            if crop_season_id:
                s1_params["crop_season_id"] = crop_season_id
            sar_job = VegetationJob(
                tenant_id=tenant_id,
                entity_id=entity_id,
                job_type="download_sar",
                status="pending",
                parameters=s1_params,
                crop_season_id=season_uuid,
            )
            db.add(sar_job)
            db.commit()
            db.refresh(sar_job)

            try:
                download_sentinel1_scene.delay(
                    job_id=str(sar_job.id),
                    tenant_id=tenant_id,
                    parameters=s1_params,
                )
                job_ids.append(str(sar_job.id))
            except Exception as enq_exc:
                sar_job.status = "failed"
                sar_job.error_message = f"SAR enqueue failed: {enq_exc}"
                db.commit()
    except Exception as e:
        logger.warning("SAR trigger failed (non-fatal) for %s: %s", entity_id, e)
    return job_ids


