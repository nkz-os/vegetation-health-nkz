"""
FIWARE NGSI-LD integration for Vegetation Prime module.

Follows the platform convention:
- 1 VegetationIndex entity per parcel (updated via PATCH on each new analysis)
- Orion-LD is the source of truth for analysis results
- TimescaleDB receives historical snapshots via NGSI-LD subscription (telemetry-worker)
- Headers: NGSILD-Tenant (platform convention), Content-Type: application/ld+json
"""

import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, date, timezone

import requests

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv("CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json")

# Index attribute names: {index_type} → list of NGSI-LD property names
INDEX_ATTRS = {
    "NDVI": ("ndviMean", "ndviMin", "ndviMax", "ndviStdDev"),
    "EVI": ("eviMean", "eviMin", "eviMax", "eviStdDev"),
    "SAVI": ("saviMean", "saviMin", "saviMax", "saviStdDev"),
    "GNDVI": ("gndviMean", "gndviMin", "gndviMax", "gndviStdDev"),
    "NDRE": ("ndreMean", "ndreMin", "ndreMax", "ndreStdDev"),
    "NDWI": ("ndwiMean", "ndwiMin", "ndwiMax", "ndwiStdDev"),
}


def _make_headers(tenant_id: str) -> Dict[str, str]:
    """Standard headers for Orion-LD requests."""
    return {
        "Content-Type": "application/ld+json",
        "NGSILD-Tenant": tenant_id,
    }


def _entity_id_for_parcel(tenant_id: str, parcel_id: str) -> str:
    """Generate deterministic VegetationIndex entity ID for a parcel.

    Format: urn:ngsi-ld:VegetationIndex:{tenant}:{parcel_short}
    """
    parcel_short = parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id
    return f"urn:ngsi-ld:VegetationIndex:{tenant_id}:{parcel_short}"


def upsert_vegetation_index_entity(
    tenant_id: str,
    parcel_id: str,
    index_type: str,
    statistics: Dict[str, Any],
    raster_url: str,
    sensing_date: date,
    custom_attr_name: Optional[str] = None,
) -> Optional[str]:
    """Create or update the VegetationIndex entity for a parcel in Orion-LD.

    One entity per parcel — updated (PATCH) on each new analysis.
    Each index type writes to its own set of properties (ndviMean, eviMean, etc.)
    with ``observedAt`` metadata so the telemetry-worker subscription captures
    every update as a historical snapshot in TimescaleDB.

    Follows the weather-worker pattern: POST → 409 → PATCH /attrs.

    Args:
        tenant_id: Tenant identifier
        parcel_id: Full URN of the AgriParcel entity
        index_type: NDVI, EVI, SAVI, GNDVI, NDRE, or CUSTOM
        statistics: Dict with mean, min, max, std, pixel_count
        raster_url: S3/MinIO path to the COG raster
        sensing_date: Date of the satellite observation
        custom_attr_name: For CUSTOM indices, the attribute prefix (e.g. "custom_abc123")

    Returns:
        Entity ID if successful, None on failure
    """
    entity_id = _entity_id_for_parcel(tenant_id, parcel_id)
    headers = _make_headers(tenant_id)

    observed_at = (
        datetime(sensing_date.year, sensing_date.month, sensing_date.day,
                 10, 50, 0, tzinfo=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    # Determine attribute names
    if index_type == "CUSTOM" and custom_attr_name:
        attr_mean = f"{custom_attr_name}Mean"
        attr_min = f"{custom_attr_name}Min"
        attr_max = f"{custom_attr_name}Max"
        attr_std = f"{custom_attr_name}StdDev"
    elif index_type in INDEX_ATTRS:
        attr_mean, attr_min, attr_max, attr_std = INDEX_ATTRS[index_type]
    else:
        # Fallback for unknown index types
        prefix = index_type.lower()
        attr_mean = f"{prefix}Mean"
        attr_min = f"{prefix}Min"
        attr_max = f"{prefix}Max"
        attr_std = f"{prefix}StdDev"

    # Build the full entity for creation
    entity = {
        "@context": [CONTEXT_URL],
        "id": entity_id,
        "type": "VegetationIndex",
        "refAgriParcel": {"type": "Relationship", "object": parcel_id},
        attr_mean: {
            "type": "Property",
            "value": round(float(statistics.get("mean", 0)), 6),
            "observedAt": observed_at,
        },
        attr_min: {
            "type": "Property",
            "value": round(float(statistics.get("min", 0)), 6),
            "observedAt": observed_at,
        },
        attr_max: {
            "type": "Property",
            "value": round(float(statistics.get("max", 0)), 6),
            "observedAt": observed_at,
        },
        attr_std: {
            "type": "Property",
            "value": round(float(statistics.get("std", 0)), 6),
            "observedAt": observed_at,
        },
        "pixelCount": {
            "type": "Property",
            "value": int(statistics.get("pixel_count", 0)),
        },
        "rasterUrl": {
            "type": "Property",
            "value": raster_url,
        },
        "sensingDate": {
            "type": "Property",
            "value": sensing_date.isoformat(),
        },
        "source": {
            "type": "Property",
            "value": "vegetation_health",
        },
    }

    try:
        # POST — create entity
        url = f"{ORION_URL}/ngsi-ld/v1/entities"
        response = requests.post(url, json=entity, headers=headers, timeout=10)

        result = None

        if response.status_code in (201, 204):
            logger.info("Created VegetationIndex entity %s", entity_id)
            result = entity_id

        elif response.status_code == 409:
            # Entity already exists — PATCH attributes
            logger.debug("VegetationIndex %s already exists, updating...", entity_id)
            result = _patch_vegetation_index(entity_id, entity, headers)
        else:
            logger.error(
                "Failed to create VegetationIndex entity: %s - %s",
                response.status_code, response.text,
            )

        # After successful VegetationIndex upsert, update the AgriParcel
        if result:
            _patch_agriparcel_with_index(
                parcel_id, entity_id, statistics, observed_at, headers
            )

        return result

    except Exception as e:
        logger.error("Error upserting VegetationIndex entity: %s", e, exc_info=True)
        return None


def _patch_vegetation_index(
    entity_id: str,
    entity: Dict[str, Any],
    headers: Dict[str, str],
) -> Optional[str]:
    """PATCH attributes on an existing VegetationIndex entity.

    Extracts all attributes except id/type and sends them as a PATCH.
    """
    try:
        attrs = {
            k: v for k, v in entity.items()
            if k not in ("id", "type")
        }
        # @context must be included in PATCH body for application/ld+json
        if "@context" not in attrs:
            attrs["@context"] = [CONTEXT_URL]

        url = f"{ORION_URL}/ngsi-ld/v1/entities/{entity_id}/attrs"
        response = requests.patch(url, json=attrs, headers=headers, timeout=10)

        if response.status_code in (204, 207):
            logger.info("Updated VegetationIndex entity %s", entity_id)
            return entity_id

        logger.error(
            "Failed to PATCH VegetationIndex %s: %s - %s",
            entity_id, response.status_code, response.text,
        )
        return None

    except Exception as e:
        logger.error("Error patching VegetationIndex entity: %s", e, exc_info=True)
        return None


def _patch_agriparcel_with_index(
    parcel_id: str,
    vegetation_index_id: str,
    statistics: Dict[str, Any],
    observed_at: str,
    headers: Dict[str, str],
) -> None:
    """PATCH the AgriParcel entity with latest NDVI summary and back-reference."""
    try:
        ndvi_value = round(float(statistics.get("mean", 0)), 4)
    except (TypeError, ValueError):
        ndvi_value = 0.0

    parcel_patch = {
        "latestNDVI": {
            "type": "Property",
            "value": ndvi_value,
            "observedAt": observed_at,
        },
        "refVegetationIndex": {
            "type": "Relationship",
            "object": vegetation_index_id,
        },
    }

    try:
        url = f"{ORION_URL}/ngsi-ld/v1/entities/{parcel_id}/attrs"
        resp = requests.patch(url, json=parcel_patch, headers=headers, timeout=10)
        if resp.status_code in (204, 207):
            logger.debug(
                "Patched AgriParcel %s with latestNDVI=%s from %s",
                parcel_id, ndvi_value, vegetation_index_id,
            )
        else:
            logger.warning(
                "Failed to PATCH AgriParcel %s: %s — %s",
                parcel_id, resp.status_code, resp.text,
            )
    except Exception as e:
        logger.warning("Error patching AgriParcel %s: %s", parcel_id, e)


def query_vegetation_index_entities(
    tenant_id: str,
    parcel_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Query VegetationIndex entities from Orion-LD.

    Args:
        tenant_id: Tenant identifier
        parcel_id: Optional — filter by refAgriParcel
        limit: Max results

    Returns:
        List of NGSI-LD entities
    """
    headers = {
        "Accept": "application/ld+json",
        "NGSILD-Tenant": tenant_id,
    }

    params: Dict[str, Any] = {
        "type": "VegetationIndex",
        "limit": limit,
    }
    if parcel_id:
        params["q"] = f"refAgriParcel=={parcel_id}"

    try:
        url = f"{ORION_URL}/ngsi-ld/v1/entities"
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Failed to query VegetationIndex entities: %s", e)
        return []


# ---------------------------------------------------------------------------
# Legacy compatibility — kept for FIWARE_NATIVE_MODE=false / dual
# ---------------------------------------------------------------------------

class FIWAREClient:
    """Legacy client for interacting with FIWARE Context Broker.

    Kept for backward compatibility during the dual-write transition period.
    New code should use upsert_vegetation_index_entity() directly.
    """

    def __init__(self, context_broker_url: str, tenant_id: str, auth_token: Optional[str] = None):
        self.context_broker_url = context_broker_url.rstrip('/')
        self.tenant_id = tenant_id
        self.session = requests.Session()
        if auth_token:
            self.session.headers['Authorization'] = f'Bearer {auth_token}'
        self.session.headers.update({
            'NGSILD-Tenant': tenant_id,
            'Content-Type': 'application/ld+json',
        })

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
            response = self.session.patch(url, json=attrs, timeout=10)
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
