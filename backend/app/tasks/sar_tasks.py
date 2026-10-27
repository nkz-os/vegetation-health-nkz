"""
Celery tasks for Sentinel-1 SAR processing — integrated into the subscription pipeline.

Single-task design: download VV+VH bands and compute zonal stats in one Celery task.
Creates two VegetationJob records (job_type=calculate_index, index_type=SAR-VV/SAR-VH)
so they appear automatically in the frontend's available_indices.
"""

import logging
import os
from pathlib import Path
import tempfile
import uuid
from datetime import date, datetime, timezone
from typing import Dict, Any, Optional

import numpy as np
from rasterio.transform import from_gcps
import requests

from app.celery_app import celery_app
from app.models import VegetationJob, VegetationScene
from app.database import get_db_session
from app.services.copernicus_client import CopernicusDataSpaceClient
from app.services.platform_credentials import get_copernicus_credentials_with_fallback
from app.services.fiware_integration import upsert_eo_product
from app.services.sar_smi_processor import DEFAULT_INCIDENCE_DEG
from app.services.storage import create_storage_service, generate_tenant_bucket_name

logger = logging.getLogger(__name__)

FIELD_OPS_URL = "http://field-operations-api-service:8420/internal/suggested-operation"


def _clip_sar_to_parcel(
    src_path: str,
    dst_path: str,
    parcel_geom,
    pad_deg: float = 0.005,
    nodata: float = 0,
) -> None:
    """Warp a GCP-only SAR band to EPSG:4326, masked to the parcel polygon.

    The raw Sentinel-1 GRD band is NOT geocoded (crs=None, identity transform,
    ~210 GCPs in EPSG:4326). This warps it via the GCPs to a georeferenced
    EPSG:4326 raster covering only the parcel (plus a small pad), so the COG is
    parcel-sized instead of the full ~26410x16668 scene (which is identical and
    redundant across parcels in the same scene). Pixels outside the parcel
    polygon are set to nodata (0) so the map layer follows the parcel shape,
    like the optical indices, instead of painting its bounding rectangle.
    `parcel_geom` is a shapely geometry in EPSG:4326.
    """
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import reproject, Resampling, calculate_default_transform

    left, bottom, right, top = parcel_geom.bounds
    left -= pad_deg
    bottom -= pad_deg
    right += pad_deg
    top += pad_deg

    with rasterio.open(src_path) as src:
        gcps, gcps_crs = src.gcps
        dst_crs = "EPSG:4326"
        with WarpedVRT(
            src,
            src_crs=gcps_crs,
            crs=dst_crs,
            resampling=Resampling.bilinear,
            src_nodata=nodata,
            nodata=nodata,
        ) as vrt:
            dst_transform, dst_width, dst_height = calculate_default_transform(
                dst_crs, dst_crs, vrt.width, vrt.height,
                left=left, bottom=bottom, right=right, top=top,
            )
            kwargs = {
                "driver": "GTiff",
                "height": dst_height,
                "width": dst_width,
                "count": 1,
                "dtype": src.dtypes[0],
                "crs": dst_crs,
                "transform": dst_transform,
                "nodata": nodata,
            }
            with rasterio.open(dst_path, "w+", **kwargs) as dst:
                reproject(
                    source=rasterio.band(vrt, 1),
                    destination=rasterio.band(dst, 1),
                    src_transform=vrt.transform,
                    src_crs=dst_crs,
                    src_nodata=nodata,
                    dst_transform=dst_transform,
                    dst_crs=dst_crs,
                    dst_nodata=nodata,
                    resampling=Resampling.bilinear,
                )
                # all_touched keeps edge pixels that partially overlap the
                # parcel, matching the optical pipeline's boundary handling.
                outside = geometry_mask(
                    [parcel_geom.__geo_interface__],
                    out_shape=(dst_height, dst_width),
                    transform=dst_transform,
                    all_touched=True,
                )
                band = dst.read(1)
                band[outside] = nodata
                dst.write(band, 1)


SAR_DB_NODATA = -9999.0  # 0 dB is a real backscatter value, so it cannot mark "no data"
_WINDOW_PAD_PX = 20


def _calibrated_parcel(band_path: str, calibration_path: str, parcel_geom, workdir: str):
    """Calibrated parcel backscatter: (zonal stats in dB, georeferenced EPSG:4326 dB raster).

    The GRD band's digital numbers become sigma nought with the product's calibration
    LUT (``sar_calibration``), over the parcel's pixel window only. Statistics come from
    the radar-geometry pixels inside the parcel (mean in linear power). The window is then
    warped through its GCPs and masked to the parcel for the map layer and the SMI; pixels
    without signal or outside the parcel are ``SAR_DB_NODATA``. Returns (stats, None) when
    the parcel covers no pixel.
    """
    import rasterio
    from rasterio.control import GroundControlPoint
    from rasterio.features import rasterize
    from rasterio.windows import Window

    from app.services import sar_calibration

    lut = sar_calibration.parse_calibration(Path(calibration_path).read_bytes())
    with rasterio.open(band_path) as src:
        grid_transform, grid_crs = _raster_grid(src)
        mask = rasterize(
            [(_geom_in_raster_crs(parcel_geom, grid_crs), 1)],
            out_shape=(src.height, src.width), transform=grid_transform, fill=0, dtype="uint8",
        )
        rows, cols = np.nonzero(mask)
        if rows.size == 0:
            return sar_calibration.zonal_db(np.array([])), None
        r0, r1 = max(int(rows.min()) - _WINDOW_PAD_PX, 0), min(int(rows.max()) + _WINDOW_PAD_PX, src.height - 1)
        c0, c1 = max(int(cols.min()) - _WINDOW_PAD_PX, 0), min(int(cols.max()) + _WINDOW_PAD_PX, src.width - 1)
        dn = src.read(1, window=Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)).astype(np.float64)
        gcps, gcps_crs = src.gcps
    rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
    db = sar_calibration.sigma0_db(dn.ravel(), rr.ravel(), cc.ravel(), lut).reshape(dn.shape)
    stats = sar_calibration.zonal_db(db[mask[r0:r1 + 1, c0:c1 + 1] == 1])
    if stats["pixel_count"] == 0:
        return stats, None

    stem = Path(band_path).stem
    window_path = os.path.join(workdir, f"{stem}_db_window.tif")
    shifted = [GroundControlPoint(row=g.row - r0, col=g.col - c0, x=g.x, y=g.y, z=g.z) for g in gcps]
    with rasterio.open(window_path, "w", driver="GTiff", height=dn.shape[0], width=dn.shape[1],
                       count=1, dtype="float32", nodata=SAR_DB_NODATA) as dst:
        dst.write(np.where(np.isfinite(db), db, SAR_DB_NODATA).astype(np.float32), 1)
        dst.gcps = (shifted, gcps_crs)
    parcel_path = os.path.join(workdir, f"{stem}_db_parcel.tif")
    _clip_sar_to_parcel(window_path, parcel_path, parcel_geom, nodata=SAR_DB_NODATA)
    return stats, parcel_path


def _zone_sigma0_stats(parcel_raster_path: str, zone_geom) -> Optional[Dict[str, Any]]:
    """sigma0 dB statistics of one zone, read from the already calibrated parcel raster.

    Reuses the parcel's calibrated, georeferenced dB window (no per-zone
    calibration or warp). None when the zone has fewer than MIN_ZONE_PIXELS
    valid pixels or its valid fraction is under MIN_VALID_FRACTION.
    """
    import rasterio
    from rasterio.features import geometry_mask

    from app.services import sar_calibration
    from app.services.zone_products import MIN_VALID_FRACTION, MIN_ZONE_PIXELS

    with rasterio.open(parcel_raster_path) as src:
        inside = ~geometry_mask([zone_geom.__geo_interface__], out_shape=(src.height, src.width),
                                transform=src.transform, all_touched=True)
        values = src.read(1)
    zone_pixels = int(inside.sum())
    if zone_pixels == 0:
        return None
    picked = values[inside]
    stats = sar_calibration.zonal_db(picked[picked != SAR_DB_NODATA])
    valid_fraction = stats["pixel_count"] / zone_pixels
    if stats["pixel_count"] < MIN_ZONE_PIXELS or valid_fraction < MIN_VALID_FRACTION:
        return None
    return {**stats, "valid_fraction": valid_fraction}


def _publish_zone_sar_products(tenant_id: str, entity_id: str, calibrated_paths: Dict[str, str],
                               acq_date) -> int:
    """Per-management-zone VV/VH EOProducts. Never raises; one zone failing skips only it.

    sarMoisture / sarChangeFlag are NOT published per zone: the SMI needs the
    VV raster and DEM, and the change flag needs per-zone history.
    """
    from shapely.geometry import shape as shapely_shape

    from app.services.fiware_integration import upsert_eo_zone_product
    from app.services.zone_products import fetch_management_zones

    published = 0
    parcel_short = entity_id.split(":")[-1][:10]
    for zone in fetch_management_zones(tenant_id, entity_id):
        try:
            zone_geom = shapely_shape(zone["geometry"])
            vv = _zone_sigma0_stats(calibrated_paths["VV"], zone_geom)
            vh = _zone_sigma0_stats(calibrated_paths["VH"], zone_geom)
            if vv is None or vh is None:
                logger.info("SAR zone %s skipped (too few valid pixels)", zone["zone_id"])
                continue
            stats = {"pixel_count": min(vv["pixel_count"], vh["pixel_count"]),
                     "valid_fraction": min(vv["valid_fraction"], vh["valid_fraction"])}
            if upsert_eo_zone_product(tenant_id, parcel_short, zone, acq_date,
                                      vv["mean"], vh["mean"], stats):
                published += 1
        except Exception as exc:
            logger.warning("SAR zone %s product failed for %s: %s", zone["zone_id"], entity_id, exc)
    return published


def _upload_sar_cog(
    local_tiff: str,
    tenant_id: str,
    entity_id: str,
    scene_id: str,
    pol: str,
    sensing_date_str: str,
    parcel_geom=None,
) -> Optional[str]:
    """Persist a downloaded SAR band to MinIO as a COG.

    The raw band lives in a ``TemporaryDirectory`` that is deleted when the task
    ends, so it must be uploaded here (and its object key stored as
    ``raster_path``) for the tile endpoints to render the map layer. When
    ``parcel_geom`` (shapely, EPSG:4326) is given the band is clipped and masked
    to the parcel before COG-ifying.
    Returns the S3 object key, or ``None`` on failure (job degrades to stats-only).
    """
    bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(tenant_id)
    date_key = sensing_date_str or "unknown"
    remote_path = f"{tenant_id}/entities/{entity_id}/sar/{date_key}/{scene_id}-{pol}.tif"

    try:
        with tempfile.TemporaryDirectory() as td:
            cog_path = os.path.join(td, "cog.tif")
            try:
                from rio_cogeo.cogeo import cog_translate
                from rio_cogeo.profiles import cog_profiles
                source = local_tiff
                if parcel_geom is not None:
                    clipped = os.path.join(td, "clipped.tif")
                    _clip_sar_to_parcel(local_tiff, clipped, parcel_geom)
                    source = clipped
                # in_memory=False streams to disk: a full S1 IW scene is ~880 MB
                # and would OOM the worker if built in a MemoryFile. cog_translate
                # georeferences the (clipped) GRD band into EPSG:4326 for tiles.
                cog_translate(source, cog_path, cog_profiles.get("deflate"), in_memory=False, quiet=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("SAR COG conversion failed for %s (%s); uploading raw GeoTIFF", pol, e)
                cog_path = local_tiff

            storage = create_storage_service(
                storage_type=os.getenv("STORAGE_TYPE", "s3"),
                default_bucket=bucket,
            )
            storage.upload_file(cog_path, remote_path, bucket)
        return remote_path
    except Exception as e:  # noqa: BLE001
        logger.warning("SAR COG upload failed for %s (%s): %s", pol, scene_id, e)
        return None


def _raster_grid(src):
    """Return the (transform, crs) a 4326 geometry must be rasterized against.

    Sentinel-1 GRD products are NOT geocoded. Verified against a real product
    (26410x16668): `crs` is None, `transform` is the identity, and `bounds` are
    plain pixel indices — the georeferencing travels as ~210 GCPs in EPSG:4326.
    Rasterizing a parcel against that identity sends it to pixel (-2, 42), which
    is why every SAR run reported "No valid pixels".

    An affine fitted to the GCPs is an approximation of the full terrain
    correction, but it puts the parcel on the right pixels: for the product above
    the centroid lands at row 7154, col 24588 and the mask selects real pixels.
    """
    if src.crs is not None:
        return src.transform, src.crs
    gcps, gcps_crs = src.gcps
    if gcps:
        return from_gcps(gcps), gcps_crs
    return src.transform, None


def _geom_in_raster_crs(geom, raster_crs):
    """Return `geom` (EPSG:4326) in the raster's CRS, ready for rasterize().

    Sentinel-1 GRD products are projected in UTM metres while NGSI-LD parcel
    geometry is in degrees. Rasterizing the unprojected geometry against the
    raster transform selects no pixels at all, which the caller used to treat as
    "no valid pixels" and skip — leaving polarizations_computed empty on a job
    that reported success. Same idiom as lst_processor / scl_validation.

    A raster already in 4326, or one carrying no CRS, is left untouched: guessing
    a projection would be worse than not converting.
    """
    if raster_crs is None or str(raster_crs) == "EPSG:4326":
        return geom
    from pyproj import Transformer
    from shapely.ops import transform as shp_transform

    transformer = Transformer.from_crs("EPSG:4326", raster_crs, always_xy=True)
    return shp_transform(transformer.transform, geom)


def _notify_field_operations(
    tenant_id: str,
    parcel_id: str,
    operation_type: str,
    confidence: float,
    delta_vv_db: float,
    sensing_date_str: str,
    scene_id: str,
) -> bool:
    """POST suggested operation to field-operations. Fail-open: logs warning."""
    secret = os.getenv("INTERNAL_SERVICE_SECRET", "")
    if not secret:
        logger.warning("INTERNAL_SERVICE_SECRET not set — field-ops notification skipped")
        return False
    try:
        resp = requests.post(
            FIELD_OPS_URL,
            json={
                "tenant_id": tenant_id,
                "parcel_id": parcel_id,
                "operation_type": operation_type,
                "confidence": confidence,
                "sensing_date": sensing_date_str,
                "delta_vv_db": delta_vv_db,
                "scene_id": scene_id,
            },
            headers={
                "X-Internal-Service-Secret": secret,
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code in (200, 201, 409):
            logger.info(
                "Notified field-ops: %s on %s (%d)",
                operation_type, parcel_id, resp.status_code,
            )
            return True
        logger.warning("field-ops notification returned %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        logger.warning("field-ops notification failed for %s: %s", parcel_id, exc)
        return False


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

        # Inherit the parent SAR job's crop_season_id so the SAR calculate_index
        # jobs land under the season, not the legacy no-season bucket.
        _child_season_id = parameters.get("crop_season_id")
        if _child_season_id:
            try:
                _child_season_id = uuid.UUID(str(_child_season_id))
            except (ValueError, TypeError):
                _child_season_id = None

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
            calibration_paths = copernicus.download_s1_calibration(
                scene_id, polarizations=["vv", "vh"], output_dir=tmpdir,
            )
            calibrated_paths: dict[str, str] = {}

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
                    calibration_path = calibration_paths.get(pol.lower())
                    if not calibration_path:
                        # Uncalibrated digital numbers are not backscatter: never publish them as dB.
                        raise ValueError(f"calibration annotation unavailable for {pol}")
                    raw, parcel_raster = _calibrated_parcel(raster_path, calibration_path, geom, tmpdir)
                    if raw["pixel_count"] == 0:
                        logger.warning("No valid pixels for %s in %s", pol, entity_id)
                        continue
                    mean_val = raw["mean"]
                    stats = {
                        "mean": round(mean_val, 4),
                        "min": round(raw["min"], 4),
                        "max": round(raw["max"], 4),
                        "pixel_count": raw["pixel_count"],
                        "unit": "dB",
                    }
                    stats_by_pol[pol] = stats
                    calibrated_paths[pol] = parcel_raster

                    # ── Create calculate_index VegetationJob (visible in frontend) ──
                    calc_job = VegetationJob(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        entity_id=entity_id,
                        job_type="calculate_index",
                        status="running",
                        parameters={"scene_id": scene_id, "index_type": f"SAR-{pol}"},
                        crop_season_id=_child_season_id,
                    )
                    db.add(calc_job)
                    db.commit()

                    # Persist the band to MinIO as a COG before the local temp
                    # dir is deleted, and store the object key as raster_path.
                    s3_path = _upload_sar_cog(
                        parcel_raster, tenant_id, entity_id, scene_id, pol, sensing_date_str,
                    )

                    completed_result = {
                        "index_type": f"SAR-{pol}",
                        "index_key": f"SAR-{pol}",
                        "statistics": stats,
                        "scene_id": str(scene_record.id),
                        "sensing_date": sensing_date_str,
                        "source_image_count": 1,
                        "is_composite": False,
                    }
                    if s3_path:
                        completed_result["raster_path"] = s3_path
                    calc_job.mark_completed(completed_result)
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
                        crop_season_id=_child_season_id,
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

                # Per-zone products from the same calibrated rasters (additive).
                try:
                    if calibrated_paths.get("VV") and calibrated_paths.get("VH"):
                        _publish_zone_sar_products(tenant_id, entity_id, calibrated_paths, acq_date)
                except Exception as exc:
                    logger.warning("SAR zone products skipped for %s: %s", entity_id, exc)

                # ── SMI (Soil Moisture Index) from VV + DEM ──────────
                try:
                    from app.services.sar_smi_processor import compute_smi
                    from app.services.fiware_integration import upsert_eo_smi
                    smi_stats = compute_smi(
                        vv_path=calibrated_paths["VV"],
                        geometry_geojson=parcel_geometry,
                        orbit_direction=parameters.get("orbit_direction", "ASCENDING"),
                        nominal_incidence_deg=parameters.get(
                            "incidence_deg", DEFAULT_INCIDENCE_DEG
                        ),
                    )
                    if smi_stats.get("pixel_count", 0) > 0:
                        sdate = sensing_dt or date.today()
                        smi_eid = upsert_eo_smi(
                            tenant_id=tenant_id,
                            parcel_id=entity_id,
                            statistics=smi_stats,
                            sensing_date=sdate,
                            scene_id=scene_id,
                            incidence_deg=parameters.get("incidence_deg"),
                        )
                        if smi_eid:
                            logger.info(
                                "Published SMI %.3f for %s",
                                smi_stats["mean"], entity_id,
                            )
                except Exception as exc:
                    logger.warning("SMI computation skipped for %s: %s", entity_id, exc)

            # ── SAR Change Detection ──────────────────────────────────
            try:
                from app.services.sar_change_detector import detect_change
                from app.services.fiware_integration import upsert_eo_change_flag
                from nkz_platform_sdk import SyncOrionClient

                # Query previous EOProduct for ΔVV comparison
                orion = SyncOrionClient(tenant_id)
                try:
                    prev_entities = orion.query_entities(
                        type="EOProduct",
                        q=f'hasAgriParcel=="{entity_id}"|refAgriParcel=="{entity_id}";productType=="GRD"',
                        limit=2,
                        options="keyValues",
                    )
                except Exception as exc:
                    logger.warning("Previous EOProduct lookup failed: %s", exc)
                    prev_entities = []
                # Pick the one before current (if any)
                prev_vv = None
                prev_ndvi = None
                prev_date = None
                for pe in (prev_entities if isinstance(prev_entities, list) else []):
                    sd = str(pe.get("sensingDate", ""))
                    if sd and sd < (sensing_date_str or ""):
                        prev_vv = float(pe.get("backscatterVV", 0)) if pe.get("backscatterVV") else None
                        prev_date = sd
                        break

                # NDVI check: latest EOProduct with ndvi
                try:
                    ndvi_entities = orion.query_entities(
                        type="EOProduct",
                        q=f'hasAgriParcel=="{entity_id}"|refAgriParcel=="{entity_id}"',
                        limit=1,
                        options="keyValues",
                    )
                except Exception as exc:
                    logger.warning("NDVI lookup failed: %s", exc)
                    ndvi_entities = []
                ndvi_current = None
                if isinstance(ndvi_entities, list) and ndvi_entities:
                    for ne in ndvi_entities:
                        n = ne.get("ndvi")
                        if n is not None:
                            ndvi_current = float(n) if isinstance(n, (int, float)) else None
                            break

                days_prev = None
                if prev_date and sensing_date_str:
                    try:
                        from datetime import datetime as dt
                        d1 = dt.fromisoformat(sensing_date_str.replace("Z", "+00:00"))
                        d2 = dt.fromisoformat(str(prev_date).replace("Z", "+00:00"))
                        days_prev = abs((d1 - d2).days)
                    except (ValueError, TypeError):
                        pass

                current_vv = stats_by_pol.get("VV", {}).get("mean")
                if current_vv is not None:
                    ch = detect_change(
                        vv_current_db=float(current_vv),
                        vv_previous_db=prev_vv,
                        ndvi_current=ndvi_current,
                        ndvi_previous=prev_ndvi,
                        days_since_previous=days_prev,
                    )
                    if ch and ch.get("change_flag") not in (None, "none"):
                        # Publish flag on EOProduct
                        sdate = sensing_dt or date.today()
                        upsert_eo_change_flag(
                            tenant_id=tenant_id,
                            parcel_id=entity_id,
                            change_flag=ch["change_flag"],
                            confidence=ch["confidence"],
                            delta_vv=ch["delta_vv"],
                            sensing_date=sdate,
                            scene_id=scene_id,
                        )
                        # Webhook field-operations
                        _notify_field_operations(
                            tenant_id=tenant_id,
                            parcel_id=entity_id,
                            operation_type=ch["change_flag"],
                            confidence=ch["confidence"],
                            delta_vv_db=ch["delta_vv"],
                            sensing_date_str=sensing_date_str or "",
                            scene_id=scene_id,
                        )
            except Exception as exc:
                logger.warning("SAR change detection skipped for %s: %s", entity_id, exc)

            # ── Close the download job ────────────────────────────────
            job_result = {
                "scene_id": scene_id,
                "sensing_date": sensing_date_str,
                "bands_downloaded": list(band_paths.keys()),
                "polarizations_computed": list(stats_by_pol.keys()),
            }
            if stats_by_pol:
                download_job.mark_completed(job_result)
            else:
                # Bands on disk but nothing measured off them. This used to be
                # reported as success, so the pipeline looked healthy while every
                # run produced polarizations_computed: [] and no EOProduct.
                download_job.mark_failed(
                    "Bands downloaded but no polarization yielded statistics "
                    f"(bands={list(band_paths.keys())}) — see the per-polarization "
                    "warnings above for whether the raster was missing or the mask "
                    "selected no pixels"
                )
                download_job.result = job_result
            db.commit()

    except Exception as e:
        logger.warning("SAR download failed (will retry): %s", e)
        if download_job:
            download_job.mark_failed(str(e))
            db.commit()
        raise self.retry(exc=e)

    finally:
        db.close()
