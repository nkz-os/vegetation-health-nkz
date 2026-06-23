"""
Celery tasks for Sentinel-1 SAR processing — integrated into the subscription pipeline.

Single-task design: download VV+VH bands and compute zonal stats in one Celery task.
Creates two VegetationJob records (job_type=calculate_index, index_type=SAR-VV/SAR-VH)
so they appear automatically in the frontend's available_indices.
"""

import logging
import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from typing import Dict, Any

import numpy as np

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
    """Download Sentinel-1 GRD bands and compute SAR backscatter for a parcel.

    Downloads VV and VH GeoTIFFs, computes zonal statistics (mean backscatter
    per polarization), and creates two calculate_index VegetationJob records
    (index_type="SAR-VV" / "SAR-VH") so they appear in the frontend.

    Also publishes an EOProduct entity to Orion-LD for the crop-health pipeline.

    Args:
        job_id: UUID of the download_sar VegetationJob
        tenant_id: Tenant identifier
        parameters: {scene_id, bounds, entity_id, sensing_date}
    """
    import rasterio
    from rasterio.features import rasterize
    from shapely.geometry import shape as shapely_shape

    db = next(get_db_session())
    download_job = None

    try:
        download_job = db.query(VegetationJob).filter(
            VegetationJob.id == uuid.UUID(job_id)
        ).first()
        if not download_job:
            logger.error("SAR job %s not found", job_id)
            return

        download_job.mark_started()
        download_job.celery_task_id = self.request.id
        db.commit()

        scene_id = parameters.get("scene_id")
        entity_id = parameters.get("entity_id")
        sensing_date_str = parameters.get("sensing_date", "")
        bounds = parameters.get("bounds")

        if not scene_id or not entity_id:
            raise ValueError("scene_id and entity_id are required")

        # ── Credentials ──────────────────────────────────────────────
        creds = get_copernicus_credentials_with_fallback()
        if not creds:
            raise ValueError("Copernicus credentials not available")

        copernicus = CopernicusDataSpaceClient()
        copernicus.set_credentials(creds["client_id"], creds["client_secret"])

        # ── Download bands ───────────────────────────────────────────
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

            # ── Parse parcel geometry ─────────────────────────────────
            parcel_geometry = bounds or {}
            try:
                geom = shapely_shape(parcel_geometry)
            except Exception:
                logger.warning("Cannot parse geometry for %s, using bbox", entity_id)
                bbox = parameters.get("bbox", [0, 0, 0, 0])
                from shapely.geometry import box
                geom = box(*bbox)

            # ── Scene record for tracking ─────────────────────────────
            sensing_dt = None
            if sensing_date_str:
                try:
                    sensing_dt = date.fromisoformat(sensing_date_str)
                except (ValueError, TypeError):
                    pass

            scene_record = db.query(VegetationScene).filter(
                VegetationScene.scene_id == scene_id,
                VegetationScene.tenant_id == tenant_id,
            ).first()

            if not scene_record:
                scene_record = VegetationScene(
                    tenant_id=tenant_id,
                    scene_id=scene_id,
                    sensing_date=sensing_dt or date.today(),
                    cloud_coverage="N/A",
                    storage_path="",
                    storage_bucket=None,
                    bands=["vv", "vh"] if (vv_path and vh_path) else (["vv"] if vv_path else ["vh"]),
                    is_valid=True,
                    job_id=download_job.id,
                )
                db.add(scene_record)
                db.commit()

            # ── Zonal stats for each polarization ─────────────────────
            stats_by_pol: dict[str, dict] = {}

            for pol, raster_path in [("VV", vv_path), ("VH", vh_path)]:
                if not raster_path or not os.path.isfile(raster_path):
                    logger.warning("Skipping %s — raster not found", pol)
                    continue

                try:
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
                            logger.warning("No valid pixels for %s in %s", pol, entity_id)
                            continue

                        mean_val = float(np.mean(data[valid]))
                        stats = {
                            "mean": round(mean_val, 4),
                            "min": round(float(np.min(data[valid])), 4),
                            "max": round(float(np.max(data[valid])), 4),
                            "pixel_count": int(np.sum(valid)),
                        }
                        stats_by_pol[pol] = stats

                        # ── Create calculate_index VegetationJob (visible in frontend) ──
                        calc_job = VegetationJob(
                            id=uuid.uuid4(),
                            tenant_id=tenant_id,
                            entity_id=entity_id,
                            job_type="calculate_index",
                            status="running",
                            parameters={"scene_id": scene_id, "index_type": f"SAR-{pol}"},
                        )
                        db.add(calc_job)
                        db.commit()

                        calc_job.mark_completed({
                            "index_type": f"SAR-{pol}",
                            "index_key": f"SAR-{pol}",
                            "statistics": stats,
                            "raster_path": raster_path,
                            "scene_id": str(scene_record.id),
                            "sensing_date": sensing_date_str,
                            "source_image_count": 1,
                            "is_composite": False,
                        })
                        db.commit()

                        logger.info(
                            "SAR-%s complete: mean=%.4f pixels=%d for %s",
                            pol, mean_val, stats["pixel_count"], entity_id,
                        )

                except Exception as e:
                    logger.error("SAR zonal stats failed for %s: %s", pol, e)
                    # Create a failed job so frontend knows it was attempted
                    calc_job = VegetationJob(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        entity_id=entity_id,
                        job_type="calculate_index",
                        status="failed",
                        parameters={"scene_id": scene_id, "index_type": f"SAR-{pol}"},
                        error_message=str(e),
                    )
                    db.add(calc_job)
                    db.commit()

            # ── Publish EOProduct when both VV and VH are ready ──────
            if "VV" in stats_by_pol and "VH" in stats_by_pol:
                vv_mean = stats_by_pol["VV"]["mean"]
                vh_mean = stats_by_pol["VH"]["mean"]

                try:
                    acq_date = datetime.fromisoformat(
                        sensing_date_str + "T00:00:00Z" if sensing_date_str else ""
                    )
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
                else:
                    logger.warning("Failed to publish EOProduct for %s", entity_id)

            # ── Mark download job complete ────────────────────────────
            download_job.mark_completed({
                "scene_id": scene_id,
                "sensing_date": sensing_date_str,
                "bands_downloaded": list(band_paths.keys()),
                "polarizations_computed": list(stats_by_pol.keys()),
            })
            db.commit()

    except Exception as e:
        logger.warning("SAR download failed (will retry): %s", e)
        if download_job:
            download_job.mark_failed(str(e))
            db.commit()
        raise self.retry(exc=e)

    finally:
        db.close()
