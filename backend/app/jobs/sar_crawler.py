"""
SAR Crawler — Sentinel-1 GRD ingestion job.

Daily Celery task that:
1. Fetches all AgriParcel entities with geometry from Orion-LD.
2. Searches Copernicus STAC for new Sentinel-1 GRD scenes.
3. Downloads VV and VH bands via S3.
4. Computes zonal statistics (mean backscatter per parcel).
5. Publishes EOProduct entities to Orion-LD.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from typing import Any

import requests

from app.services.copernicus_client import CopernicusDataSpaceClient
from app.services.fiware_integration import upsert_eo_product

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")


def _get_headers(tenant_id: str) -> dict[str, str]:
    """Build minimal NGSI-LD headers for GET queries (public, no write)."""
    return {
        "Accept": "application/ld+json",
        "NGSILD-Tenant": tenant_id,
        "Fiware-Service": tenant_id,
        "Fiware-ServicePath": "/",
    }


def _should_skip_parcel(
    parcel_id: str,
    tenant_id: str,
    sensing_date: str,
    orion_url: str,
) -> bool:
    """Check if an EOProduct already exists for this parcel + date.

    Returns True if we should skip (already ingested), False if we should process.
    """
    try:
        resp = requests.get(
            f"{orion_url}/ngsi-ld/v1/entities",
            params={
                "type": "EOProduct",
                "q": f'hasAgriParcel=="{parcel_id}";acquisitionDate=="{sensing_date}"',
                "limit": 1,
            },
            headers=_get_headers(tenant_id),
            timeout=10,
        )
        if resp.status_code == 200:
            entities = resp.json()
            if isinstance(entities, list) and len(entities) > 0:
                logger.debug(
                    "Skipping parcel %s for %s — EOProduct already exists",
                    parcel_id,
                    sensing_date,
                )
                return True
    except Exception as e:
        logger.debug("Skip check failed for %s: %s", parcel_id, e)
    return False


def compute_parcel_backscatter(
    vv_path: str,
    vh_path: str,
    parcel_geometry: dict[str, Any],
) -> tuple[float, float] | None:
    """Compute mean VV and VH backscatter for a parcel polygon from GeoTIFF rasters.

    Uses rasterio to read the dB values and numpy to compute the mean over
    the mask derived from the parcel geometry.

    Args:
        vv_path: Path to VV polarization GeoTIFF (dB values)
        vh_path: Path to VH polarization GeoTIFF (dB values)
        parcel_geometry: GeoJSON geometry of the parcel polygon

    Returns:
        (vv_mean, vh_mean) in dB, or None if raster files are missing/unreadable
    """
    import numpy as np
    import rasterio
    from rasterio.features import rasterize
    from shapely.geometry import shape

    for path in (vv_path, vh_path):
        if not os.path.isfile(path):
            logger.error("Raster file not found: %s", path)
            return None

    try:
        geom = shape(parcel_geometry)

        def _read_mean(raster_path: str) -> float:
            with rasterio.open(raster_path) as src:
                # Create binary mask from parcel geometry
                mask = rasterize(
                    [(geom, 1)],
                    out_shape=(src.height, src.width),
                    transform=src.transform,
                    fill=0,
                    dtype="uint8",
                )
                data = src.read(1).astype(np.float32)
                # Mask out nodata (0 values and NaN)
                nodata = src.nodata
                valid = (mask == 1) & (np.isfinite(data))
                if nodata is not None:
                    valid = valid & (data != nodata)
                if not np.any(valid):
                    logger.warning("No valid pixels for geometry in %s", raster_path)
                    return float("nan")
                return float(np.mean(data[valid]))

        vv_mean = _read_mean(vv_path)
        vh_mean = _read_mean(vh_path)

        if np.isnan(vv_mean) or np.isnan(vh_mean):
            return None

        return (round(vv_mean, 4), round(vh_mean, 4))

    except Exception as e:
        logger.error("Zonal stats failed: %s", e, exc_info=True)
        return None


def _get_parcel_geometry(parcel_entity: dict[str, Any]) -> dict[str, Any] | None:
    """Extract GeoJSON geometry from an NGSI-LD AgriParcel entity."""
    location = parcel_entity.get("location", {})
    if isinstance(location, dict):
        geom = location.get("value") or location
        if geom and "type" in geom:
            return geom
    return None


def _get_parcel_bbox(geometry: dict[str, Any]) -> list[float]:
    """Compute bounding box [minLon, minLat, maxLon, maxLat] from GeoJSON geometry."""
    from shapely.geometry import shape
    geom = shape(geometry)
    bounds = geom.bounds  # (minx, miny, maxx, maxy)
    return list(bounds)


def sar_crawl_task(tenant_id: str | None = None) -> dict[str, Any]:
    """Celery task: crawl Sentinel-1 GRD scenes for all parcels.

    Queries Orion-LD for AgriParcel entities, searches Copernicus STAC
    for new S1 GRD scenes, downloads bands, computes zonal stats,
    and publishes EOProduct entities.

    Args:
        tenant_id: Optional — if None, processes all tenants (platform-level run).

    Returns:
        Dict with summary: parcels_processed, scenes_found, eo_products_created.
    """
    from app.services.platform_credentials import get_copernicus_credentials_with_fallback

    logger.info("SAR crawler: starting S1 GRD ingestion cycle")

    # --- Credentials ---
    creds = get_copernicus_credentials_with_fallback()
    if not creds:
        logger.error("SAR crawler: no Copernicus credentials available")
        return {"error": "no_credentials", "parcels_processed": 0}

    copernicus = CopernicusDataSpaceClient()
    copernicus.set_credentials(creds["client_id"], creds["client_secret"])

    # --- Fetch parcels ---
    try:
        headers = _get_headers(tenant_id or "default")
        resp = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/entities",
            params={
                "type": "AgriParcel",
                "attrs": "location,name",
                "limit": 1000,
            },
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        parcels = resp.json()
    except Exception as e:
        logger.error("SAR crawler: failed to fetch AgriParcel entities: %s", e)
        return {"error": "parcel_fetch_failed", "parcels_processed": 0}

    if not isinstance(parcels, list):
        parcels = []

    logger.info("SAR crawler: fetched %d parcels", len(parcels))

    # --- Date window: last 14 days ---
    end_date = date.today()
    start_date = end_date - timedelta(days=14)

    summary = {
        "parcels_processed": 0,
        "scenes_found": 0,
        "eo_products_created": 0,
        "parcels_skipped": 0,
        "errors": 0,
    }

    # --- Process each parcel ---
    for parcel in parcels:
        parcel_id = parcel.get("id", "")
        parcel_tenant = tenant_id or "default"

        if not parcel_id:
            continue

        geometry = _get_parcel_geometry(parcel)
        if not geometry:
            logger.debug("Skipping parcel %s — no geometry", parcel_id)
            continue

        bbox = _get_parcel_bbox(geometry)

        try:
            scenes = copernicus.search_s1_scenes(
                bbox=bbox,
                start_date=start_date,
                end_date=end_date,
                limit=20,
            )
        except Exception as e:
            logger.error("S1 search failed for parcel %s: %s", parcel_id, e)
            summary["errors"] += 1
            continue

        summary["scenes_found"] += len(scenes)

        for scene in scenes:
            sensing_date = scene["sensing_date"]
            scene_id = scene["id"]

            # Skip if already ingested
            if _should_skip_parcel(parcel_id, parcel_tenant, sensing_date, ORION_URL):
                summary["parcels_skipped"] += 1
                continue

            # Download bands
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    band_paths = copernicus.download_s1_bands(
                        scene_id,
                        polarizations=["vv", "vh"],
                        output_dir=tmpdir,
                    )
                except Exception as e:
                    logger.error("S1 download failed for %s: %s", scene_id, e)
                    summary["errors"] += 1
                    continue

                vv_path = band_paths.get("vv", "")
                vh_path = band_paths.get("vh", "")
                if not vv_path or not vh_path:
                    logger.warning("Missing polarization bands for %s", scene_id)
                    continue

                # Zonal stats
                result = compute_parcel_backscatter(vv_path, vh_path, geometry)
                if result is None:
                    logger.warning("Zonal stats failed for %s on %s", parcel_id, scene_id)
                    continue

                vv_mean, vh_mean = result

                # Parse acquisition datetime
                try:
                    acq_date = datetime.fromisoformat(
                        scene.get("datetime") or f"{sensing_date}T00:00:00Z"
                    )
                except (ValueError, TypeError):
                    acq_date = datetime.strptime(sensing_date, "%Y-%m-%d")

                # Publish to Orion-LD
                eid = upsert_eo_product(
                    tenant_id=parcel_tenant,
                    parcel_id=parcel_id,
                    vv_mean=vv_mean,
                    vh_mean=vh_mean,
                    acquisition_date=acq_date,
                )
                if eid:
                    summary["eo_products_created"] += 1
                    logger.info(
                        "Created/updated EOProduct %s for parcel %s", eid, parcel_id
                    )
                else:
                    summary["errors"] += 1

        summary["parcels_processed"] += 1

    logger.info(
        "SAR crawler: complete — %d parcels, %d scenes, %d EOProducts created",
        summary["parcels_processed"],
        summary["scenes_found"],
        summary["eo_products_created"],
    )
    return summary
