"""
Celery tasks for processing vegetation indices.

Orion-LD is the source of truth. Results are persisted as VegetationIndex
entities via upsert_vegetation_index_entity(). TimescaleDB is populated
automatically by the telemetry-worker subscription on Orion-LD.
"""

import logging
import os
import hashlib
from typing import Dict, Any, List, Optional
from datetime import datetime, date, timezone
import uuid
from pathlib import Path
import numpy as np

from app.celery_app import celery_app
from app.models import VegetationJob, VegetationScene
from app.services.processor import VegetationIndexProcessor
from app.services.storage import create_storage_service, generate_tenant_bucket_name
from app.database import get_db_session
from app.services.fiware_integration import upsert_vegetation_index_entity

logger = logging.getLogger(__name__)

# Redis client for idempotency (reuses Celery broker connection)
_redis_client = None

def _get_redis():
    """Lazy-init Redis client for idempotency checks."""
    global _redis_client
    if _redis_client is None:
        import redis
        redis_url = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
        _redis_client = redis.from_url(redis_url, decode_responses=True)
    return _redis_client


def _decrement_and_cleanup_bands(scene_product_id: str) -> bool:
    """Decrement Redis counter and cleanup tenant band files if this is the last calculation.

    Returns True if bands were cleaned up, False otherwise.
    """
    try:
        key = f"vegetation:bandclean:{scene_product_id}"
        r = _get_redis()
        remaining = r.decr(key)
        if remaining is not None and remaining <= 0:
            meta = r.hgetall(key + ":meta")
            tenant_bucket = meta.get("bucket") if meta else None
            storage_prefix = meta.get("prefix", "").rstrip("/") if meta else ""
            if tenant_bucket and storage_prefix:
                band_prefix = f"{storage_prefix}scenes/{scene_product_id}/"
                storage = create_storage_service(
                    storage_type=os.getenv('STORAGE_TYPE', 's3'),
                    default_bucket=tenant_bucket,
                )
                deleted = storage.delete_prefix(band_prefix, tenant_bucket)
                logger.info(
                    "Band cleanup: deleted %d band files from tenant bucket %s/%s",
                    deleted, tenant_bucket, band_prefix,
                )
            r.delete(key, key + ":meta")
            return True
        elif remaining is None:
            logger.debug("Band cleanup counter not found for scene %s (may have expired)", scene_product_id)
        else:
            logger.debug("Band cleanup deferred for scene %s: %d calculations remaining", scene_product_id, remaining)
    except Exception as e:
        logger.warning("Band cleanup check failed for scene %s: %s", scene_product_id, e)
    return False


def _idempotency_key(tenant_id: str, parcel_id: str, index_type: str, sensing_date_str: str) -> str:
    return f"vegetation:calc:{tenant_id}:{parcel_id}:{index_type}:{sensing_date_str}"


def _check_idempotency(tenant_id: str, parcel_id: str, index_type: str, sensing_date_str: str) -> bool:
    """Atomically claim the slot for this (tenant, parcel, index, date)
    calculation in Redis (SETNX, TTL 24h). Two workers racing on the same
    key cannot both proceed — only one wins.

    Returns True if a previous run already claimed this slot (caller must
    skip), False when the claim is fresh and the caller should compute.

    The caller is responsible for releasing the key on failure via
    _release_idempotency, otherwise a single failed run would lock out
    retries for 24h.
    """
    try:
        key = _idempotency_key(tenant_id, parcel_id, index_type, sensing_date_str)
        r = _get_redis()
        was_set = r.set(key, "1", nx=True, ex=86400)
        return not was_set
    except Exception as e:
        logger.debug("Redis idempotency check failed, proceeding: %s", e)
        return False


def _release_idempotency(tenant_id: str, parcel_id: str, index_type: str, sensing_date_str: str) -> None:
    """Release a previously-claimed idempotency slot. Called after a failed
    calc_index so the next retry can compute again, and from the DELETE
    cascade so users can re-run an analysis they explicitly removed."""
    try:
        key = _idempotency_key(tenant_id, parcel_id, index_type, sensing_date_str)
        _get_redis().delete(key)
    except Exception as e:
        logger.debug("Redis idempotency release failed (non-fatal): %s", e)


def _extract_formula_bands(formula: str) -> List[str]:
    """Extract Sentinel-2 bands used by a custom formula."""
    import re
    if not formula:
        return []
    found = sorted(set(re.findall(r"\bB(?:0[2-8]|8A|1[12])\b", formula.upper())))
    return found


def _persist_results(
    tenant_id: str,
    job: VegetationJob,
    index_type: str,
    formula: Optional[str],
    statistics: Dict[str, Any],
    remote_raster_path: str,
    primary_scene: VegetationScene,
) -> None:
    """Persist analysis results to Orion-LD as VegetationIndex entity.

    Orion-LD is the source of truth. TimescaleDB is populated automatically
    via the NGSI-LD subscription from Orion-LD to telemetry-worker.
    """
    custom_attr_name = None
    if index_type == 'CUSTOM' and formula:
        formula_hash = hashlib.md5(formula.encode()).hexdigest()[:8]
        custom_attr_name = f"custom_{formula_hash}"

    if not job.entity_id:
        logger.warning("No entity_id on job %s — cannot upsert VegetationIndex", job.id)
        return

    raster_url = f"s3://{os.getenv('VEGETATION_COG_BUCKET', 'vegetation-prime')}/{remote_raster_path}"
    result = upsert_vegetation_index_entity(
        tenant_id=tenant_id,
        parcel_id=job.entity_id,
        index_type=index_type,
        statistics=statistics,
        raster_url=raster_url,
        sensing_date=primary_scene.sensing_date,
        custom_attr_name=custom_attr_name,
    )
    if result:
        logger.info("VegetationIndex entity upserted: %s", result)
    else:
        logger.error("Failed to upsert VegetationIndex entity")


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
        formula_id = job.parameters.get('formula_id') if job.parameters else None
        formula_name = job.parameters.get('formula_name') if job.parameters else None
        result_index_key = job.parameters.get('result_index_key') if job.parameters else None
        idempotency_key = result_index_key or (f"custom:{formula_id}" if index_type == "CUSTOM" and formula_id else index_type)
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
        bucket_name = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(tenant_id)
        from app.models import VegetationConfig
        config = db.query(VegetationConfig).filter(
            VegetationConfig.tenant_id == tenant_id
        ).first()
        storage_type = config.storage_type if config else os.getenv('STORAGE_TYPE', 's3')
        storage = create_storage_service(
            storage_type=storage_type,
            default_bucket=bucket_name
        )

        # ── VRA Zoning mode ────────────────────────────────────────────────
        # Short-circuit: zoning does not use per-scene index calculation.
        if index_type == 'VRA_ZONES':
            logger.info("VRA zoning mode for entity %s", job.entity_id)
            entity_id = job.entity_id
            if not entity_id:
                raise ValueError("entity_id is required for VRA zoning")

            n_zones = (job.parameters or {}).get('n_zones', 3)
            from app.jobs.zoning_algorithm import ZoningAlgorithm
            zoning = ZoningAlgorithm(tenant_id=tenant_id)
            result = zoning.execute(
                parcel_id=entity_id,
                scene_id="",
                parameters={"n_zones": n_zones},
            )

            if result.get('status') == 'error':
                raise ValueError(result.get('message', 'Zoning algorithm failed'))

            job.mark_completed({
                'status': result.get('status'),
                'zones_created': result.get('zones_created'),
                'message': result.get('message'),
                'geojson': result.get('geojson'),
                'index_type': 'VRA_ZONES',
            })
            db.commit()

            logger.info(
                "VRA zoning completed for %s: %s zones",
                entity_id, result.get('zones_created', 0),
            )

            # Band-cleanup not applicable for zoning
            db.close()
            return {
                'status': 'completed',
                'index_type': 'VRA_ZONES',
                'zones': result.get('zones_created', 0),
            }

        source_image_count = 1
        scenes_to_process: List[VegetationScene] = []

        if is_temporal_composite:
            # TEMPORAL COMPOSITE MODE
            logger.info(f"Temporal composite mode: {start_date} to {end_date}")
            self.update_state(state='PROGRESS', meta={'progress': 10, 'message': 'Finding scenes for composite'})

            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)

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

        else:
            # SINGLE SCENE MODE
            if not scene_id:
                raise ValueError("scene_id is required for single scene mode")

            logger.info(f"Single scene mode: {scene_id}")
            self.update_state(state="PROGRESS", meta={"progress": 10, "message": "Loading scene"})

            # scene_id can be a UUID (from DB) or a Sentinel-2 product ID (from API)
            scene_query = db.query(VegetationScene).filter(
                VegetationScene.tenant_id == tenant_id,
            )
            try:
                scene = scene_query.filter(VegetationScene.id == uuid.UUID(scene_id)).first()
            except (ValueError, AttributeError):
                scene = scene_query.filter(VegetationScene.scene_id == scene_id).first()

            if not scene:
                raise ValueError(f"Scene {scene_id} not found")

            # Idempotency check via Redis (always)
            sensing_str = scene.sensing_date.isoformat() if scene.sensing_date else "unknown"
            if _check_idempotency(tenant_id, job.entity_id or "", idempotency_key, sensing_str):
                logger.info(
                    "Idempotency: %s for scene %s already calculated, skipping",
                    index_type, scene.id,
                )
                job.mark_completed({
                    "index_type": index_type,
                    "index_key": idempotency_key,
                    "scene_id": str(scene.id),
                    "sensing_date": scene.sensing_date.isoformat() if scene.sensing_date else None,
                    "statistics": {},
                    "raster_path": None,
                    "source_image_count": 1,
                    "is_composite": False,
                    "skipped": True,
                })
                db.commit()
                _decrement_and_cleanup_bands(scene.scene_id)
                return

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

            band_paths = scene.bands or {}
            if not band_paths:
                logger.warning(f"Scene {scene.id} has no bands, skipping")
                continue

            required_bands = {
                'NDVI': ['B04', 'B08'],
                'EVI': ['B02', 'B04', 'B08'],
                'SAVI': ['B04', 'B08'],
                'GNDVI': ['B03', 'B08'],
                'NDRE': ['B8A', 'B08'],
            }.get(index_type, ['B04', 'B08'])
            if index_type == 'CUSTOM':
                required_bands = _extract_formula_bands(formula) or ['B04', 'B08']

            storage = create_storage_service(
                storage_type=storage_type,
                default_bucket=scene.storage_bucket or generate_tenant_bucket_name(tenant_id)
            )

            local_band_dir = Path(f"/tmp/vegetation_processing/{tenant_id}/{scene.id}")
            local_band_dir.mkdir(parents=True, exist_ok=True)

            local_band_paths = {}
            for band in required_bands:
                remote_path = (scene.bands or {}).get(band)
                if not remote_path:
                    logger.error(f"Band {band} not found in metadata for scene {scene.id}")
                    continue

                local_path = local_band_dir / f"{band}.tif"
                if not local_path.exists():
                    logger.info(f"Downloading band {band} from {scene.storage_bucket} to {local_path}")
                    storage.download_file(remote_path, str(local_path), scene.storage_bucket)

                local_band_paths[band] = str(local_path)

            if not local_band_paths:
                logger.error(f"No bands could be downloaded for scene {scene.id}")
                continue

            crop_bbox = job.parameters.get('bbox') if job.parameters else None
            processor = VegetationIndexProcessor(local_band_paths, bbox=crop_bbox)
            processor.load_bands(required_bands)

            if reference_meta is None:
                reference_meta = processor.band_meta

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
            composite_array = VegetationIndexProcessor.create_temporal_composite(
                index_arrays, method='median'
            )
        else:
            composite_array = index_arrays[0]

        # Calculate statistics
        self.update_state(state='PROGRESS', meta={'progress': 80, 'message': 'Calculating statistics'})
        processor = VegetationIndexProcessor({})
        processor.band_meta = reference_meta

        geometry_mask = None
        parcel_geojson = job.parameters.get('bounds') if job.parameters else None
        if parcel_geojson and isinstance(parcel_geojson, dict) and 'coordinates' in parcel_geojson:
            try:
                geometry_mask = processor.create_geometry_mask(parcel_geojson)
                logger.info("Geometry mask applied")
            except Exception as mask_err:
                logger.warning("Could not create geometry mask: %s", mask_err)

        statistics = processor.calculate_statistics(composite_array, mask=geometry_mask)

        # Vectorize for offline sync
        self.update_state(state='PROGRESS', meta={'progress': 85, 'message': 'Vectorizing index'})
        vector_geojson = None
        try:
            vector_geojson = processor.vectorize_index(composite_array, index_type, mask=geometry_mask)
        except Exception as v_err:
            logger.warning("Could not vectorize index: %s", v_err)

        # Apply mask to raster
        if geometry_mask is not None:
            composite_array = np.where(geometry_mask, np.nan, composite_array)

        # Save raster
        self.update_state(state="PROGRESS", meta={"progress": 90, "message": "Saving results"})

        primary_scene = scenes_to_process[0]

        if is_temporal_composite:
            remote_raster_path = (
                f"{tenant_id}/entities/{job.entity_id or 'unknown'}/"
                f"composites/{job.id}/{index_type}.tif"
            )
        else:
            remote_raster_path = (
                f"{tenant_id}/entities/{job.entity_id or 'unknown'}/"
                f"scenes/{primary_scene.id}/{index_type}.tif"
            )

        local_dir = Path(f"/tmp/vegetation_indices/{tenant_id}/{job_id}")
        local_dir.mkdir(parents=True, exist_ok=True)
        local_output_path = local_dir / f"{index_type}.tif"

        processor.save_index_raster(composite_array, str(local_output_path))

        # Convert to COG
        local_cog_path = local_dir / f"{index_type}_cog.tif"
        try:
            from rio_cogeo.cogeo import cog_translate
            from rio_cogeo.profiles import cog_profiles

            dst_profile = cog_profiles.get("deflate")
            cog_translate(
                str(local_output_path),
                str(local_cog_path),
                dst_profile,
                in_memory=True,
                quiet=True,
            )
            logger.info("COG conversion successful for %s", index_type)
            upload_path = str(local_cog_path)
        except Exception as cog_err:
            logger.warning("COG conversion failed (%s), uploading raw GeoTIFF", cog_err)
            upload_path = str(local_output_path)

        # Upload to MinIO/S3
        storage.upload_file(upload_path, remote_raster_path, bucket_name)

        # Persist results to Orion-LD
        _persist_results(
            tenant_id=tenant_id,
            job=job,
            index_type=index_type,
            formula=formula,
            statistics=statistics,
            remote_raster_path=remote_raster_path,
            primary_scene=primary_scene,
        )

        # Mark job as completed
        job_result = {
            'index_type': index_type,
            'index_key': result_index_key or (f"custom:{formula_id}" if index_type == 'CUSTOM' and formula_id else index_type),
            'is_custom': index_type == 'CUSTOM',
            'formula_id': formula_id,
            'formula_name': formula_name,
            'statistics': statistics,
            'raster_path': remote_raster_path,
            'source_image_count': source_image_count,
            'is_composite': is_temporal_composite,
            'scene_id': str(primary_scene.id),
            'sensing_date': primary_scene.sensing_date.isoformat() if primary_scene.sensing_date else None,
        }

        if is_temporal_composite:
            job_result['date_range'] = {'start': start_date, 'end': end_date}

        job.mark_completed(job_result)
        db.commit()

        # Band cleanup: delete raw bands from tenant bucket after last index calc
        if not is_temporal_composite and primary_scene:
            _decrement_and_cleanup_bands(primary_scene.scene_id)

        mode_str = "temporal composite" if is_temporal_composite else "single scene"
        logger.info(
            "Index %s calculated successfully (%s, %d scenes)",
            index_type, mode_str, source_image_count,
        )

    except Exception as e:
        logger.error(f"Error calculating index: {str(e)}", exc_info=True)
        if job:
            job.mark_failed(str(e), str(e.__traceback__))
            db.commit()
            # Release the Redis idempotency lock so the user can retry without
            # waiting 24h. Without this, a single failure would mark every
            # subsequent calc_index for the same (tenant,parcel,index,date)
            # as 'skipped:true' until the TTL expires (root cause of the
            # 11/11-skipped NDRE the audit surfaced 2026-05-07).
            try:
                params = job.parameters or {}
                idx_for_lock = params.get('index_type') or (job.result or {}).get('index_type')
                sensing_for_lock = (
                    (job.result or {}).get('sensing_date')
                    or params.get('sensing_date')
                )
                if idx_for_lock and sensing_for_lock and job.entity_id:
                    _release_idempotency(
                        tenant_id, job.entity_id, idx_for_lock, sensing_for_lock,
                    )
            except Exception:
                pass
        raise
    finally:
        # Clean up temporary files. ONLY the job-specific indices dir is safe
        # to wipe here — the tenant-level processing dir (/tmp/vegetation_processing/<tenant>)
        # is shared by concurrent calc_index tasks for the same scene, and
        # erasing it caused 'Band file not found: B08.tif' races between
        # NDVI/EVI/SAVI/GNDVI/NDRE running on the same worker. Bands accumulate
        # under /tmp until the pod recycles (worker_max_tasks_per_child=50).
        import shutil
        job_indices_dir = Path(f"/tmp/vegetation_indices/{tenant_id}/{job_id}")
        if job_indices_dir.exists():
            try:
                shutil.rmtree(job_indices_dir, ignore_errors=True)
            except Exception as cleanup_err:
                logger.debug("Temp cleanup failed for %s: %s", job_indices_dir, cleanup_err)
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
            job.end_date.isoformat() if job.end_date else None,
        )

    finally:
        db.close()
