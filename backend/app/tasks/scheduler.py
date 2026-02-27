"""
Periodic tasks for scheduling vegetation updates.
"""
from datetime import datetime, timedelta
from sqlalchemy import or_

from app.celery_app import celery_app
from app.database import get_db_session
from app.models import VegetationSubscription
from app.tasks.download_tasks import download_sentinel2_scene
from app.tasks.processing_tasks import calculate_vegetation_index
# Note: CopernicusDataSpaceClient imported inside check_and_process_entity

# We might need a task to find new scenes first, then download them.
# The current download_sentinel2_scene downloads a SPECIFIC scene ID.
# So we need a "discovery" task.

@celery_app.task(name="vegetation.process_subscriptions")
def process_subscriptions():
    """
    Check for due subscriptions and schedule downloads/calculations.
    Run hourly.
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
        
        print(f"INFO: Processing {len(subscriptions)} due subscriptions.")
        
        for sub in subscriptions:
            # Determine date range to check
            # From last run (or start date) to today
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
            print(f"Error: Subscription {subscription_id} not found")
            return
            
        # Update status to syncing if new
        if sub.status == 'created':
            sub.status = 'syncing'
            db.commit()

        # Parse geometry: bbox for SCL clip and GeoJSON for STAC intersects (Phase 3 SOTA)
        try:
            geom_shape = to_shape(sub.geometry)
            bbox = list(geom_shape.bounds)  # [minx, miny, maxx, maxy]
            intersects_geojson = geom_shape.__geo_interface__  # GeoJSON for strict intersection
        except Exception as e:
            print(f"Error parsing geometry for {subscription_id}: {e}")
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
            print(f"Error: Copernicus credentials not available for subscription {subscription_id}")
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
            print(f"Error searching scenes: {e}")
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
                print(f"Triggering download for scene {scene['id']}")
                download_sentinel2_scene.delay(
                    job_id=str(job.id),
                    tenant_id=sub.tenant_id,
                    parameters=dl_params
                )
                triggered_count += 1
        
        if triggered_count > 0:
            print(f"Triggered {triggered_count} downloads for subscription {subscription_id}")
            
        # Update status to active after queuing initial batch
        if sub.status == 'syncing':
            sub.status = 'active'
            db.commit()

    finally:
        db.close()
