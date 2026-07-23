"""EngineSelector — per-tenant engine selection with auto-degradation.

Primary engine = SentinelHubEngine (zero-download).
Fallback engine = LocalProcessingEngine (sovereign GDAL/rasterio).

Degradation rules:
  - Auth failure (401/403)      → fallback for 1 hour
  - Rate limit (429)            → fallback for 5 minutes
  - Timeout                     → fallback for this request only
  - Server error (5xx)          → fallback for this request only
"""

import logging
import time
from datetime import date

from .base import BaseVegetationEngine, IndexResult, EngineDegradedException
from .sentinel_hub import SentinelHubEngine
from .local import LocalProcessingEngine

logger = logging.getLogger(__name__)

# Degradation TTLs in seconds
AUTH_FAILURE_TTL = 3600     # 1 hour
RATE_LIMIT_TTL = 300        # 5 minutes


class EngineSelector:
    """Selects and manages engine lifecycle with automatic degradation."""

    def __init__(self):
        self._primary: BaseVegetationEngine = SentinelHubEngine()
        self._fallback: BaseVegetationEngine = LocalProcessingEngine()
        # tenant_id → (engine_name, degraded_until_timestamp)
        self._tenant_engine: dict[str, tuple[str, float]] = {}

    async def compute_indices(
        self,
        tenant_id: str,
        parcel_id: str,
        parcel_geometry: dict,
        date_range: tuple[date, date],
        index_types: list[str],
        cloud_cover_max: float = 50.0,
    ) -> list[IndexResult]:
        """Compute indices with automatic degradation on failure."""
        engine = self._get_engine_for(tenant_id)

        try:
            results = await engine.compute_indices(
                tenant_id=tenant_id,
                parcel_id=parcel_id,
                parcel_geometry=parcel_geometry,
                date_range=date_range,
                index_types=index_types,
                cloud_cover_max=cloud_cover_max,
            )
            if engine is self._fallback:
                for r in results:
                    r.data_fidelity = "degraded_fallback"
            return results
        except EngineDegradedException as e:
            logger.warning(
                "Engine degraded for tenant %s: %s — retry after %ds",
                tenant_id, e.reason, e.retry_after_seconds,
            )
            self._tenant_engine[tenant_id] = (
                "fallback", time.time() + e.retry_after_seconds
            )
            return await self._fallback_compute(
                tenant_id, parcel_id, parcel_geometry,
                date_range, index_types, cloud_cover_max,
            )
        except Exception as e:
            logger.error(
                "Unexpected engine error for tenant %s: %s — falling back",
                tenant_id, e,
            )
            return await self._fallback_compute(
                tenant_id, parcel_id, parcel_geometry,
                date_range, index_types, cloud_cover_max,
            )

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
        """Get tile with auto-degradation on failure."""
        engine = self._get_engine_for(tenant_id)
        try:
            return await engine.get_tile(
                tenant_id=tenant_id,
                index_type=index_type,
                z=z, x=x, y=y,
                date_str=date_str,
                color_ramp=color_ramp,
            )
        except EngineDegradedException:
            return await self._fallback.get_tile(
                tenant_id=tenant_id,
                index_type=index_type,
                z=z, x=x, y=y,
                date_str=date_str,
                color_ramp=color_ramp,
            )
        except Exception:
            return await self._fallback.get_tile(
                tenant_id=tenant_id,
                index_type=index_type,
                z=z, x=x, y=y,
                date_str=date_str,
                color_ramp=color_ramp,
            )

    async def _fallback_compute(self, tenant_id, parcel_id, parcel_geometry,
                                 date_range, index_types, cloud_cover_max):
        """Compute via fallback engine and mark fidelity."""
        results = await self._fallback.compute_indices(
            tenant_id=tenant_id,
            parcel_id=parcel_id,
            parcel_geometry=parcel_geometry,
            date_range=date_range,
            index_types=index_types,
            cloud_cover_max=cloud_cover_max,
        )
        for r in results:
            r.data_fidelity = "degraded_fallback"
        return results

    def _get_engine_for(self, tenant_id: str) -> BaseVegetationEngine:
        """Return the active engine for a tenant, respecting degradation TTL."""
        if tenant_id in self._tenant_engine:
            engine_name, until = self._tenant_engine[tenant_id]
            if time.time() < until:
                logger.debug(
                    "Tenant %s is degraded to %s until %s",
                    tenant_id, engine_name, until,
                )
                return self._fallback
            else:
                del self._tenant_engine[tenant_id]
        return self._primary
