"""Celery tasks for Landsat LST acquisition and EOProduct publish."""

from __future__ import annotations

import logging
from datetime import date

from app.celery_app import celery_app
from app.services.fiware_integration import upsert_eo_lst
from app.services.lst_processor import process_parcel_lst
from app.services.platform_credentials import get_copernicus_credentials_with_fallback

logger = logging.getLogger(__name__)


def _auth_headers() -> dict[str, str]:
    creds = get_copernicus_credentials_with_fallback()
    if not creds:
        return {}
    from app.services.copernicus_client import CopernicusDataSpaceClient

    client = CopernicusDataSpaceClient()
    client.set_credentials(creds["client_id"], creds["client_secret"])
    token = client._get_access_token()
    return {"Authorization": f"Bearer {token}"}


@celery_app.task(
    name="vegetation.process_parcel_lst",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def process_parcel_lst_task(
    self,
    tenant_id: str,
    parcel_id: str,
    latitude: float,
    longitude: float,
    geometry_geojson: dict,
):
    """Fetch Landsat LST for a parcel and upsert EOProduct.lst."""
    try:
        stats, sensing_date_str, scene_id = process_parcel_lst(
            latitude,
            longitude,
            geometry_geojson,
            auth_headers=_auth_headers(),
        )
        if not stats or not sensing_date_str:
            logger.info("No LST available for parcel %s (tenant %s)", parcel_id, tenant_id)
            return {"status": "no_data", "parcel_id": parcel_id}

        sensing_date = date.fromisoformat(sensing_date_str)
        entity_id = upsert_eo_lst(
            tenant_id=tenant_id,
            parcel_id=parcel_id,
            statistics=stats,
            sensing_date=sensing_date,
            scene_id=scene_id,
        )
        if not entity_id:
            raise RuntimeError(f"EOProduct LST upsert failed for {parcel_id}")

        logger.info(
            "Published LST %.1f°C for parcel %s (scene %s)",
            stats["mean"], parcel_id, scene_id,
        )
        return {
            "status": "ok",
            "parcel_id": parcel_id,
            "entity_id": entity_id,
            "lst_c": round(stats["mean"], 2),
            "sensing_date": sensing_date_str,
            "scene_id": scene_id,
        }
    except Exception as exc:
        logger.error("LST task failed for %s: %s", parcel_id, exc, exc_info=True)
        raise self.retry(exc=exc)
