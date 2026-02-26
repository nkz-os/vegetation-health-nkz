"""
Celery tasks for processing vegetation indices.
"""

import logging
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, date, timezone
import uuid

from app.celery_app import celery_app
from app.models import VegetationJob, VegetationScene, VegetationIndexCache
from app.services.processor import VegetationIndexProcessor
from app.services.storage import create_storage_service, generate_tenant_bucket_name
from app.database import get_db_session

logger = logging.getLogger(__name__)

# Indices published to Orion-LD by default (lazy evaluation).
# All supported indices are published for full discovery by DataHub and Intelligence module.
_ORION_PUBLISHED_INDICES = {"NDVI", "EVI", "SAVI", "GNDVI", "NDRE", "NDWI"}


def _patch_vegetation_status_to_orion(
    tenant_id: str,
    parcel_id: str,
    index_type: str,
    mean_value: float,
    sensing_date: date,
    custom_attr_name: Optional[str] = None,
) -> None:
    """PATCH the latest vegetation index as a flat property on AgriParcel in Orion-LD.

    Uses a flat property per index so the DataHub entity normalization can expose
    each index as an individually selectable attribute:

        ndviMean  → source: "vegetation_health"   (→ env var TIMESERIES_ADAPTER_VEGETATION_HEALTH_URL)
        ndreMean  → source: "vegetation_health"
        custom_<hash>Mean → source: "vegetation_health" (for CUSTOM indices)

    The 'observedAt' temporal metadata follows NGSI-LD §5.2.3.

    This is a best-effort, non-critical operation: if Orion-LD is unavailable
    the PostgreSQL cache (vegetation_indices_cache) remains the source of truth
    and the DataHub adapter will still serve data correctly.
    
    Args:
        custom_attr_name: Optional custom attribute name for CUSTOM indices.
                         If provided, uses '{custom_attr_name}Mean' format.
    """
    from app.services.fiware_integration import FIWAREClient

    try:
        url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
        client = FIWAREClient(url, tenant_id=tenant_id)

        # Use custom attribute name if provided, otherwise derive from index_type
        attr_name = f"{custom_attr_name}Mean" if custom_attr_name else f"{index_type.lower()}Mean"
        observed_at = (
            datetime(sensing_date.year, sensing_date.month, sensing_date.day,
                     tzinfo=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

        client.update_entity({
            "id": parcel_id,
            attr_name: {
                "type": "Property",
                "value": round(float(mean_value), 6),
                "observedAt": observed_at,
                "source": {
                    "type": "Property",
                    # Underscore name → maps to env var TIMESERIES_ADAPTER_VEGETATION_HEALTH_URL
                    "value": "vegetation_health",
                },
            },
        })
        logger.info("Orion-LD PATCH: %s=%s on %s", attr_name, mean_value, parcel_id)
    except Exception as exc:
        logger.warning(
            "Non-critical: could not PATCH Orion-LD for parcel %s (%s): %s",
            parcel_id, index_type, exc,
        )


@celery_app.task(bind=True, name='vegetation.calculate_vegetation_index')
def calculate_vegetation_index(
    self,
    job_id: str,
    tenant_id: str,
    scene_id: str = None,
    index_type: str = None,
    formula: str = None,
    start_date: str = None,
    end_date: str = None
):
    """Calculate vegetation index for a scene or temporal composite.
    
    Args:
        job_id: Job ID
        tenant_id: Tenant ID
        scene_id: Scene ID (for single scene mode)
        index_type: Type of index (NDVI, EVI, SAVI, GNDVI, NDRE, CUSTOM)
        formula: Custom formula (if index_type is CUSTOM)
        start_date: Start date for temporal composite (ISO format)
        end_date: End date for temporal composite (ISO format)
    """
    db = next(get_db_session())
    job = None
    
    try:
        # Get job
        job = db.query(VegetationJob).filter(VegetationJob.id == uuid.UUID(job_id)).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return
        
        # Get parameters from job if not provided (backward compatibility)
        if not index_type:
            index_type = job.parameters.get('index_type')
        if not formula:
            formula = job.parameters.get('formula')
        if not scene_id:
            scene_id = job.parameters.get('scene_id')
        if not start_date and job.start_date:
            start_date = job.start_date.isoformat()
        if not end_date and job.end_date:
            end_date = job.end_date.isoformat()
        
        # Determine mode: single scene or temporal composite
        is_temporal_composite = start_date and end_date
        
        # Update job status
        job.mark_started()
        job.celery_task_id = self.request.id
        db.commit()
        
        # Get storage service
        bucket_name = generate_tenant_bucket_name(tenant_id)
        from app.models import VegetationConfig
        config = db.query(VegetationConfig).filter(
            VegetationConfig.tenant_id == tenant_id
        ).first()
        storage_type = config.storage_type if config else 's3'
        storage = create_storage_service(
            storage_type=storage_type,
            default_bucket=bucket_name
        )
        
        source_image_count = 1
        scenes_to_process: List[VegetationScene] = []
        
        if is_temporal_composite:
            # TEMPORAL COMPOSITE MODE
            logger.info(f"Temporal composite mode: {start_date} to {end_date}")
            self.update_state(state='PROGRESS', meta={'progress': 10, 'message': 'Finding scenes for composite'})
            
            # Parse dates
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
            
            # Find scenes in date range (limit to 10 for performance)
            scenes_query = db.query(VegetationScene).filter(
                VegetationScene.tenant_id == tenant_id,
                VegetationScene.sensing_date >= start,
                VegetationScene.sensing_date <= end
            )
            
            if job.entity_id:
                scenes_query = scenes_query.filter(VegetationScene.entity_id == job.entity_id)
            
            scenes_to_process = scenes_query.order_by(VegetationScene.sensing_date.desc()).limit(10).all()
            
            if not scenes_to_process:
                raise ValueError(f"No scenes found in date range {start_date} to {end_date}")
            
            source_image_count = len(scenes_to_process)
            logger.info(f"Found {source_image_count} scenes for temporal composite")
            
            if source_image_count > 10:
                logger.warning(f"More than 10 scenes found, using first 10 for composite")
                scenes_to_process = scenes_to_process[:10]
                source_image_count = 10
            
        else:
            # SINGLE SCENE MODE
            if not scene_id:
                raise ValueError("scene_id is required for single scene mode")
            
            logger.info(f"Single scene mode: {scene_id}")
            self.update_state(state='PROGRESS', meta={'progress': 10, 'message': 'Loading scene'})
            
            scene = db.query(VegetationScene).filter(
                VegetationScene.id == uuid.UUID(scene_id),
                VegetationScene.tenant_id == tenant_id
            ).first()
            
            if not scene:
                raise ValueError(f"Scene {scene_id} not found")
            
            scenes_to_process = [scene]
        
        # Calculate indices for all scenes
        index_arrays: List[Any] = []
        reference_meta = None
        
        for idx, scene in enumerate(scenes_to_process):
            progress = 20 + (idx * 50 // len(scenes_to_process))
            self.update_state(state='PROGRESS', meta={
                'progress': progress,
                'message': f'Processing scene {idx + 1}/{len(scenes_to_process)}'
            })
            
            # Load band paths
            band_paths = scene.bands or {}
            if not band_paths:
                logger.warning(f"Scene {scene.id} has no bands, skipping")
                continue
            
            # Create processor for this scene
            processor = VegetationIndexProcessor(band_paths)
            
            # Determine required bands based on index type
            required_bands = {
                'NDVI': ['B04', 'B08'],
                'EVI': ['B02', 'B04', 'B08'],
                'SAVI': ['B04', 'B08'],
                'GNDVI': ['B03', 'B08'],
                'NDRE': ['B8A', 'B08'],
            }.get(index_type, ['B04', 'B08'])
            
            # Load bands
            processor.load_bands(required_bands)
            
            # Store reference metadata from first scene
            if reference_meta is None:
                reference_meta = processor.band_meta
            
            # Calculate index
            if index_type == 'NDVI':
                index_array = processor.calculate_ndvi()
            elif index_type == 'EVI':
                index_array = processor.calculate_evi()
            elif index_type == 'SAVI':
                index_array = processor.calculate_savi()
            elif index_type == 'GNDVI':
                index_array = processor.calculate_gndvi()
            elif index_type == 'NDRE':
                index_array = processor.calculate_ndre()
            elif index_type == 'CUSTOM' and formula:
                index_array = processor.calculate_custom_index(formula)
            else:
                raise ValueError(f"Unsupported index type: {index_type}")
            
            index_arrays.append(index_array)
        
        if not index_arrays:
            raise ValueError("No valid scenes processed")
        
        # Create composite if multiple scenes
        self.update_state(state='PROGRESS', meta={'progress': 75, 'message': 'Creating composite'})
        
        if len(index_arrays) > 1:
            # Temporal composite using median (cloud-free)
            composite_array = VegetationIndexProcessor.create_temporal_composite(
                index_arrays,
                method='median'
            )
        else:
            composite_array = index_arrays[0]
        
        # Calculate statistics
        self.update_state(state='PROGRESS', meta={'progress': 80, 'message': 'Calculating statistics'})
        processor = VegetationIndexProcessor({})  # Dummy processor for statistics
        processor.band_meta = reference_meta
        statistics = processor.calculate_statistics(composite_array)
        
        # Save raster
        self.update_state(state='PROGRESS', meta={'progress': 90, 'message': 'Saving results'})
        
        # Use first scene's storage path or create composite path
        if is_temporal_composite:
            output_path = f"composites/{job_id}/{index_type}.tif"
        else:
            output_path = f"{scenes_to_process[0].storage_path}/indices/{index_type}.tif"
        
        processor.save_index_raster(composite_array, output_path)
        
        # Upload to storage
        storage.upload_file(output_path, output_path, bucket_name)
        
        # Create cache entry (use first scene's ID for reference, or create composite entry)
        primary_scene_id = scenes_to_process[0].id
        
        cache_entry = VegetationIndexCache(
            tenant_id=tenant_id,
            scene_id=primary_scene_id,  # Reference to first scene
            entity_id=job.entity_id,
            index_type=index_type,
            formula=formula,
            mean_value=statistics['mean'],
            min_value=statistics['min'],
            max_value=statistics['max'],
            std_dev=statistics['std'],
            pixel_count=statistics['pixel_count'],
            result_raster_path=output_path,
            calculated_at=datetime.utcnow().isoformat(),
            calculation_time_ms=None
        )
        
        db.add(cache_entry)
        
        # Mark job as completed with metadata
        job_result = {
            'index_type': index_type,
            'statistics': statistics,
            'raster_path': output_path,
            'source_image_count': source_image_count,
            'is_composite': is_temporal_composite,
        }
        
        if is_temporal_composite:
            job_result['date_range'] = {
                'start': start_date,
                'end': end_date
            }
        
        job.mark_completed(job_result)
        db.commit()

        # Update job status in usage stats
        from app.services.usage_tracker import UsageTracker
        UsageTracker.update_job_status(db, tenant_id, str(job.id), 'completed')

        # Publish current state to Orion-LD for DataHub discovery.
        # All indices are published including CUSTOM with dynamic attribute naming.
        if job.entity_id:
            # For CUSTOM indices, generate unique attribute name from formula hash
            custom_attr_name = None
            if index_type == 'CUSTOM' and formula:
                import hashlib
                formula_hash = hashlib.md5(formula.encode()).hexdigest()[:8]
                custom_attr_name = f"custom_{formula_hash}"
                logger.info(f"CUSTOM index published as '{custom_attr_name}Mean' in Orion-LD")
            
            _patch_vegetation_status_to_orion(
                tenant_id=tenant_id,
                parcel_id=job.entity_id,
                index_type=index_type,
                mean_value=float(statistics['mean']),
                sensing_date=scenes_to_process[0].sensing_date,
                custom_attr_name=custom_attr_name,
            )

        mode_str = "temporal composite" if is_temporal_composite else "single scene"
        logger.info(f"Index {index_type} calculated successfully ({mode_str}, {source_image_count} scenes)")
        
    except Exception as e:
        logger.error(f"Error calculating index: {str(e)}", exc_info=True)
        if job:
            job.mark_failed(str(e), str(e.__traceback__))
            db.commit()
            # Update job status in usage stats
            from app.services.usage_tracker import UsageTracker
            UsageTracker.update_job_status(db, tenant_id, str(job.id), 'failed')
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name='vegetation.process_index_job')
def process_index_job(self, job_id: str):
    """Process an index calculation job."""
    db = next(get_db_session())
    
    try:
        job = db.query(VegetationJob).filter(VegetationJob.id == uuid.UUID(job_id)).first()
        if not job:
            return
        
        calculate_vegetation_index.delay(
            str(job.id),
            job.tenant_id,
            job.parameters.get('scene_id'),
            job.parameters.get('index_type'),
            job.parameters.get('formula'),
            job.start_date.isoformat() if job.start_date else None,
            job.end_date.isoformat() if job.end_date else None
        )
        
    finally:
        db.close()
