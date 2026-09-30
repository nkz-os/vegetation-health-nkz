"""
Celery task for building historical NDVI baseline per parcel.

Ephemeral processing: downloads Sentinel-2 bands to /tmp, calculates zonal
statistics over the parcel geometry, writes an EOProduct to Orion-LD,
then discards the bands. No rasters are stored in MinIO.
"""
import logging
import os
import tempfile
from datetime import date, timedelta

from app.celery_app import celery_app
from app.services.fiware_integration import upsert_eo_index

logger = logging.getLogger(__name__)

BAND_MAP = {
    "NDVI": ["B04", "B08"],
    "GNDVI": ["B03", "B08"],
    "NDRE": ["B8A", "B08"],
    "SAVI": ["B04", "B08"],
    "EVI": ["B02", "B04", "B08"],
}

# Formula per index lives in VegetationIndexProcessor; each entry's bands must
# match what that method loads (BAND_MAP above).
_INDEX_CALCULATORS = {
    "NDVI": "calculate_ndvi",
    "GNDVI": "calculate_gndvi",
    "NDRE": "calculate_ndre",
    "SAVI": "calculate_savi",
    "EVI": "calculate_evi",
}


def index_bands(index: str) -> list:
    """Bands to download for `index`. Unknown indices are rejected, never
    silently computed as NDVI and stored under another index's name."""
    if index not in BAND_MAP:
        raise ValueError(f"Unsupported index for historical baseline: {index}")
    return BAND_MAP[index]


def _get_parcel_geometry(tenant_id: str, entity_id: str) -> tuple:
    """Fetch parcel geometry from Orion-LD. Returns (geom_dict, bbox_list) or raises."""
    from nkz_platform_sdk import SyncOrionClient

    orion = SyncOrionClient(tenant_id)
    # get_entity has no attrs filter; fetching the whole parcel costs a little
    # more payload and removes a call that never worked.
    from app.services.fiware_integration import orion_get_entity
    entity = orion_get_entity(orion, entity_id)
    if entity is None:
        raise ValueError(f"Parcel {entity_id} not found in Orion-LD")
    loc = entity.get("location", {})
    geom = loc.get("value") or loc
    if not geom or "coordinates" not in geom:
        raise ValueError("Parcel has no location geometry")

    from shapely.geometry import shape
    geom_obj = shape(geom)
    bbox = list(geom_obj.bounds)

    # STAC API needs simple Polygon (not MultiPolygon)
    if geom_obj.geom_type == "MultiPolygon":
        largest = max(geom_obj.geoms, key=lambda g: g.area)
        intersects = largest.__geo_interface__
    else:
        intersects = geom_obj.__geo_interface__

    return intersects, bbox


def _process_window(
    tenant_id: str,
    entity_id: str,
    intersects: dict,
    bbox: list,
    copernicus_client,
    window_start: date,
    window_end: date,
    index: str,
    cloud_threshold: float,
    required_bands: list,
) -> bool:
    """Process a single sensing window: search, download, calc, persist.

    Returns True if a record was created, False when the window has no usable
    scene or no valid pixels. Raster processing errors propagate to the caller.
    """
    from app.services.processor import VegetationIndexProcessor

    # Search for best scene in window
    scenes = copernicus_client.search_scenes(
        intersects=intersects,
        start_date=window_start,
        end_date=window_end,
        cloud_cover_lte=cloud_threshold,
        limit=5,
    )
    if not scenes:
        return False

    best = min(scenes, key=lambda s: (s.get("cloud_cover", 100), s["sensing_date"]))

    # Download bands ephemerally to /tmp
    with tempfile.TemporaryDirectory() as tmpdir:
        band_paths = copernicus_client.download_scene_bands(
            scene_id=best["id"],
            bands=required_bands,
            output_dir=tmpdir,
        )

        if not band_paths:
            return False

        missing = [b for b in required_bands if not band_paths.get(b)]
        if missing:
            logger.warning("Scene %s missing bands %s for %s", best["id"], missing, index)
            return False

        # The processor crops to the parcel bbox and rasterizes the parcel in
        # the raster's CRS: Sentinel-2 bands are UTM, the parcel is EPSG:4326.
        processor = VegetationIndexProcessor(band_paths, bbox=bbox)
        index_array = getattr(processor, _INDEX_CALCULATORS[index])()
        geometry_mask = processor.create_geometry_mask(intersects)
        statistics = processor.calculate_statistics(index_array, mask=geometry_mask)

        if statistics["pixel_count"] == 0:
            return False

    try:
        sensing_dt = date.fromisoformat(best["sensing_date"])
    except (ValueError, TypeError):
        sensing_dt = window_start

    # Persist to Orion-LD as EOProduct
    upsert_eo_index(
        tenant_id=tenant_id,
        parcel_id=entity_id if entity_id.startswith("urn:ngsi-ld:AgriParcel:") else f"urn:ngsi-ld:AgriParcel:{entity_id}",
        index_type=index,
        statistics=statistics,
        sensing_date=sensing_dt,
    )

    return True


@celery_app.task(
    bind=True,
    name="vegetation.build_historical_baseline",
    max_retries=1,
    default_retry_delay=3600,
    soft_time_limit=7200,
)
def build_historical_baseline(
    self,
    tenant_id: str,
    entity_id: str,
    years: int = 5,
    index: str = "NDVI",
    window_days: int = 20,
    cloud_threshold: float = 30.0,
):
    """Build historical index baseline for a parcel.

    For each year going back, divides the year into windows of window_days,
    searches for the best Sentinel-2 scene in each window, calculates zonal
    statistics over the parcel, and writes an EOProduct to Orion-LD.

    Ephemeral: bands are downloaded to /tmp and discarded after processing.
    No rasters are stored in MinIO.
    """
    from app.services.copernicus_client import CopernicusDataSpaceClient
    from app.services.platform_credentials import get_copernicus_credentials_with_fallback

    required_bands = index_bands(index)
    today = date.today()

    try:
        # 1. Get parcel geometry
        intersects, bbox = _get_parcel_geometry(tenant_id, entity_id)

        # 2. Init Copernicus client
        creds = get_copernicus_credentials_with_fallback()
        copernicus = CopernicusDataSpaceClient()
        if creds:
            copernicus.set_credentials(creds["client_id"], creds["client_secret"])

        records_created = 0
        windows_failed = 0

        # 3. Iterate years backwards
        for year_offset in range(years):
            year = today.year - year_offset
            year_start = date(year, 1, 1)

            if year_start > today:
                continue

            year_end = min(date(year, 12, 31), today)

            # 4. Divide year into windows of window_days
            current = year_start
            while current <= year_end:
                window_end = min(current + timedelta(days=window_days - 1), year_end)

                self.update_state(
                    state="PROGRESS",
                    meta={
                        "progress": int((year_offset / years) * 100),
                        "message": f"Processing {year} window {current.isoformat()}..{window_end.isoformat()}",
                    },
                )

                try:
                    if _process_window(
                        tenant_id=tenant_id,
                        entity_id=entity_id,
                        intersects=intersects,
                        bbox=bbox,
                        copernicus_client=copernicus,
                        window_start=current,
                        window_end=window_end,
                        index=index,
                        cloud_threshold=cloud_threshold,
                        required_bands=required_bands,
                    ):
                        records_created += 1
                except Exception:
                    # One bad scene must not abort years of windows, but it must
                    # be visible: a systematic error used to look like "no data".
                    windows_failed += 1
                    logger.exception(
                        "Historical window %s..%s failed for %s (%s)",
                        current.isoformat(), window_end.isoformat(), entity_id, index,
                    )

                current = window_end + timedelta(days=1)

        if windows_failed and records_created == 0:
            raise RuntimeError(
                f"Historical baseline for {entity_id} ({index}): every processed window "
                f"failed ({windows_failed}); see window errors above"
            )

        logger.info(
            "Historical baseline complete for %s: %d records, %d failed windows (%d years, %s)",
            entity_id, records_created, windows_failed, years, index,
        )
        return {
            "records_created": records_created,
            "windows_failed": windows_failed,
            "years": years,
            "index": index,
        }

    except Exception as e:
        logger.error("Historical baseline failed for %s: %s", entity_id, e, exc_info=True)
        raise
