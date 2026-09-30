"""Celery task for the Copernicus leg of a season analysis.

The HTTP handler reserves quota, records a `copernicus_analyze` job and returns
immediately; this task computes and persists the indices off the request path.
On a genuine Copernicus failure the indices are routed to the local
download→calc pipeline, as the handler used to do inline.
"""

import asyncio
import logging
import traceback
import uuid
from datetime import date
from typing import Any

from app.celery_app import celery_app
from app.database import get_db_session
from app.models import VegetationJob

logger = logging.getLogger(__name__)

# One selector per worker process, like the API's app.state singleton: it keeps
# the engine degradation state across tasks.
_ENGINE_SELECTOR = None


def _engine_selector():
    global _ENGINE_SELECTOR
    if _ENGINE_SELECTOR is None:
        from app.engines.selector import EngineSelector
        _ENGINE_SELECTOR = EngineSelector()
    return _ENGINE_SELECTOR


@celery_app.task(
    bind=True,
    name="vegetation.analyze_copernicus",
    max_retries=0,
    soft_time_limit=900,
)
def analyze_copernicus(self, job_id: str, tenant_id: str, parameters: dict[str, Any]):
    """Run `persist_copernicus_indices` for the job, falling back to local.

    parameters: {indices, entity_id, geometry, bbox, start_date, end_date,
                 crop_season_id, local_cloud_threshold}
    """
    from app.services.copernicus_analyze import persist_copernicus_indices
    from app.services.local_analyze import dispatch_local_pipeline
    from app.services.satellite_quota import SatelliteQuota

    db = next(get_db_session())
    job = None
    try:
        job = db.query(VegetationJob).filter(VegetationJob.id == uuid.UUID(job_id)).first()
        if not job:
            logger.error("copernicus_analyze job %s not found", job_id)
            return
        job.mark_started()
        job.celery_task_id = self.request.id
        db.commit()

        indices = parameters["indices"]
        entity_id = parameters["entity_id"]
        crop_season_id = parameters.get("crop_season_id")
        season_uuid = uuid.UUID(crop_season_id) if crop_season_id else None

        try:
            job_ids = asyncio.run(persist_copernicus_indices(
                db,
                engine_selector=_engine_selector(),
                quota=SatelliteQuota(),
                tenant_id=tenant_id,
                entity_id=entity_id,
                geometry=parameters["geometry"],
                date_range=(
                    date.fromisoformat(parameters["start_date"]),
                    date.fromisoformat(parameters["end_date"]),
                ),
                indices=indices,
                season_uuid=season_uuid,
                user_id=job.created_by,
            ))
            job.mark_completed({"engine": "copernicus", "indices": indices, "job_ids": job_ids})
            db.commit()
            return
        except Exception as exc:
            copernicus_error = str(exc)
            logger.exception(
                "Copernicus compute failed for %s (%s, tenant %s) — routing local",
                entity_id, indices, tenant_id,
            )

        local = dispatch_local_pipeline(
            db,
            tenant_id=tenant_id,
            entity_id=entity_id,
            user_id=job.created_by,
            geometry=parameters["geometry"],
            bbox=parameters["bbox"],
            indices=indices,
            custom_formula_specs=[],
            start_date_iso=parameters["start_date"],
            end_date_iso=parameters["end_date"],
            local_cloud_threshold=parameters.get("local_cloud_threshold"),
            crop_season_id=crop_season_id,
            season_uuid=season_uuid,
            include_sar=False,
        )
        job.mark_completed({
            "engine": "local_fallback",
            "indices": indices,
            "job_ids": local["job_ids"],
            "copernicus_error": copernicus_error,
        })
        db.commit()
    except Exception as exc:
        logger.exception("copernicus_analyze job %s failed", job_id)
        if job is not None:
            db.rollback()
            job.mark_failed(str(exc), traceback.format_exc())
            db.commit()
    finally:
        db.close()
