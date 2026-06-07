"""
Celery tasks for Sentinel-1 SAR processing — integrated into the subscription pipeline.

Follows the same pattern as Sentinel-2: download → calculate_index → publish.
Results flow through VegetationJob.result with index_type="SAR-VV" / "SAR-VH",
making them automatically visible in the frontend's available_indices.
"""

import logging
import os
import tempfile
import uuid
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Dict, Any

from app.celery_app import celery_app
from app.models import VegetationJob, VegetationScene
from app.database import get_db_session
from app.services.copernicus_client import CopernicusDataSpaceClient
from app.services.platform_credentials import get_copernicus_credentials_with_fallback
from app.services.fiware_integration import upsert_eo_product

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="vegetation.download_sentinel1_scene",
    max_retries=2,
    default_retry_delay=1800,
    soft_time_limit=1800,
)
def download_sentinel1_scene(
    self,
    job_id: str,
    tenant_id: str,
    parameters: Dict[str, Any],
):
    """Download Sentinel-1 GRD VV and VH bands for a scene.

    Triggered by check_and_process_entity when S1 scenes are found for a
    subscribed parcel. After downloading, triggers calculate_sar_backscatter
    for each polarization (SAR-VV, SAR-VH).

    Args:
        job_id: VegetationJob UUID
        tenant_id: Tenant identifier
        parameters: Dict with scene_id, bounds, entity_id, sensing_date
    """
    db = next(get_db_session())
    job = None

    try:
        job = db.query(VegetationJob).filter(
            VegetationJob.id == uuid.UUID(job_id)
        ).first()
        if not job:
            logger.error("SAR job %s not found", job_id)
            return

        job.mark_started()
        job.celery_task_id = self.request.id
        db.commit()

        scene_id = parameters.get("scene_id")
        entity_id = parameters.get("entity_id")
        sensing_date = parameters.get("sensing_date")
        bounds = parameters.get("bounds")

        if not scene_id:
            raise ValueError("scene_id is required for SAR download")

        creds = get_copernicus_credentials_with_fallback()
        if not creds:
            raise ValueError("Copernicus credentials not available")

        copernicus = CopernicusDataSpaceClient()
        copernicus.set_credentials(creds["client_id"], creds["client_secret"])

        self.update_state(
            state="PROGRESS",
            meta={"progress": 20, "message": f"Downloading S1 bands for {scene_id}"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            band_paths = copernicus.download_s1_bands(
                scene_id,
                polarizations=["vv", "vh"],
                output_dir=tmpdir,
            )

            vv_path = band_paths.get("vv", "")
            vh_path = band_paths.get("vh", "")

            if not vv_path and not vh_path:
                raise ValueError(f"No polarization bands downloaded for {scene_id}")

            # Store scene metadata (reuse VegetationScene, no geometry needed for S1)
            sensing_dt = None
            if sensing_date:
                try:
                    sensing_dt = date.fromisoformat(sensing_date)
                except (ValueError, TypeError):
                    pass

            # Create or find VegetationScene for tracking
            scene_record = db.query(VegetationScene).filter(
                VegetationScene.scene_id == scene_id,
                VegetationScene.tenant_id == tenant_id,
            ).first()

            if not scene_record:
                scene_record = VegetationScene(
                    tenant_id=tenant_id,
                    scene_id=scene_id,
                    sensing_date=sensing_dt or date.today(),
                    cloud_coverage="N/A",  # S1 doesn't have cloud cover
                    storage_path="",
                    storage_bucket=None,
                    bands=["vv", "vh"] if (vv_path and vh_path) else (["vv"] if vv_path else ["vh"]),
                    is_valid=True,
                    job_id=job.id,
                )
                db.add(scene_record)
                db.commit()

            job.mark_completed({
                "scene_id": scene_id,
                "sensing_date": sensing_date,
                "bands_downloaded": list(band_paths.keys()),
            })
            db.commit()

            # Trigger calculate jobs for each polarization
            from app.tasks.sar_tasks import calculate_sar_backscatter

            for pol, raster_path in band_paths.items():
                pol_upper = pol.upper()
                calc_job_id = str(uuid.uuid4())
                calc_params = {
                    "scene_id": scene_id,
                    "entity_id": entity_id,
                    "index_type": f"SAR-{pol_upper}",
                    "rar_path": raster_path,
                    "sensing_date": sensing_date,
                    "bounds": bounds,
                }

                calc_job = VegetationJob(
                    id=uuid.UUID(calc_job_id),
                    tenant_id=tenant_id,
                    entity_id=entity_id,
                    job_type="calculate_index",
                    status="pending",
                    parameters=calc_params,
                )
                db.add(calc_job)
                db.commit()

                try:
                    async_result = calculate_sar_backscatter.delay(
                        job_id=calc_job_id,
                        tenant_id=tenant_id,
                        scene_id=str(scene_record.id),
                        index_type=f"SAR-{pol_upper}",
                    )
                    calc_job.celery_task_id = async_result.id
                    db.commit()
                    logger.info(
                        "Triggered SAR calc for %s/%s (job %s)",
                        entity_id, f"SAR-{pol_upper}", calc_job_id,
                    )
                except Exception as e:
                    logger.exception("Failed to enqueue SAR calc %s", calc_job_id)
                    calc_job.status = "failed"
                    calc_job.error_message = str(e)
                    db.commit()

    except Exception as e:
        logger.error("SAR download failed for %s: %s", parameters.get("scene_id", "?"), e, exc_info=True)
        if job:
            job.mark_completed({"error": str(e)})
            db.commit()
        raise self.retry(exc=e)

    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="vegetation.calculate_sar_backscatter",
    max_retries=2,
    default_retry_delay=600,
    soft_time_limit=600,
)
def calculate_sar_backscatter(
    self,
    job_id: str,
    tenant_id: str,
    scene_id: str,
    index_type: str,
):
    """Calculate mean SAR backscatter for a parcel from a downloaded raster.

    Stores result in VegetationJob.result with index_type and statistics,
    making it visible to the frontend. Also publishes EOProduct to Orion-LD.

    Args:
        job_id: VegetationJob UUID
        tenant_id: Tenant identifier
        scene_id: VegetationScene UUID (DB record, not product ID)
        index_type: "SAR-VV" or "SAR-VH"
    """
    import numpy as np
    import rasterio
    from rasterio.features import rasterize
    from shapely.geometry import shape as shapely_shape
    from geoalchemy2.shape import to_shape

    db = next(get_db_session())
    job = None

    try:
        job = db.query(VegetationJob).filter(
            VegetationJob.id == uuid.UUID(job_id)
        ).first()
        if not job:
            logger.error("SAR calc job %s not found", job_id)
            return

        job.mark_started()
        job.celery_task_id = self.request.id
        db.commit()

        # Get the download job results to find the raster path
        raster_path = job.parameters.get("raster_path", "")
        entity_id = job.parameters.get("entity_id", "")
        sensing_date_str = job.parameters.get("sensing_date", "")
        bounds = job.parameters.get("bounds")

        if not raster_path or not os.path.isfile(raster_path):
            raise ValueError(f"Raster file not found: {raster_path}")

        # Get the scene record for metadata
        try:
            scene = db.query(VegetationScene).filter(
                VegetationScene.id == uuid.UUID(scene_id)
            ).first()
        except (ValueError, AttributeError):
            scene = None

        # Get parcel geometry from the subscription (or bounds fallback)
        parcel_geometry = bounds or {}
        parcel_id = entity_id

        # Compute zonal stats
        self.update_state(
            state="PROGRESS",
            meta={"progress": 50, "message": f"Computing {index_type} zonal stats"},
        )

        geom = shapely_shape(parcel_geometry)

        with rasterio.open(raster_path) as src:
            mask = rasterize(
                [(geom, 1)],
                out_shape=(src.height, src.width),
                transform=src.transform,
                fill=0,
                dtype="uint8",
            )
            data = src.read(1).astype(np.float32)
            nodata = src.nodata
            valid = (mask == 1) & (np.isfinite(data))
            if nodata is not None:
                valid = valid & (data != nodata)

            if not np.any(valid):
                raise ValueError(f"No valid pixels in geometry for {index_type}")

            mean_val = float(np.mean(data[valid]))
            min_val = float(np.min(data[valid]))
            max_val = float(np.max(data[valid]))

        statistics = {
            "mean": round(mean_val, 4),
            "min": round(min_val, 4),
            "max": round(max_val, 4),
            "pixel_count": int(np.sum(valid)),
        }

        # Store result in VegetationJob (makes it visible to frontend)
        result = {
            "index_type": index_type,
            "index_key": index_type,
            "statistics": statistics,
            "raster_path": raster_path,  # temporary path — not uploaded to storage
            "scene_id": str(scene.id) if scene else scene_id,
            "sensing_date": sensing_date_str,
            "source_image_count": 1,
            "is_composite": False,
        }
        job.mark_completed(result)
        db.commit()

        logger.info(
            "SAR calc complete: %s mean=%.4f pixels=%d",
            index_type, mean_val, statistics["pixel_count"],
        )

        # Publish EOProduct to Orion-LD (side effect for crop-health pipeline)
        # Only publish when BOTH VV and VH are done — use Redis to track
        try:
            import redis as redis_lib
            redis_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
            r = redis_lib.from_url(redis_url, decode_responses=True)
            key = f"vegetation:sar:{tenant_id}:{entity_id}:{sensing_date_str}"
            r.hset(key, index_type, str(round(mean_val, 4)))
            r.expire(key, 86400)

            # Check if both VV and VH are ready
            vv_str = r.hget(key, "SAR-VV")
            vh_str = r.hget(key, "SAR-VH")
            if vv_str and vh_str:
                vv_mean = float(vv_str)
                vh_mean = float(vh_str)

                # Parse acquisition date
                try:
                    acq_date = datetime.fromisoformat(sensing_date_str + "T00:00:00Z")
                except (ValueError, TypeError):
                    acq_date = datetime.now(timezone.utc)

                eid = upsert_eo_product(
                    tenant_id=tenant_id,
                    parcel_id=entity_id,
                    vv_mean=vv_mean,
                    vh_mean=vh_mean,
                    acquisition_date=acq_date,
                )
                if eid:
                    logger.info("Published EOProduct %s for %s", eid, entity_id)
                    # Clean up Redis key
                    r.delete(key)
        except Exception as e:
            logger.warning("EOProduct publish deferred: %s", e)

    except Exception as e:
        logger.error("SAR calc failed for %s: %s", index_type, e, exc_info=True)
        if job:
            job.mark_completed({"error": str(e)})
            db.commit()
        raise self.retry(exc=e)

    finally:
        db.close()
