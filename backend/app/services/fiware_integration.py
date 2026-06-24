"""
FIWARE NGSI-LD integration for Vegetation Prime module.

Follows the platform convention:
- 1 EOProduct entity per (parcel, sensingDate) acquisition, with one named
  Property per vegetation index merged in via PATCH (see upsert_eo_index)
- Orion-LD is the source of truth for analysis results
- TimescaleDB receives historical snapshots via NGSI-LD subscription (telemetry-worker)
- Uses SyncOrionClient from nkz-platform-sdk (no raw requests/httpx)
"""

import logging
import os
import re
from typing import Dict, Any, Optional, List
from datetime import datetime, date, timezone

from nkz_platform_sdk import SyncOrionClient

logger = logging.getLogger(__name__)

CONTEXT_URL = os.getenv("CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json")


def _make_headers(tenant_id: str) -> Dict[str, str]:
    """Legacy compatibility wrapper — delegates to SyncOrionClient header injection.
    
    Deprecated: new code should use SyncOrionClient directly.
    """
    from nkz_platform_sdk import SyncOrionClient
    orion = SyncOrionClient(tenant_id)
    return {
        "Content-Type": "application/json",
        "NGSILD-Tenant": tenant_id,
        "Fiware-Service": tenant_id,
    }


def _entity_id_for_eo_product(tenant_id: str, parcel_id: str, sensing_date_str: str) -> str:
    """Generate deterministic EOProduct entity ID for a parcel + sensing date.

    Format: urn:ngsi-ld:EOProduct:{tenant}:{parcel_short}:GRD:{date}
    """
    parcel_short = parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id
    return f"urn:ngsi-ld:EOProduct:{tenant_id}:{parcel_short}:GRD:{sensing_date_str}"


def _entity_id_for_acquisition(tenant_id: str, parcel_id: str, sensing_date_str: str) -> str:
    """EOProduct id at acquisition granularity (no productType segment)."""
    parcel_short = parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id
    parcel_short = parcel_short[:10]
    return f"urn:ngsi-ld:EOProduct:{tenant_id}:{parcel_short}:{sensing_date_str}"


def _index_attribute(index_type: str, statistics: dict, observed_at: str,
                     raster_url: Optional[str], preview_url: Optional[str]) -> dict:
    attr = {
        "type": "Property",
        "value": round(float(statistics.get("mean", 0)), 6),
        "observedAt": observed_at,
        "min": {"type": "Property", "value": round(float(statistics.get("min", 0)), 6)},
        "max": {"type": "Property", "value": round(float(statistics.get("max", 0)), 6)},
        "std": {"type": "Property", "value": round(float(statistics.get("std", 0)), 6)},
    }
    if raster_url:
        attr["rasterUrl"] = {"type": "Property", "value": raster_url}
    if preview_url:
        attr["previewUrl"] = {"type": "Property", "value": preview_url}
    return attr


def upsert_eo_index(tenant_id, parcel_id, index_type, statistics, sensing_date,
                    raster_url=None, preview_url=None, cloud_cover=None):
    """Approach A: one EOProduct per (parcel, sensingDate); merge one named index Property."""
    orion = SyncOrionClient(tenant_id)
    sensing_date_str = sensing_date.isoformat()
    entity_id = _entity_id_for_acquisition(tenant_id, parcel_id, sensing_date_str)
    observed_at = datetime(sensing_date.year, sensing_date.month, sensing_date.day,
                           10, 50, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    index_key = index_type.lower()
    index_attr = _index_attribute(index_type, statistics, observed_at, raster_url, preview_url)

    entity = {
        "@context": [CONTEXT_URL],
        "id": entity_id,
        "type": "EOProduct",
        "hasAgriParcel": {"type": "Relationship", "object": parcel_id},
        "sensingDate": {"type": "Property", "value": sensing_date_str},
        "pixelCount": {"type": "Property", "value": int(statistics.get("pixel_count", 0))},
        "source": {"type": "Property", "value": "vegetation_health"},
        index_key: index_attr,
    }
    if cloud_cover is not None:
        try:
            entity["cloudCoverPercentage"] = {"type": "Property", "value": round(float(cloud_cover), 2)}
        except (TypeError, ValueError):
            pass
    try:
        resp = orion.post("/ngsi-ld/v1/entities", json=entity)
        if resp.status_code in (201, 204):
            logger.info("Created EOProduct %s (%s)", entity_id, index_key)
            return entity_id
        if resp.status_code == 409:
            attrs = {k: v for k, v in entity.items() if k not in ("id", "type")}
            patch = orion.patch(f"/ngsi-ld/v1/entities/{entity_id}/attrs", json=attrs)
            if patch.status_code in (204, 207):
                logger.info("Merged %s into EOProduct %s", index_key, entity_id)
                return entity_id
            logger.error("PATCH EOProduct %s failed: %s", entity_id, patch.status_code)
            return None
        logger.error("POST EOProduct %s failed: %s - %s", entity_id, resp.status_code, resp.text)
        return None
    except Exception as exc:
        logger.error("Error upserting EOProduct %s: %s", entity_id, exc, exc_info=True)
        return None


def upsert_eo_product(
    tenant_id: str,
    parcel_id: str,
    vv_mean: float = 0.0,
    vh_mean: float = 0.0,
    acquisition_date=None,
    product_type: str = "GRD",
    processing_level: str = "L1",
    campaign_id: Optional[str] = None,
):
    """Create or update a SAR backscatter EOProduct entity in Orion-LD
    (product_type="GRD").

    Uses SyncOrionClient: POST → 201 | 409 → PATCH pattern.
    """
    orion = SyncOrionClient(tenant_id)

    if acquisition_date is None:
        logger.error("acquisition_date required for SAR EOProduct")
        return None
    sensing_date_str = acquisition_date.strftime("%Y-%m-%d")
    entity_id = _entity_id_for_eo_product(tenant_id, parcel_id, sensing_date_str)
    observed_at = acquisition_date.isoformat().replace("+00:00", "Z")

    entity: Dict[str, Any] = {
        "@context": [CONTEXT_URL],
        "id": entity_id,
        "type": "EOProduct",
        "productType": {"type": "Property", "value": product_type},
        "processingLevel": {"type": "Property", "value": processing_level},
        "hasAgriParcel": {"type": "Relationship", "object": parcel_id},
        "source": {"type": "Property", "value": "vegetation_health"},
        "backscatterVV": {
            "type": "Property",
            "value": round(float(vv_mean), 4),
            "observedAt": observed_at,
        },
        "backscatterVH": {
            "type": "Property",
            "value": round(float(vh_mean), 4),
            "observedAt": observed_at,
        },
        "acquisitionDate": {"type": "Property", "value": observed_at},
    }

    if campaign_id:
        campaign_urn = f"urn:ngsi-ld:AgriParcelOperation:{campaign_id}"
        entity["campaign"] = {"type": "Relationship", "object": campaign_urn}

    try:
        response = orion.post("/ngsi-ld/v1/entities", json=entity)

        if response.status_code in (201, 204):
            logger.info("Created EOProduct entity %s", entity_id)
            return entity_id

        if response.status_code == 409:
            logger.debug("EOProduct %s exists, updating...", entity_id)
            attrs = {k: v for k, v in entity.items() if k not in ("id", "type")}
            if "@context" not in attrs:
                attrs["@context"] = [CONTEXT_URL]
            patch_resp = orion.patch(
                f"/ngsi-ld/v1/entities/{entity_id}/attrs", json=attrs
            )
            if patch_resp.status_code in (204, 207):
                logger.info("Updated EOProduct entity %s", entity_id)
                return entity_id
            logger.error("Failed PATCH EOProduct %s: %s", entity_id, patch_resp.status_code)
        else:
            logger.error(
                "Failed POST EOProduct: %s — %s", response.status_code, response.text
            )

        return None
    except Exception as e:
        logger.error("Error upserting EOProduct: %s", e, exc_info=True)
        return None


def delete_eo_product(tenant_id: str, entity_id: str) -> bool:
    """Delete an EOProduct entity from Orion-LD."""
    orion = SyncOrionClient(tenant_id)
    try:
        response = orion.delete(f"/ngsi-ld/v1/entities/{entity_id}")
        if response.status_code in (204, 200):
            logger.info("Deleted EOProduct %s", entity_id)
            return True
        logger.warning("Failed to delete EOProduct %s: %s", entity_id, response.status_code)
        return False
    except Exception as e:
        logger.error("Error deleting EOProduct %s: %s", entity_id, e)
        return False


# ---------------------------------------------------------------------------
# Legacy compatibility — kept for FIWARE_NATIVE_MODE=false / dual
# ---------------------------------------------------------------------------

class FIWAREClient:
    """Legacy client for interacting with FIWARE Context Broker.

    Kept for backward compatibility during the dual-write transition period.
    New code should use upsert_eo_index() or SyncOrionClient directly.
    """

    def __init__(self, context_broker_url: str, tenant_id: str, auth_token: Optional[str] = None):
        import requests
        self.context_broker_url = context_broker_url.rstrip('/')
        self.tenant_id = tenant_id
        self.session = requests.Session()
        if auth_token:
            self.session.headers['Authorization'] = f'Bearer {auth_token}'
        n = tenant_id.lower().strip()
        n = re.sub(r'[^a-z0-9-]', '', n)
        n = n.strip('-') or tenant_id
        self.session.headers.update({
            'NGSILD-Tenant': n,
            'Fiware-Service': n,
            'Fiware-ServicePath': '/',
            'Content-Type': 'application/ld+json',
            'Accept': 'application/ld+json',
        })
        ctx = os.getenv('CONTEXT_URL', '')
        if ctx:
            self.session.headers['Link'] = (
                f'<{ctx}>; '
                f'rel="http://www.w3.org/ns/json-ld#context"; '
                f'type="application/ld+json"'
            )

    def create_entity(self, entity: Dict[str, Any]) -> bool:
        try:
            url = f"{self.context_broker_url}/ngsi-ld/v1/entities"
            response = self.session.post(url, json=entity, timeout=10)
            if response.status_code in (201, 204):
                logger.info("Created entity %s", entity.get('id'))
                return True
            if response.status_code == 409:
                return self.update_entity(entity)
            logger.error("Failed to create entity: %s - %s", response.status_code, response.text)
            return False
        except Exception as e:
            logger.error("Failed to create entity: %s", e)
            return self.update_entity(entity)

    def update_entity(self, entity: Dict[str, Any]) -> bool:
        try:
            entity_id = entity.get('id')
            if not entity_id:
                raise ValueError("Entity ID is required for update")
            url = f"{self.context_broker_url}/ngsi-ld/v1/entities/{entity_id}/attrs"
            attrs = {k: v for k, v in entity.items() if k not in ('id', 'type', '@context')}
            # PATCH attrs must be application/json + Link header (not ld+json without @context)
            # The session already has Link header from __init__
            response = self.session.patch(
                url, json=attrs, timeout=10,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code in (204, 207):
                logger.info("Updated entity %s", entity_id)
                return True
            logger.error("Failed to update entity: %s - %s", response.status_code, response.text)
            return False
        except Exception as e:
            logger.error("Failed to update entity: %s", e)
            return False

    def query_entities(self, entity_type: str, filters: Optional[Dict[str, Any]] = None, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            url = f"{self.context_broker_url}/ngsi-ld/v1/entities"
            params: Dict[str, Any] = {'type': entity_type, 'limit': limit}
            if filters:
                for key, value in filters.items():
                    params['q'] = f"{key}=={value}"
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to query entities: %s", e)
            return []
