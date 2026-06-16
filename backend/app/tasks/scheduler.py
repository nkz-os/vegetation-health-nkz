"""
Periodic tasks for scheduling vegetation updates.
"""
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_, and_

from app.celery_app import celery_app
from app.database import get_db_session
from app.models import VegetationSubscription, VegetationJob
from app.tasks.download_tasks import download_sentinel2_scene
from app.tasks.processing_tasks import calculate_vegetation_index

logger = logging.getLogger(__name__)
# Note: CopernicusDataSpaceClient imported inside check_and_process_entity

# We might need a task to find new scenes first, then download them.
# The current download_sentinel2_scene downloads a SPECIFIC scene ID.
# So we need a "discovery" task.

@celery_app.task(name="vegetation.process_subscriptions")
def process_subscriptions():
    """
    Check for due subscriptions and schedule downloads/calculations. Run hourly.
    Backfill + incremental (§12.2): first run uses subscription start_date (full history);
    subsequent runs use last_run_at so only new scenes are processed.
    """
    db_gen = get_db_session()
    db = next(db_gen)
    
    try:
        now = datetime.utcnow()
        today = now.date()
        
        # Find active subscriptions due for run
        # either next_run_at is reached/passed OR next_run_at is None (new)
        subscriptions = db.query(VegetationSubscription).filter(
            VegetationSubscription.is_active == True,
            or_(
                VegetationSubscription.next_run_at <= now,
                VegetationSubscription.next_run_at == None
            )
        ).all()
        
        logger.info("Processing %d due subscriptions.", len(subscriptions))
        
        for sub in subscriptions:
            # Backfill + incremental: first run uses start_date (full history), then last_run_at
            start_check = (sub.last_run_at.date() if sub.last_run_at else sub.start_date)
            
            # Simple logic: Trigger a new task that discovers and processes scenes for this entity
            # We call a new task: check_and_process_entity
            check_and_process_entity.delay(
                subscription_id=str(sub.id),
                start_date=start_check.isoformat(),
                end_date=today.isoformat()
            )
            
            # Compute next_run_at aligned to date boundary, not current timestamp.
            # Using now + timedelta accumulates microsecond drift: after one iteration
            # next_run_at becomes 02:00:00.991 instead of 02:00:00.000. The scheduler
            # fires at ~02:00:00.884 the next day, which is BEFORE 02:00:00.991 → not due.
            # Rounding to midnight + 2h snaps to the exact beat time regardless of jitter.
            from datetime import time as dt_time

            if sub.frequency == 'daily':
                delta = timedelta(days=1)
            elif sub.frequency == 'weekly':
                delta = timedelta(weeks=1)
            elif sub.frequency == 'biweekly':
                delta = timedelta(weeks=2)
            else:
                delta = timedelta(weeks=1)

            next_run_date = (now + delta).date()
            sub.next_run_at = datetime.combine(next_run_date, dt_time(2, 0))
            sub.last_run_at = now  # Mark as checked
            db.commit()
            
    finally:
        db.close()

@celery_app.task(bind=True, name="vegetation.check_and_process_entity")
def check_and_process_entity(self, subscription_id, start_date, end_date):
    """
    Search for new scenes for the entity and trigger processing.
    """
    import json
    import uuid
    from app.services.copernicus_client import CopernicusDataSpaceClient
    from app.services.platform_credentials import get_copernicus_credentials_with_fallback
    from app.models import VegetationScene, VegetationConfig, VegetationJob
    from geoalchemy2.shape import to_shape

    db_gen = get_db_session()
    db = next(db_gen)
    try:
        sub = db.query(VegetationSubscription).filter(VegetationSubscription.id == subscription_id).first()
        if not sub:
            logger.error("Subscription %s not found", subscription_id)
            return
            
        # Update status to syncing if new
        if sub.status == 'created':
            sub.status = 'syncing'
            db.commit()

        # Parse geometry: bbox for SCL clip and GeoJSON for STAC intersects
        # STAC requires simple Polygon, not MultiPolygon
        try:
            geom_shape = to_shape(sub.geometry)
            bbox = list(geom_shape.bounds)  # [minx, miny, maxx, maxy]
            # STAC API requires Polygon, not MultiPolygon
            if geom_shape.geom_type == 'MultiPolygon':
                # Extract the largest polygon from the MultiPolygon
                largest = max(geom_shape.geoms, key=lambda g: g.area)
                intersects_geojson = largest.__geo_interface__
                logger.debug("Converted MultiPolygon (%d parts) to Polygon for STAC", len(geom_shape.geoms))
            else:
                intersects_geojson = geom_shape.__geo_interface__
            logger.debug("Search geometry type: %s, bbox: %s", intersects_geojson['type'], bbox)
        except Exception as e:
            logger.error("Error parsing geometry for %s: %s", subscription_id, e)
            sub.last_error = f"Geometry error: {str(e)}"
            sub.status = 'error'
            db.commit()
            return

        # Load Copernicus credentials from platform DB (or module config fallback)
        config = db.query(VegetationConfig).filter(
            VegetationConfig.tenant_id == sub.tenant_id
        ).first()
        
        creds = get_copernicus_credentials_with_fallback(
            fallback_client_id=config.copernicus_client_id if config else None,
            fallback_client_secret=config.copernicus_client_secret_encrypted if config else None
        )
        
        if not creds:
            logger.error("Copernicus credentials not available for subscription %s", subscription_id)
            sub.last_error = "Copernicus credentials not configured. Configure in platform admin or module settings."
            sub.status = 'error'
            db.commit()
            return
        
        # Search Copernicus with loaded credentials
        client = CopernicusDataSpaceClient()
        client.set_credentials(creds['client_id'], creds['client_secret'])
        
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        
        try:
            # Phase 3 macro filter: intersects (exact parcel) + cloud_cover_lte 50
            scenes = client.search_scenes(
                intersects=intersects_geojson,
                start_date=start,
                end_date=end,
                cloud_cover_lte=50,
                limit=20,
            )
        except Exception as e:
            logger.error("Error searching scenes: %s", e)
            sub.last_error = f"Search failed: {str(e)}"
            # Don't set error status permanently if just search fail?
            # sub.status = 'error' 
            db.commit()
            return

        if not scenes:
            # No scenes found, but successful search
            if sub.status == 'syncing':
                sub.status = 'active'
                db.commit()
            return

        # Filter existing scenes for this tenant (scene_id unique per tenant)
        scene_ids = [s['id'] for s in scenes]
        existing = db.query(VegetationScene.scene_id).filter(
            VegetationScene.scene_id.in_(scene_ids),
            VegetationScene.tenant_id == sub.tenant_id,
        ).all()
        existing_ids = {r[0] for r in existing}
        
        triggered_count = 0
        for scene in scenes:
            if scene['id'] not in existing_ids:
                dl_params = {
                    'scene_id': scene['id'],
                    'bounds': intersects_geojson,
                    'bbox': bbox,
                    'date': scene['sensing_date'],
                    'entity_id': sub.entity_id,
                    'calculate_indices': sub.index_types,
                    'subscription_id': subscription_id,
                }
                job = VegetationJob(
                    id=uuid.uuid4(),
                    tenant_id=sub.tenant_id,
                    entity_id=sub.entity_id,
                    job_type='download',
                    status='pending',
                    parameters=dl_params,
                )
                db.add(job)
                db.commit()
                logger.info("Triggering download for scene %s", scene['id'])
                download_sentinel2_scene.delay(
                    job_id=str(job.id),
                    tenant_id=sub.tenant_id,
                    parameters=dl_params
                )
                triggered_count += 1
        
        if triggered_count > 0:
            logger.info("Triggered %d S2 downloads for subscription %s", triggered_count, subscription_id)

        # ── Sentinel-1 GRD (SAR) search ─────────────────────────────────
        # Search S1 scenes independently of S2 — SAR penetrates clouds.
        # Create download_sar jobs which then trigger calculate_sar_backscatter
        # jobs with index_type "SAR-VV" and "SAR-VH", integrating into the
        # same VegetationJob pipeline and frontend available_indices.
        try:
            s1_scenes = client.search_s1_scenes(
                intersects=intersects_geojson,
                start_date=start,
                end_date=end,
                limit=10,
            )
            if s1_scenes:
                s1_existing = db.query(VegetationScene.scene_id).filter(
                    VegetationScene.scene_id.in_([s["id"] for s in s1_scenes]),
                    VegetationScene.tenant_id == sub.tenant_id,
                ).all()
                s1_existing_ids = {r[0] for r in s1_existing}

                s1_triggered = 0
                for s1_scene in s1_scenes:
                    if s1_scene["id"] not in s1_existing_ids:
                        s1_params = {
                            "scene_id": s1_scene["id"],
                            "bounds": intersects_geojson,
                            "bbox": bbox,
                            "sensing_date": s1_scene["sensing_date"],
                            "entity_id": sub.entity_id,
                        }
                        s1_job = VegetationJob(
                            id=uuid.uuid4(),
                            tenant_id=sub.tenant_id,
                            entity_id=sub.entity_id,
                            job_type="download_sar",
                            status="pending",
                            parameters=s1_params,
                        )
                        db.add(s1_job)
                        db.commit()

                        from app.tasks.sar_tasks import download_sentinel1_scene
                        download_sentinel1_scene.delay(
                            job_id=str(s1_job.id),
                            tenant_id=sub.tenant_id,
                            parameters=s1_params,
                        )
                        s1_triggered += 1
                        logger.info(
                            "Triggered SAR download for scene %s (%s)",
                            s1_scene["id"], sub.entity_id,
                        )

                if s1_triggered > 0:
                    logger.info(
                        "Triggered %d SAR downloads for subscription %s",
                        s1_triggered, subscription_id,
                    )
        except Exception as e:
            logger.warning(
                "S1 search failed for subscription %s (non-fatal): %s",
                subscription_id, e,
            )

        # Update status to active after queuing initial batch
        if sub.status == 'syncing':
            sub.status = 'active'
            db.commit()

    finally:
        db.close()


@celery_app.task(name="vegetation.reap_stuck_jobs")
def reap_stuck_jobs():
    """Reap stuck jobs and retry them up to 3 times before permanent failure.

    Three patterns:
    - Orphan: 'pending' with NULL celery_task_id older than 10 minutes
      → never enqueued (.delay() likely failed before recording task id).
    - Lost message: 'pending' with non-NULL celery_task_id but never started
      (started_at IS NULL) older than 15 minutes → message sent to broker
      but consumed by wrong worker or dropped.
    - Zombie: 'running' with no progress update for >1 hour
      → worker died mid-task.

    Instead of marking them permanently as 'failed', we reset them to 'pending'
    and increment a retry_count in the result JSON. After 3 failed attempts the
    job is permanently failed. This gives transient Copernicus outages / OOM
    a chance to resolve before the next retry.
    """
    from app.tasks.download_tasks import download_sentinel2_scene
    from app.tasks.processing_tasks import calculate_vegetation_index

    db = next(get_db_session())
    try:
        now = datetime.now(timezone.utc)
        orphan_cutoff = now - timedelta(minutes=10)
        lost_cutoff = now - timedelta(minutes=15)
        zombie_cutoff = now - timedelta(hours=1)
        retries = 0

        # Helpers
        def _increment_retry(job, reason: str):
            """Increment retry_count on a job and reset to pending if <3 retries."""
            result = job.result or {}
            rc = result.get("retry_count", 0) + 1
            result["retry_count"] = rc
            result["retry_reason"] = reason
            job.result = result

            if rc >= 3:
                job.status = "failed"
                job.error_message = f"Permanent failure after {rc} retries: {reason}"
                job.completed_at = now
                logger.warning(
                    "Job %s permanently failed after %d retries: %s",
                    job.id, rc, reason,
                )
                return 0

            # Reset to pending so the next scheduler cycle or manual trigger
            # can pick it up. Clear celery_task_id so the orphan filter catches
            # it if enqueue fails again.
            job.status = "pending"
            job.celery_task_id = None
            job.completed_at = None
            job.error_message = None
            logger.info(
                "Job %s reset to pending (retry %d/3): %s",
                job.id, rc, reason,
            )
            return 1

        def _redispatch(job):
            """Re-dispatch a job to Celery after resetting its status."""
            try:
                if job.job_type == "download":
                    async_result = download_sentinel2_scene.delay(
                        str(job.id), job.tenant_id, job.parameters or {},
                    )
                elif job.job_type == "calculate_index":
                    params = job.parameters or {}
                    async_result = calculate_vegetation_index.delay(
                        str(job.id),
                        job.tenant_id,
                        params.get("scene_id"),
                        params.get("index_type"),
                        params.get("formula"),
                        params.get("start_date"),
                        params.get("end_date"),
                    )
                else:
                    return  # don't know how to redispatch this type
                job.celery_task_id = async_result.id
                return 1
            except Exception as e:
                logger.warning(
                    "Re-dispatch failed for job %s: %s", job.id, e,
                )
                return 0

        # ── Orphans: never enqueued ──────────────────────────────────
        orphans = db.query(VegetationJob).filter(
            VegetationJob.status == "pending",
            VegetationJob.celery_task_id.is_(None),
            VegetationJob.created_at < orphan_cutoff,
        ).all()
        for job in orphans:
            if _increment_retry(job, "never enqueued"):
                retries += _redispatch(job)
        db.commit()

        # ── Lost: enqueued but never started ─────────────────────────
        lost = db.query(VegetationJob).filter(
            VegetationJob.status == "pending",
            VegetationJob.celery_task_id.isnot(None),
            VegetationJob.started_at.is_(None),
            VegetationJob.created_at < lost_cutoff,
        ).all()
        for job in lost:
            if _increment_retry(job, "broker message lost"):
                retries += _redispatch(job)
        db.commit()

        # ── Zombies: stuck in running ────────────────────────────────
        zombies = db.query(VegetationJob).filter(
            VegetationJob.status == "running",
            VegetationJob.updated_at < zombie_cutoff,
        ).all()
        for job in zombies:
            if _increment_retry(job, "stuck >1h"):
                retries += _redispatch(job)
        db.commit()

        if orphans or lost or zombies:
            logger.warning(
                "Reaper: %d orphan(s), %d lost, %d zombie(s) — %d re-queued",
                len(orphans), len(lost), len(zombies), retries,
            )
    finally:
        db.close()
