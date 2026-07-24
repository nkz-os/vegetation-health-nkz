"""EngineSelector — per-tenant engine selection with auto-degradation.

Primary engine = SentinelHubEngine (zero-download).
Fallback engine = LocalProcessingEngine (sovereign GDAL/rasterio).

Each tenant gets its OWN SentinelHubEngine instance to avoid credential
leaks across tenants. The fallback engine is shared (stateless).

Degradation rules:
  - Auth failure (401/403)      → fallback for 1 hour
  - Rate limit (429)            → fallback for 5 minutes
  - Timeout                     → fallback for this request only
  - Server error (5xx)          → fallback for this request only
"""

import asyncio
import logging
import time
from datetime import date

from .base import BaseVegetationEngine, IndexResult, EngineDegradedException, TileLocalFallback
from .sentinel_hub import SentinelHubEngine
from .local import LocalProcessingEngine

logger = logging.getLogger(__name__)

# Degradation TTLs in seconds
AUTH_FAILURE_TTL = 3600     # 1 hour
RATE_LIMIT_TTL = 300        # 5 minutes


class EngineSelector:
    """Selects and manages engine lifecycle with automatic degradation.

    Thread safety: each tenant gets its own SentinelHubEngine instance
    so concurrent requests from different tenants never share credentials.
    """

    def __init__(self):
        # Per-tenant primary engines (one SentinelHubEngine per tenant)
        self._engines: dict[str, SentinelHubEngine] = {}
        # Shared fallback (stateless — just dispatches Celery tasks)
        self._fallback: BaseVegetationEngine = LocalProcessingEngine()
        # tenant_id → (engine_name, degraded_until_timestamp)
        self._tenant_degraded: dict[str, tuple[str, float]] = {}
        # Guards the _engines check-and-insert in `_get_engine_for` so
        # concurrent cold-start requests for the same new tenant don't each
        # resolve credentials and construct their own engine (last-write-wins
        # churn). Single lock, not per-tenant: the critical section is short
        # (dict check + one offloaded credential resolve) and a per-tenant
        # lock dict would need its own cleanup to avoid growing unbounded.
        self._engine_lock = asyncio.Lock()

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
        engine = await self._get_engine_for(tenant_id)

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
            self._tenant_degraded[tenant_id] = (
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

    def is_sentinel_hub_usable(self, tenant_id: str) -> bool:
        """Cheap pre-check: is Sentinel Hub actually usable for this tenant now?

        Returns True only if the tenant is NOT currently degraded AND
        Copernicus credentials resolve (BYOK → platform → env). Reuses the
        selector's own degraded-state map and `_resolve_credentials` — no
        reimplementation, no network I/O (credential resolution is a local
        DB/env read).

        Used by POST /calculate to avoid calling the selector (and reserving
        quota) for the common no-credential / degraded case, which would
        otherwise degrade to an INLINE local compute that blocks the HTTP
        request for minutes and risks an api-gateway 504.

        This method is intentionally sync (it does its own blocking DB read
        via `_resolve_credentials`). Callers invoking it from an async
        context (e.g. a FastAPI request handler) MUST offload it themselves
        via `await asyncio.to_thread(selector.is_sentinel_hub_usable, tenant_id)`
        — see `api/scenes.py` — so the blocking BYOK query never runs
        directly on the event loop.
        """
        if tenant_id in self._tenant_degraded:
            _, until = self._tenant_degraded[tenant_id]
            if time.time() < until:
                return False
            # Degradation window elapsed — clear it and re-evaluate creds.
            del self._tenant_degraded[tenant_id]

        client_id, client_secret = _resolve_credentials(tenant_id)
        return bool(client_id and client_secret)

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
        """Get tile with auto-degradation on failure.

        Mirrors `compute_indices`' degradation recording: an
        `EngineDegradedException` marks the tenant degraded in
        `_tenant_degraded` for `e.retry_after_seconds`, same TTL mechanism.
        Unlike `compute_indices`, there is no working engine-level tile
        fallback (`LocalProcessingEngine.get_tile` unconditionally raises
        `NotImplementedError` — Phase 3 tile rendering through the engine
        interface is not implemented). So instead of calling
        `self._fallback.get_tile(...)`, this raises `TileLocalFallback` to
        tell the caller (`api/tiles.py`) to query the local COG directly,
        which is the only tile fallback that actually exists today.
        """
        engine = await self._get_engine_for(tenant_id)
        try:
            return await engine.get_tile(
                tenant_id=tenant_id,
                index_type=index_type,
                z=z, x=x, y=y,
                date_str=date_str,
                color_ramp=color_ramp,
            )
        except EngineDegradedException as e:
            logger.warning(
                "Engine degraded for tenant %s (tile): %s — retry after %ds",
                tenant_id, e.reason, e.retry_after_seconds,
            )
            self._tenant_degraded[tenant_id] = (
                "fallback", time.time() + e.retry_after_seconds
            )
            raise TileLocalFallback(str(e)) from e
        except Exception as e:
            logger.error(
                "Unexpected engine error for tenant %s (tile): %s — falling back",
                tenant_id, e,
            )
            raise TileLocalFallback(str(e)) from e

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

    async def _get_engine_for(self, tenant_id: str) -> BaseVegetationEngine:
        """Return the active engine for a tenant.

        Each tenant gets its own SentinelHubEngine instance, created lazily
        with credentials resolved from: BYOK (vegetation_config) → platform
        fallback (external_api_credentials) → env vars.

        Credential resolution runs a blocking SQLAlchemy query, so it is
        offloaded to a worker thread via `asyncio.to_thread` — this method
        runs inside FastAPI's async event loop and must never block it
        (matches `LocalProcessingEngine`'s discipline in `local.py`).

        The `await asyncio.to_thread(...)` below is an interleave point on
        the single-threaded event loop: two concurrent first-time calls for
        the same new tenant could otherwise both pass the `not in` check
        before either writes the cache, each resolving credentials and
        constructing its own `SentinelHubEngine` (redundant DB hits, and the
        second write clobbers the first). `_engine_lock` serializes the
        check-and-insert; the re-check right after acquiring the lock
        (double-checked locking, same pattern as
        `SentinelHubClient.get_token`'s token-refresh lock) lets a coroutine
        that lost the race reuse the engine the winner already cached instead
        of resolving again.
        """
        if tenant_id in self._tenant_degraded:
            engine_name, until = self._tenant_degraded[tenant_id]
            if time.time() < until:
                logger.debug(
                    "Tenant %s is degraded to %s until %s",
                    tenant_id, engine_name, until,
                )
                return self._fallback
            else:
                del self._tenant_degraded[tenant_id]

        if tenant_id in self._engines:
            return self._engines[tenant_id]

        # Lazily create a per-tenant engine with resolved credentials.
        async with self._engine_lock:
            # Double-check: another coroutine may have populated the cache
            # for this tenant while we were waiting for the lock.
            if tenant_id in self._engines:
                return self._engines[tenant_id]

            engine = SentinelHubEngine()
            client_id, client_secret = await asyncio.to_thread(_resolve_credentials, tenant_id)
            if client_id and client_secret:
                engine.set_credentials(client_id, client_secret)
                logger.info("Resolved credentials for tenant %s (source: byok)", tenant_id)
            else:
                logger.warning(
                    "No credentials for tenant %s — Sentinel Hub will be unavailable",
                    tenant_id,
                )
            self._engines[tenant_id] = engine
            return engine


def _resolve_credentials(tenant_id: str) -> tuple[str | None, str | None]:
    """Resolve Copernicus CDSE credentials for a tenant.

    Priority:
      1. Tenant BYOK (vegetation_config table, Fernet-decrypted)
      2. Platform (COPERNICUS_CLIENT_ID / COPERNICUS_CLIENT_SECRET env vars,
         sourced from the shared K8s secret `copernicus-cdse-secret`)
    """
    # 1. Tenant BYOK
    try:
        from app.database import SessionLocal
        from app.models.config import VegetationConfig
        from app.services.encryption import decrypt_secret

        db = SessionLocal()
        try:
            cfg = db.query(VegetationConfig).filter(
                VegetationConfig.tenant_id == tenant_id
            ).first()
            if cfg and cfg.copernicus_client_id and cfg.copernicus_client_secret_encrypted:
                client_id = cfg.copernicus_client_id
                client_secret = decrypt_secret(cfg.copernicus_client_secret_encrypted)
                return client_id, client_secret
        finally:
            db.close()
    except Exception as e:
        logger.debug("BYOK credential lookup failed: %s", e)

    # 2. Platform (env vars)
    try:
        from app.services.platform_credentials import get_copernicus_credentials
        creds = get_copernicus_credentials()
        if creds:
            return creds.get("client_id"), creds.get("client_secret")
    except Exception as e:
        logger.debug("Platform credential lookup failed: %s", e)

    return None, None
