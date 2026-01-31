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
    from app.models import VegetationScene
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

        # Parse geometry to get bbox using GeoAlchemy2
        try:
            # sub.geometry is WKBElement
            geom_shape = to_shape(sub.geometry)
            # Returns (minx, miny, maxx, maxy)
            bbox = list(geom_shape.bounds)
        except Exception as e:
            print(f"Error parsing geometry for {subscription_id}: {e}")
            sub.last_error = f"Geometry error: {str(e)}"
            sub.status = 'error'
            db.commit()
            return

        # Search Copernicus
        client = CopernicusDataSpaceClient()
        
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        
        try:
            scenes = client.search_scenes(bbox=bbox, start_date=start, end_date=end, limit=20)
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

        # Filter existing scenes for this tenant
        scene_ids = [s['id'] for s in scenes]
        existing = db.query(VegetationScene.scene_id).filter(
            VegetationScene.scene_id.in_(scene_ids),
            VegetationScene.tenant_id == sub.tenant_id,
            VegetationScene.entity_id == sub.entity_id 
        ).all()
        existing_ids = {r[0] for r in existing}
        
        triggered_count = 0
        for scene in scenes:
            if scene['id'] not in existing_ids:
                job_id = str(uuid.uuid4())
                
                # Parameters for download task
                dl_params = {
                    'scene_id': scene['id'],
                    'bbox': bbox,
                    'date': scene['sensing_date'],
                    'entity_id': sub.entity_id,
                    'calculate_indices': sub.index_types, 
                    'subscription_id': subscription_id
                }
                
                print(f"Triggering download for scene {scene['id']}")
                download_sentinel2_scene.delay(
                    job_id=job_id,
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
