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
            
            # Update next_run_at based on frequency
            if sub.frequency == 'daily':
                delta = timedelta(days=1)
            elif sub.frequency == 'weekly':
                delta = timedelta(weeks=1)
            elif sub.frequency == 'biweekly':
                delta = timedelta(weeks=2)
            else:
                delta = timedelta(weeks=1) # Default
            
            # If it was never run, set next run to tomorrow (to avoid spinning if logic fails today)
            # Or better, set it to now + frequency
            sub.next_run_at = now + delta
            sub.last_run_at = now # Mark as checked
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
            # Phase 3 macro filter: intersects (exact parcel) + cloud_cover_lte 60
            scenes = client.search_scenes(
                intersects=intersects_geojson,
                start_date=start,
                end_date=end,
                cloud_cover_lte=60,
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
            logger.info("Triggered %d downloads for subscription %s", triggered_count, subscription_id)

        # Update status to active after queuing initial batch
        if sub.status == 'syncing':
            sub.status = 'active'
            db.commit()

    finally:
        db.close()


@celery_app.task(name="vegetation.reap_stuck_jobs")
def reap_stuck_jobs():
    """Mark orphaned/zombie/lost jobs as failed.

    Three patterns we want to catch:
    - Orphan: 'pending' with NULL celery_task_id older than 10 minutes
      → never enqueued (.delay() likely failed before recording task id).
    - Lost message: 'pending' with non-NULL celery_task_id but never started
      (started_at IS NULL) older than 15 minutes → message was sent to the
      broker but consumed by the wrong worker (cross-app queue collision)
      or otherwise dropped before the right worker received it.
    - Zombie: 'running' with no progress update for >1 hour
      → worker died mid-task without acking.
    """
    db = next(get_db_session())
    try:
        now = datetime.now(timezone.utc)
        orphan_cutoff = now - timedelta(minutes=10)
        lost_cutoff = now - timedelta(minutes=15)
        zombie_cutoff = now - timedelta(hours=1)

        orphans = db.query(VegetationJob).filter(
            VegetationJob.status == "pending",
            VegetationJob.celery_task_id.is_(None),
            VegetationJob.created_at < orphan_cutoff,
        ).all()
        for job in orphans:
            job.status = "failed"
            job.error_message = "Reaped: pending without celery_task_id (never enqueued)"
            job.completed_at = now

        lost = db.query(VegetationJob).filter(
            VegetationJob.status == "pending",
            VegetationJob.celery_task_id.isnot(None),
            VegetationJob.started_at.is_(None),
            VegetationJob.created_at < lost_cutoff,
        ).all()
        for job in lost:
            job.status = "failed"
            job.error_message = (
                "Reaped: enqueued but never started (broker message lost or "
                "consumed by wrong worker). Retry the analysis."
            )
            job.completed_at = now

        zombies = db.query(VegetationJob).filter(
            VegetationJob.status == "running",
            VegetationJob.updated_at < zombie_cutoff,
        ).all()
        for job in zombies:
            job.status = "failed"
            job.error_message = "Reaped: stuck in 'running' >1h with no progress update"
            job.completed_at = now

        if orphans or lost or zombies:
            db.commit()
            logger.warning(
                "Reaper: failed %d orphan(s), %d lost-message(s), %d zombie(s)",
                len(orphans), len(lost), len(zombies),
            )
    finally:
        db.close()
