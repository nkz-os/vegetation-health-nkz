"""
Celery task for building historical NDVI baseline per parcel.

Ephemeral processing: downloads Sentinel-2 bands to /tmp, calculates zonal
statistics over the parcel geometry, writes AgriParcelRecord to Orion-LD,
then discards the bands. No rasters are stored in MinIO.
"""
import logging
import os
import tempfile
from datetime import date, timedelta
from typing import Optional

import numpy as np

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

BAND_MAP = {
    "NDVI": ["B04", "B08"],
    "GNDVI": ["B03", "B08"],
    "NDRE": ["B8A", "B08"],
    "SAVI": ["B04", "B08"],
    "EVI": ["B02", "B04", "B08"],
}


def _get_parcel_geometry(tenant_id: str, entity_id: str) -> tuple:
    """Fetch parcel geometry from Orion-LD. Returns (geom_dict, bbox_list) or raises."""
    import httpx

    orion_url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
    from nkz_platform_sdk import inject_fiware_headers
    headers = inject_fiware_headers(
        {"Accept": "application/json"},
        tenant=tenant_id,
        has_context_in_body=False,
    )

    with httpx.Client(timeout=10) as client:
        resp = client.get(
            f"{orion_url}/ngsi-ld/v1/entities/{entity_id}?attrs=location",
            headers=headers,
        )
        if resp.status_code != 200:
            raise ValueError(f"Parcel {entity_id} not found in Orion-LD")

        entity = resp.json()
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


def _upsert_agri_parcel_record(
    tenant_id: str,
    entity_id: str,
    sensing_date: str,
    index: str,
    mean_val: float,
    min_val: float,
    max_val: float,
    std_val: float,
    window_days: int,
    year: int,
    doy_start: int,
) -> Optional[str]:
    """Write an AgriParcelRecord entity to Orion-LD."""
    import httpx

    orion_url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
    from nkz_platform_sdk import inject_fiware_headers

    parcel_short = entity_id.split(":")[-1] if ":" in entity_id else entity_id
    record_id = f"urn:ngsi-ld:AgriParcelRecord:{tenant_id}:{parcel_short}:{index.lower()}:{year}-{doy_start}"

    # entity_id may be full URN or short ID
    parcel_urn = entity_id if entity_id.startswith("urn:ngsi-ld:AgriParcel:") else f"urn:ngsi-ld:AgriParcel:{entity_id}"
    entity = {
        "id": record_id,
        "type": "AgriParcelRecord",
        "observedAt": {"type": "Property", "value": sensing_date},
        "hasAgriParcel": {
            "type": "Relationship",
            "object": parcel_urn,
        },
        f"{index.lower()}Mean": {"type": "Property", "value": round(mean_val, 4)},
        f"{index.lower()}Min": {"type": "Property", "value": round(min_val, 4)},
        f"{index.lower()}Max": {"type": "Property", "value": round(max_val, 4)},
        f"{index.lower()}Std": {"type": "Property", "value": round(std_val, 4)},
        "windowSize": {"type": "Property", "value": window_days},
        "year": {"type": "Property", "value": year},
        "@context": "https://nekazari.robotika.cloud/ngsi-ld-context.json",
    }

    headers = inject_fiware_headers(
        {"Content-Type": "application/ld+json"},
        tenant=tenant_id,
        has_context_in_body=True,
    )

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{orion_url}/ngsi-ld/v1/entities",
                json=entity,
                headers=headers,
            )
            if resp.status_code in (201, 204):
                logger.debug("Upserted AgriParcelRecord %s", record_id)
                return record_id
            else:
                logger.warning(
                    "Failed to upsert AgriParcelRecord %s: %s %s",
                    record_id, resp.status_code, resp.text[:200],
                )
                return None
    except Exception as e:
        logger.warning("Orion-LD write failed for %s: %s", record_id, e)
        return None


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

    Returns True if a record was created, False otherwise.
    """
    import rasterio
    from rasterio.mask import mask as rio_mask
    from shapely.geometry import shape

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

        # Read red (B04) and NIR (B08) bands for NDVI computation
        red_band = band_paths.get('B04')
        nir_band = band_paths.get('B08')
        if not red_band or not nir_band:
            logger.warning("Missing B04 or B08 for NDVI computation")
            return False

        try:
            with rasterio.open(red_band) as red_src, rasterio.open(nir_band) as nir_src:
                parcel_shape = shape(intersects)
                red_data, _ = rio_mask(red_src, [parcel_shape], crop=True, nodata=np.nan)
                nir_data, _ = rio_mask(nir_src, [parcel_shape], crop=True, nodata=np.nan)
                red = red_data[0].astype(np.float32)
                nir = nir_data[0].astype(np.float32)
                ndvi = np.where((nir + red) > 0, (nir - red) / (nir + red), np.nan)
                valid = np.isfinite(ndvi)

                if not np.any(valid):
                    return False

                mean_val = float(np.nanmean(ndvi))
                min_val = float(np.nanmin(ndvi))
                max_val = float(np.nanmax(ndvi))
                std_val = float(np.nanstd(ndvi))
        except Exception as e:
            logger.warning("Raster processing failed for %s: %s", best["id"], e)
            return False

    # Calculate year and DOY from sensing date
    try:
        sensing_dt = date.fromisoformat(best["sensing_date"])
    except (ValueError, TypeError):
        sensing_dt = window_start
    year = sensing_dt.year
    doy_start = sensing_dt.timetuple().tm_yday

    # Persist to Orion-LD
    _upsert_agri_parcel_record(
        tenant_id=tenant_id,
        entity_id=entity_id,
        sensing_date=best["sensing_date"],
        index=index,
        mean_val=mean_val,
        min_val=min_val,
        max_val=max_val,
        std_val=std_val,
        window_days=(window_end - window_start).days,
        year=year,
        doy_start=doy_start,
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
    statistics over the parcel, and writes an AgriParcelRecord to Orion-LD.

    Ephemeral: bands are downloaded to /tmp and discarded after processing.
    No rasters are stored in MinIO.
    """
    from app.services.copernicus_client import CopernicusDataSpaceClient
    from app.services.platform_credentials import get_copernicus_credentials_with_fallback

    required_bands = BAND_MAP.get(index, ["B04", "B08"])
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

                current = window_end + timedelta(days=1)

        logger.info(
            "Historical baseline complete for %s: %d records (%d years, %s)",
            entity_id, records_created, years, index,
        )
        return {"records_created": records_created, "years": years, "index": index}

    except Exception as e:
        logger.error("Historical baseline failed for %s: %s", entity_id, e, exc_info=True)
        raise
