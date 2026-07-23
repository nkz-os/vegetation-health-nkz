"""SentinelHubEngine — primary engine using Sentinel Hub Statistical + Process APIs.

Produces identical EOProduct outputs as LocalProcessingEngine but without
downloading raw Sentinel-2 bands locally.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any

from .base import BaseVegetationEngine, IndexResult, EngineHealth
from app.services.sentinel_hub_client import (
    SentinelHubClient,
    SentinelHubAuthError,
    SentinelHubError,
    SentinelHubRateLimitError,
    SentinelHubTimeoutError,
)
from app.services.evalscripts import MULTI_INDEX, NDVI_COLOR

logger = logging.getLogger(__name__)

# Map engine index_type → evalscript output band name
_INDEX_OUTPUT_MAP = {
    "NDVI": "ndvi",
    "EVI": "evi",
    "SAVI": "savi",
    "GNDVI": "gndvi",
    "NDRE": "ndre",
}


class SentinelHubEngine(BaseVegetationEngine):
    """Primary vegetation index engine — zero-download via Sentinel Hub API."""

    def __init__(self, client_id: str = "", client_secret: str = ""):
        self._client = SentinelHubClient(
            client_id=client_id,
            client_secret=client_secret,
        )

    @property
    def engine_name(self) -> str:
        return "sentinel_hub"

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        """Update OAuth2 credentials (e.g., tenant BYOK switch)."""
        self._client.set_credentials(client_id, client_secret)

    async def compute_indices(
        self,
        tenant_id: str,
        parcel_id: str,
        parcel_geometry: dict,
        date_range: tuple[date, date],
        index_types: list[str],
        cloud_cover_max: float = 50.0,
    ) -> list[IndexResult]:
        """Compute vegetation indices via Statistical API.

        Uses the multi-index evalscript to compute all requested indices
        in a single API call per 5-day aggregation window.
        """
        bands = ["B02", "B03", "B04", "B05", "B08", "B8A", "SCL"]

        try:
            raw = await self._client.statistical(
                geometry=parcel_geometry,
                evalscript=MULTI_INDEX,
                date_range=date_range,
                bands=bands,
                cloud_cover_max=cloud_cover_max,
            )
        except SentinelHubError as e:
            logger.error("Sentinel Hub Statistical API failed: %s", e)
            raise

        results: list[IndexResult] = []
        for interval in raw.get("data", []):
            outputs = interval.get("outputs", {})
            interval_from = interval.get("interval", {}).get("from", "")
            sensing_date = self._parse_interval_midpoint(interval_from)

            for idx_type in index_types:
                output_key = _INDEX_OUTPUT_MAP.get(idx_type.upper())
                if not output_key:
                    logger.warning("Unknown index type %s — skipped", idx_type)
                    continue

                band_data = outputs.get(output_key, {}).get("bands", {}).get("B0", {})
                stats = band_data.get("stats", {})
                if not stats:
                    continue

                results.append(IndexResult(
                    index_type=idx_type.upper(),
                    sensing_date=sensing_date,
                    mean=float(stats.get("mean", 0)),
                    std=float(stats.get("stDev", 0)),
                    min=float(stats.get("min", 0)),
                    max=float(stats.get("max", 0)),
                    p10=float(stats.get("percentiles", {}).get("10", 0)),
                    p90=float(stats.get("percentiles", {}).get("90", 0)),
                    valid_pixels=int(stats.get("sampleCount", 0)),
                    total_pixels=int(stats.get("sampleCount", 0)) + int(stats.get("noDataCount", 0)),
                    data_fidelity="sentinel_hub",
                ))

        return results

    async def get_tile(
        self,
        tenant_id: str,
        index_type: str,
        z: int,
        x: int,
        y: int,
        date_str: str | None = None,
        color_ramp: str = "agronomic",
    ) -> bytes:
        """Render a visual tile via Process API.

        Currently only NDVI color rendering is supported. Additional
        index visualizations (EVI, SAVI color ramps) can be added as
        separate evalscripts.
        """
        # EPSG:3857 tile → EPSG:4326 bbox
        bbox = _tile_to_bbox(x, y, z)

        try:
            return await self._client.process(
                bbox=bbox,
                evalscript=NDVI_COLOR,
                width=256,
                height=256,
                date_str=date_str,
            )
        except SentinelHubError as e:
            logger.error("Sentinel Hub Process API failed for tile %d/%d/%d: %s", z, x, y, e)
            raise

    async def health_check(self) -> EngineHealth:
        """Check engine health by attempting token refresh."""
        try:
            await self._client.get_token()
            return EngineHealth(status="ok", reason=None)
        except SentinelHubError as e:
            return EngineHealth(status="unavailable", reason=str(e))

    @staticmethod
    def _parse_interval_midpoint(interval_from: str) -> date:
        """Extract midpoint date from a 5-day aggregation interval.

        interval_from = "2026-07-15T00:00:00Z" → date(2026, 7, 17)
        """
        try:
            dt = datetime.fromisoformat(interval_from.replace("Z", "+00:00"))
            return (dt + timedelta(days=2)).date()
        except (ValueError, TypeError):
            return date.today()


def _tile_to_bbox(x: int, y: int, z: int) -> list[float]:
    """Convert TMS tile (x, y, z) to EPSG:4326 bbox [minLon, minLat, maxLon, maxLat]."""
    import math
    n = 2.0 ** z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return [lon_min, lat_min, lon_max, lat_max]
