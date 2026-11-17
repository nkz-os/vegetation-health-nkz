"""Fill the zone products of the current zoning when it changes.

A new current zoning has no per-zone products yet; the three most recent NDVI
dates are published at once so the zone series is usable straight away. Zone
products are additive: this never raises and a failing date or zone is skipped.
"""

import logging
import os
import tempfile
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

BACKFILL_INDEX = "NDVI"
GRID_RES_M = 10.0


class _GridProcessor:
    """The two things ``zone_statistics`` asks of a processor, on the parcel grid."""

    def __init__(self, grid):
        self.band_meta = {"transform": grid.transform, "height": grid.shape[0],
                          "width": grid.shape[1], "crs": grid.crs}

    def calculate_statistics(self, index_array, mask=None):
        from app.services.processor import VegetationIndexProcessor
        return VegetationIndexProcessor.calculate_statistics(self, index_array, mask=mask)


def _parcel_geometry(tenant_id: str, parcel_urn: str) -> Dict[str, Any]:
    """The parcel's GeoJSON geometry from the broker."""
    from app.api.zoning_versions import broker_for

    fiware = broker_for(tenant_id)
    response = fiware.session.get(
        f"{fiware.context_broker_url}/ngsi-ld/v1/entities/{parcel_urn}",
        params={"options": "keyValues", "attrs": "location"},
        headers={"Accept": "application/json", "Content-Type": None},
        timeout=10,
    )
    response.raise_for_status()
    location = response.json().get("location")
    if isinstance(location, dict) and location.get("type") == "GeoProperty":
        location = location.get("value")
    if not isinstance(location, dict):
        raise ValueError(f"parcel {parcel_urn} has no geometry")
    return location


def _storage_reader(tenant_id: str) -> Callable[[Any, Any], np.ndarray]:
    """Reader of ready rasters from object storage, resampled onto the parcel grid."""
    from app.jobs.zoning_multidate.grid import read_onto_grid
    from app.services.storage import create_storage_service, generate_tenant_bucket_name

    bucket = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(tenant_id)
    storage = create_storage_service(storage_type=os.getenv("STORAGE_TYPE", "s3"), default_bucket=bucket)

    def read(row, grid) -> np.ndarray:
        with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
            storage.download_file(row.raster_path, tmp.name)
            with open(tmp.name, "rb") as fh:
                return read_onto_grid(fh.read(), grid)

    return read


def _latest_ready_rows(rows, last_n: int) -> List[Any]:
    """The newest ready raster of each of the ``last_n`` most recent dates, newest date first."""
    by_date: Dict[Any, Any] = {}
    for row in rows:
        if row.pending or not row.raster_path:
            continue
        best = by_date.get(row.sensing_date)
        if best is None or row.created_at > best.created_at:
            by_date[row.sensing_date] = row
    return [by_date[d] for d in sorted(by_date, reverse=True)[:last_n]]


def backfill_current_zone_products(db, tenant_id: str, parcel_urn: str, *, last_n: int = 3,
                                   read_raster: Optional[Callable[[Any, Any], np.ndarray]] = None) -> int:
    """Publish the NDVI zone products of the current zoning for the ``last_n`` latest dates.

    ``read_raster(row, grid)`` returns the raster on the parcel grid (default: object
    storage). Returns the number of products published; never raises.
    """
    try:
        from app.jobs.zoning_multidate.dates import load_raster_rows
        from app.jobs.zoning_multidate.grid import parcel_grid
        from app.services.fiware_integration import upsert_eo_zone_index
        from app.services.zone_products import fetch_current_zones, zone_statistics

        zones = fetch_current_zones(tenant_id, parcel_urn)
        if not zones:
            return 0
        rows = _latest_ready_rows(load_raster_rows(db, tenant_id, parcel_urn, BACKFILL_INDEX), last_n)
        if not rows:
            return 0
        grid = parcel_grid(_parcel_geometry(tenant_id, parcel_urn), GRID_RES_M)
        reader = read_raster or _storage_reader(tenant_id)
        processor = _GridProcessor(grid)
        parcel_short = parcel_urn.split(":")[-1][:10]
    except Exception as exc:
        logger.warning("Zone backfill setup failed for %s: %s", parcel_urn, exc)
        return 0

    published = 0
    for row in rows:
        try:
            array = reader(row, grid)
        except Exception as exc:
            logger.warning("Zone backfill: raster of %s unreadable for %s: %s",
                           row.sensing_date, parcel_urn, exc)
            continue
        for zone in zones:
            try:
                stats = zone_statistics(processor, array, zone["geometry"], exact=True)
                if stats is None:
                    logger.info("Zone %s skipped on %s (too few valid pixels)", zone["zone_id"], row.sensing_date)
                    continue
                if upsert_eo_zone_index(tenant_id, parcel_short, zone, row.sensing_date,
                                        BACKFILL_INDEX.lower(), stats):
                    published += 1
            except Exception as exc:
                logger.warning("Zone backfill: %s on %s failed for %s: %s",
                               zone["zone_id"], row.sensing_date, parcel_urn, exc)
    logger.info("Zone backfill for %s: %d products published", parcel_urn, published)
    return published
