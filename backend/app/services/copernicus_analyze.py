"""Copernicus (Sentinel Hub Statistical API) leg of a season analysis.

Runs in the Celery worker (`vegetation.analyze_copernicus`), never inside the
HTTP request: the Statistical API round-trip, the per-result broker publish and
the eager raster of the latest window together exceeded the gateway's 30 s
proxy timeout, so the request came back 502 and no job was ever committed.
"""

import asyncio
import logging
from datetime import date
from typing import Any

from app.models import VegetationJob
from app.services.fiware_integration import upsert_eo_index

logger = logging.getLogger(__name__)


async def publish_eo_index(**kwargs) -> None:
    """Publish one index to the broker from async code, best-effort.

    `upsert_eo_index` reaches the async SDK through an `asyncio.run()` bridge
    meant for sync callers. Calling it straight from a running event loop raised
    "asyncio.run() cannot be called from a running event loop" on every index,
    so the broker stayed empty while the caller still succeeded. Running it in a
    worker thread gives the bridge the loop-free context it expects.
    """
    try:
        await asyncio.to_thread(lambda: upsert_eo_index(**kwargs))
    except Exception as exc:
        logger.warning(
            "EOProduct publish failed for %s/%s: %s",
            kwargs.get("parcel_id"), kwargs.get("index_type"), exc,
        )


async def persist_copernicus_indices(
    db,
    *,
    engine_selector,
    quota,
    tenant_id: str,
    entity_id: str,
    geometry: dict[str, Any],
    date_range: tuple[date, date],
    indices: list[str],
    season_uuid,
    user_id: str | None,
) -> list[str]:
    """Compute `indices` in ONE Statistical API call and persist the results.

    One completed calculate_index job per (index, window), bound to the season;
    one EOProduct per result; the latest window's raster materialized eagerly,
    older ones lazily. Quota for `indices` must already be reserved: it is
    refunded when the selector degrades to the free local engine, and refunded
    before re-raising on a genuine failure (the caller then routes local).

    Returns the created job ids.
    """
    from app.services.copernicus_raster import materialize_if_pending

    try:
        # ONE round-trip for every index: the engine's multi-index evalscript
        # computes them together. One call per index multiplied identical
        # round-trips.
        results = await engine_selector.compute_indices(
            tenant_id=tenant_id,
            parcel_id=entity_id,
            parcel_geometry=geometry,
            date_range=date_range,
            index_types=indices,
        )
        ran_local = any(
            getattr(r, "data_fidelity", None) == "degraded_fallback" for r in results
        )
        if ran_local:
            # Selector degraded to the free local pipeline — refund all.
            for _ in indices:
                quota.release(tenant_id)

        job_ids: list[str] = []
        by_index: dict[str, list] = {}
        for r in results:
            by_index.setdefault(r.index_type, []).append(r)
        for idx in indices:
            by_date = sorted(by_index.get(idx, []), key=lambda r: r.sensing_date)
            latest_date = by_date[-1].sensing_date if by_date else None
            for r in by_date:
                is_latest = (r.sensing_date == latest_date)
                stats = {
                    "mean": r.mean, "min": r.min, "max": r.max, "std": r.std,
                    "p10": r.p10, "p90": r.p90,
                    "valid_pixels": r.valid_pixels, "total_pixels": r.total_pixels,
                }
                engine_label = ("local_processing"
                                if r.data_fidelity == "degraded_fallback" else "copernicus")
                wjob = VegetationJob(
                    tenant_id=tenant_id,
                    job_type="calculate_index",
                    entity_id=entity_id,
                    entity_type="AgriParcel",
                    parameters={"index_type": idx, "entity_id": entity_id, "engine": engine_label},
                    created_by=user_id,
                    crop_season_id=season_uuid,
                )
                wjob.mark_completed({
                    "index_type": idx,
                    "index_key": idx,
                    "engine": engine_label,
                    "data_fidelity": r.data_fidelity,
                    "sensing_date": r.sensing_date.isoformat(),
                    "statistics": stats,
                    "geometry": geometry,          # for lazy raster materialization
                    "raster_path": None,
                    "raster_pending": (engine_label == "copernicus"),
                })
                db.add(wjob)
                db.commit()
                db.refresh(wjob)

                # Best-effort: a broker outage must not lose the statistics we
                # just computed and stored.
                await publish_eo_index(
                    tenant_id=tenant_id,
                    parcel_id=entity_id,
                    index_type=idx,
                    # Copernicus reports valid_pixels; upsert reads pixel_count.
                    statistics={**stats, "pixel_count": stats.get("valid_pixels", 0)},
                    sensing_date=r.sensing_date,
                )

                if is_latest and engine_label == "copernicus":
                    await materialize_if_pending(db, wjob)   # eager latest (off-loop)
                job_ids.append(str(wjob.id))
        return job_ids
    except Exception:
        for _ in indices:
            quota.release(tenant_id)
        raise
