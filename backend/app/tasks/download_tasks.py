"""
Celery tasks for downloading Sentinel-2 scenes.
UPDATED: Real implementation with Copernicus Data Space Ecosystem.
"""

import logging
from typing import Dict, Any
from datetime import datetime, timedelta, date, timezone
import uuid
import os
from pathlib import Path

from app.celery_app import celery_app
from app.models import VegetationJob, VegetationScene, VegetationConfig, GlobalSceneCache
from app.services.storage import create_storage_service, generate_tenant_bucket_name, get_global_bucket_name
from app.services.copernicus_client import CopernicusDataSpaceClient
from app.services.platform_credentials import get_copernicus_credentials_with_fallback
from app.database import get_db_session

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name='vegetation.download_sentinel2_scene')
def download_sentinel2_scene(self, job_id: str, tenant_id: str, parameters: Dict[str, Any]):
    """Download Sentinel-2 scene from Copernicus Data Space Ecosystem.
    
    Args:
        job_id: Job ID
        tenant_id: Tenant ID
        parameters: Job parameters including bounds, date range, etc.
    """
    db = next(get_db_session())
    
    try:
        # Get job
        job = db.query(VegetationJob).filter(VegetationJob.id == uuid.UUID(job_id)).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return
        
        # Update job status
        job.mark_started()
        job.celery_task_id = self.request.id
        db.commit()
        
        # Get tenant configuration
        config = db.query(VegetationConfig).filter(
            VegetationConfig.tenant_id == tenant_id
        ).first()
        
        # Get Copernicus credentials from platform (preferred) or module config (fallback)
        creds = get_copernicus_credentials_with_fallback(
            fallback_client_id=config.copernicus_client_id if config else None,
            fallback_client_secret=config.copernicus_client_secret_encrypted if config else None
        )
        
        if not creds:
            raise ValueError(
                "Copernicus credentials not available. "
                "Please configure credentials in the platform admin panel or in module settings."
            )
        
        # Initialize Copernicus client with credentials from platform or module config
        copernicus_client = CopernicusDataSpaceClient()
        copernicus_client.set_credentials(
            client_id=creds['client_id'],
            client_secret=creds['client_secret']
        )
        
        # Extract parameters (bounds/bbox for SCL clip and scene selection)
        bounds = parameters.get('bounds')
        bbox = parameters.get('bbox')
        if bbox and len(bbox) >= 4:
            bbox = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
        elif bounds:
            from shapely.geometry import shape as shp
            geom = shp(bounds)
            if hasattr(geom, 'geoms'):
                geom = geom.geoms[0] if geom.geoms else geom
            bbox = list(geom.bounds)
        else:
            raise ValueError("Invalid bounds or bbox provided")
        
        preset_scene_id = parameters.get('scene_id')
        if preset_scene_id:
            # Phase 3 subscription flow: single scene, run SCL micro validation first
            self.update_state(state='PROGRESS', meta={'progress': 15, 'message': 'Fetching scene item'})
            best_scene = copernicus_client.get_scene_item(preset_scene_id)
            scene_id = best_scene['id']
            local_threshold = float(os.getenv('LOCAL_CLOUD_THRESHOLD_PCT', '10'))
            self.update_state(state='PROGRESS', meta={'progress': 18, 'message': 'SCL validation (micro filter)'})
            import tempfile
            from app.services.scl_validation import compute_local_cloud_pct
            with tempfile.TemporaryDirectory() as tmpdir:
                scl_path = os.path.join(tmpdir, f"{scene_id}_SCL.tif")
                try:
                    copernicus_client.download_band(scene_id, 'SCL', scl_path)
                except Exception as e:
                    logger.warning("SCL band not available for %s: %s; proceeding without micro filter", scene_id, e)
                    scl_path = None
            if scl_path:
                # Use exact parcel polygon (bounds GeoJSON), not bbox, so we only count clouds over the client parcel
                parcel_geojson = parameters.get('bounds') or {
                    'type': 'Polygon',
                    'coordinates': [[[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]],
                }
                local_cloud_pct = compute_local_cloud_pct(scl_path, parcel_geojson)
                if local_cloud_pct > local_threshold:
                    logger.info("Scene %s skipped: local_cloud_pct=%.1f > threshold=%.1f", scene_id, local_cloud_pct, local_threshold)
                    from shapely.geometry import shape as shp
                    from geoalchemy2.shape import from_shape
                    geom = shp(best_scene.get('geometry') or {'type': 'Polygon', 'coordinates': [[[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]]})
                    if hasattr(geom, 'geoms') and geom.geoms:
                        geom = geom.geoms[0]
                    footprint = from_shape(geom, srid=4326)
                    acquisition_dt = None
                    if best_scene.get('datetime'):
                        try:
                            iso = best_scene['datetime'].replace('Z', '+00:00')
                            acquisition_dt = datetime.fromisoformat(iso)
                            if acquisition_dt.tzinfo is None:
                                acquisition_dt = acquisition_dt.replace(tzinfo=timezone.utc)
                        except (ValueError, TypeError):
                            pass
                    skipped_scene = VegetationScene(
                        tenant_id=tenant_id,
                        scene_id=scene_id,
                        sensing_date=date.fromisoformat(best_scene['sensing_date']),
                        acquisition_datetime=acquisition_dt,
                        footprint=footprint,
                        cloud_coverage=str(best_scene.get('cloud_cover', '')),
                        storage_path='',
                        storage_bucket=None,
                        bands=None,
                        is_valid=False,
                        quality_flags={'skipped_due_to_clouds': True, 'local_cloud_pct': round(local_cloud_pct, 2)},
                        job_id=job.id,
                    )
                    db.add(skipped_scene)
                    db.commit()
                    job.mark_completed({
                        'skipped_due_to_clouds': True,
                        'local_cloud_pct': round(local_cloud_pct, 2),
                        'message': f'Scene skipped: local cloud {local_cloud_pct:.1f}% > {local_threshold}%',
                    })
                    db.commit()
                    return
                self.update_state(state='PROGRESS', meta={'progress': 30, 'message': f'Scene {scene_id} passed SCL'})
        else:
            # Manual flow: search then pick best scene
            start_date = parameters.get('start_date')
            end_date = parameters.get('end_date')
            cloud_threshold = parameters.get('cloud_coverage_threshold', config.cloud_coverage_threshold)
            if isinstance(start_date, str):
                start_date = date.fromisoformat(start_date)
            elif start_date is None:
                start_date = date.today() - timedelta(days=30)
            if isinstance(end_date, str):
                end_date = date.fromisoformat(end_date)
            elif end_date is None:
                end_date = date.today()
            self.update_state(state='PROGRESS', meta={'progress': 20, 'message': 'Searching Copernicus catalog'})
            scenes = copernicus_client.search_scenes(
                bbox=bbox,
                start_date=start_date,
                end_date=end_date,
                cloud_cover_max=float(cloud_threshold),
                limit=10,
            )
            if not scenes:
                raise ValueError("No scenes found matching criteria")
            best_scene = sorted(scenes, key=lambda s: (s['cloud_cover'], s['sensing_date']), reverse=True)[0]
            scene_id = best_scene['id']
            self.update_state(state='PROGRESS', meta={'progress': 30, 'message': f'Found scene {scene_id}'})
        
        # Determine which bands to download (SCL for cloud masking; rest for indices)
        required_bands = ['B02', 'B03', 'B04', 'B08', 'B11', 'B12', 'SCL']
        
        # =============================================================================
        # HYBRID CACHE LOGIC: Check global cache first, then download if needed
        # =============================================================================
        global_bucket_name = get_global_bucket_name()
        tenant_bucket_name = generate_tenant_bucket_name(tenant_id)
        
        # Get storage services (global and tenant)
        global_storage = create_storage_service(
            storage_type=config.storage_type,
            default_bucket=global_bucket_name
        )
        tenant_storage = create_storage_service(
            storage_type=config.storage_type,
            default_bucket=tenant_bucket_name
        )
        
        # Step 1: Check if scene exists in global cache
        self.update_state(state='PROGRESS', meta={'progress': 35, 'message': 'Checking global cache'})
        global_cache_entry = db.query(GlobalSceneCache).filter(
            GlobalSceneCache.scene_id == scene_id,
            GlobalSceneCache.is_valid == True
        ).first()
        
        scene_downloaded_from_copernicus = False
        all_bands_exist = False
        
        if global_cache_entry:
            # Step 2: Scene exists in cache - verify files exist and copy to tenant bucket
            logger.info(f"Scene {scene_id} found in global cache - reusing (download_count: {global_cache_entry.download_count})")
            self.update_state(state='PROGRESS', meta={'progress': 50, 'message': 'Copying from global cache'})
            
            # Verify all required bands exist in global bucket
            all_bands_exist = True
            for band in required_bands:
                global_band_path = global_cache_entry.get_band_path(band)
                if not global_band_path or not global_storage.file_exists(global_band_path, global_bucket_name):
                    logger.warning(f"Band {band} missing in global cache for scene {scene_id}")
                    all_bands_exist = False
                    break
            
            if all_bands_exist:
                # Copy bands from global bucket to tenant bucket
                storage_band_paths = {}
                for band in required_bands:
                    global_band_path = global_cache_entry.get_band_path(band)
                    tenant_band_path = f"{config.storage_path}scenes/{scene_id}/{band}.tif"
                    
                    # Copy file from global to tenant bucket
                    tenant_storage.copy_file(
                        source_path=global_band_path,
                        dest_path=tenant_band_path,
                        source_bucket=global_bucket_name,
                        dest_bucket=tenant_bucket_name
                    )
                    storage_band_paths[band] = tenant_band_path
                    logger.info(f"Copied band {band} from global cache to tenant bucket")
                
                # Increment reuse counter
                global_cache_entry.increment_download_count()
                db.commit()
                
                logger.info(f"Scene {scene_id} reused from cache (new download_count: {global_cache_entry.download_count})")
            else:
                # Some bands missing - mark as invalid and download fresh
                logger.warning(f"Scene {scene_id} in cache but some bands missing - marking invalid and downloading fresh")
                global_cache_entry.is_valid = False
                db.commit()
                global_cache_entry = None  # Force download below
        
        if not global_cache_entry or not all_bands_exist:
            # Step 3: Scene not in cache or invalid - download from Copernicus
            logger.info(f"Scene {scene_id} not in cache - downloading from Copernicus")
            self.update_state(state='PROGRESS', meta={'progress': 40, 'message': 'Downloading from Copernicus'})
            scene_downloaded_from_copernicus = True
            
            # Create temporary directory for downloads
            temp_dir = Path(f"/tmp/vegetation_downloads/{tenant_id}/{job_id}")
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            # Download bands from Copernicus
            band_paths = copernicus_client.download_scene_bands(
                scene_id=scene_id,
                bands=required_bands,
                output_dir=str(temp_dir)
            )
            
            if not band_paths:
                raise ValueError("Failed to download any bands")
            
            # Upload to global bucket first (for future reuse)
            self.update_state(state='PROGRESS', meta={'progress': 60, 'message': 'Uploading to global cache'})
            global_band_paths = {}
            for band, local_path in band_paths.items():
                global_band_path = f"scenes/{scene_id}/{band}.tif"
                global_storage.upload_file(local_path, global_band_path, global_bucket_name)
                global_band_paths[band] = global_band_path
                logger.info(f"Uploaded band {band} to global cache")
            
            # Create or update global cache entry
            global_cache_entry = db.query(GlobalSceneCache).filter(
                GlobalSceneCache.scene_id == scene_id
            ).first()
            
            if global_cache_entry:
                # Update existing entry (was marked invalid)
                global_cache_entry.is_valid = True
                global_cache_entry.bands = global_band_paths
                global_cache_entry.storage_path = f"scenes/{scene_id}/"
                global_cache_entry.storage_bucket = global_bucket_name
                global_cache_entry.cloud_coverage = str(best_scene['cloud_cover'])
                global_cache_entry.sensing_date = date.fromisoformat(best_scene['sensing_date'])
            else:
                # Create new cache entry
                global_cache_entry = GlobalSceneCache(
                    scene_id=scene_id,
                    product_type='S2MSI2A',
                    platform='Sentinel-2',
                    sensing_date=date.fromisoformat(best_scene['sensing_date']),
                    storage_path=f"scenes/{scene_id}/",
                    storage_bucket=global_bucket_name,
                    bands=global_band_paths,
                    cloud_coverage=str(best_scene['cloud_cover']),
                    download_count=0,
                    is_valid=True
                )
                db.add(global_cache_entry)
            
            db.commit()
            logger.info(f"Scene {scene_id} added to global cache")
            
            # Copy from global bucket to tenant bucket
            self.update_state(state='PROGRESS', meta={'progress': 75, 'message': 'Copying to tenant bucket'})
            storage_band_paths = {}
            for band in required_bands:
                global_band_path = global_band_paths[band]
                tenant_band_path = f"{config.storage_path}scenes/{scene_id}/{band}.tif"
                
                # Copy file from global to tenant bucket
                tenant_storage.copy_file(
                    source_path=global_band_path,
                    dest_path=tenant_band_path,
                    source_bucket=global_bucket_name,
                    dest_bucket=tenant_bucket_name
                )
                storage_band_paths[band] = tenant_band_path
                logger.info(f"Copied band {band} to tenant bucket")
            
            # Clean up local files
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        # Step 4: Create tenant scene record
        self.update_state(state='PROGRESS', meta={'progress': 90, 'message': 'Creating scene record'})

        acquisition_dt = None
        if best_scene.get('datetime'):
            try:
                iso = best_scene['datetime'].replace('Z', '+00:00')
                acquisition_dt = datetime.fromisoformat(iso)
                if acquisition_dt.tzinfo is None:
                    acquisition_dt = acquisition_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        scene = VegetationScene(
            tenant_id=tenant_id,
            scene_id=scene_id,
            sensing_date=date.fromisoformat(best_scene['sensing_date']),
            acquisition_datetime=acquisition_dt,
            footprint=None,  # TODO: Convert geometry to PostGIS
            cloud_coverage=str(best_scene['cloud_cover']),
            storage_path=f"{config.storage_path}scenes/{scene_id}/",
            storage_bucket=tenant_bucket_name,
            bands=storage_band_paths,
            job_id=job.id
        )
        
        db.add(scene)
        db.commit()
        
        # Mark job as completed
        cache_status = 'reused' if not scene_downloaded_from_copernicus else 'downloaded'
        job.mark_completed({
            'scene_id': str(scene.id),
            'scene_product_id': scene_id,
            'bands_downloaded': required_bands,
            'cache_status': cache_status,
            'message': f'Scene {cache_status} successfully'
        })
        db.commit()
        
        # Update job status in usage stats
        from app.services.usage_tracker import UsageTracker
        UsageTracker.update_job_status(db, tenant_id, str(job.id), 'completed')

        # Check for chained calculation trigger
        calculate_indices = parameters.get('calculate_indices')
        if calculate_indices and isinstance(calculate_indices, list):
            try:
                from app.tasks.processing_tasks import calculate_vegetation_index
                import uuid
                
                # Trigger calculation for each index
                for index_type in calculate_indices:
                    calc_job_id = str(uuid.uuid4())
                    calc_params = {
                        'scene_id': scene_id,
                        'index_type': index_type,
                        'entity_id': parameters.get('entity_id'),
                        'bbox': parameters.get('bbox')
                    }
                    
                    # Create job record
                    calc_job = VegetationJob(
                        id=uuid.UUID(calc_job_id),
                        tenant_id=tenant_id,
                        entity_id=parameters.get('entity_id'),
                        job_type='calculation',
                        status='pending',
                        parameters=calc_params
                    )
                    db.add(calc_job)
                    db.commit()
                    
                    logger.info(f"Auto-triggering calculation for {index_type} (Job {calc_job_id})")
                    calculate_vegetation_index.delay(
                        job_id=calc_job_id,
                        tenant_id=tenant_id,
                        scene_id=str(scene.id),
                        index_type=index_type
                    )
            except Exception as e:
                logger.error(f"Failed to trigger automated calculation: {e}")
        
        logger.info(f"Job {job_id} completed successfully - Scene {scene_id}")
        
    except Exception as e:
        logger.error(f"Error in download task: {str(e)}", exc_info=True)
        if job:
            job.mark_failed(str(e), str(e.__traceback__))
            db.commit()
            # Update job status in usage stats
            from app.services.usage_tracker import UsageTracker
            UsageTracker.update_job_status(db, tenant_id, str(job.id), 'failed')
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name='vegetation.process_download_job')
def process_download_job(self, job_id: str):
    """Process a download job (wrapper for download_sentinel2_scene)."""
    db = next(get_db_session())
    
    try:
        job = db.query(VegetationJob).filter(VegetationJob.id == uuid.UUID(job_id)).first()
        if not job:
            return
        
        download_sentinel2_scene.delay(
            str(job.id),
            job.tenant_id,
            job.parameters
        )
        
    finally:
        db.close()
