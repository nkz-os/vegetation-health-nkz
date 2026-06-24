"""Regression: Orion reads must use the SDK typed API (get_entity / query_entities).

Bug (incident 2026-06-24): scenes.py / entities.py / weather_service.py used
`async with OrionClient(...) as o: await o.get("/ngsi-ld/...")`, but the deployed
SDK OrionClient supports neither the async context manager nor a raw `.get()`.
The TypeError was swallowed, so parcel geometry came back empty -> HTTP 422
"Could not determine parcel geometry" when launching a vegetation job.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from app.api import entities, scenes


def _fake_orion(entities_map):
    """Build a fake OrionClient exposing the typed API used in production."""
    client = AsyncMock()
    client.get_entity = AsyncMock(side_effect=lambda eid, *a, **k: entities_map[eid])
    client.close = AsyncMock()
    # Must NOT be used as an async context manager.
    client.__aenter__ = None
    return client


def test_resolve_entity_name_uses_get_entity():
    parcel_id = "urn:ngsi-ld:AgriParcel:abc"
    fake = _fake_orion({parcel_id: {"id": parcel_id, "name": {"type": "Property", "value": "ORAINBAI"}}})
    with patch.object(entities, "OrionClient", return_value=fake):
        name = asyncio.run(entities._resolve_entity_name(parcel_id, "montiko"))
    assert name == "ORAINBAI"
    fake.get_entity.assert_awaited_once_with(parcel_id)
    fake.close.assert_awaited_once()


def test_get_crop_species_follows_relationship():
    parcel_id = "urn:ngsi-ld:AgriParcel:abc"
    crop_id = "urn:ngsi-ld:AgriCrop:xyz"
    fake = _fake_orion({
        parcel_id: {"id": parcel_id, "hasAgriCrop": {"type": "Relationship", "object": crop_id}},
        crop_id: {"id": crop_id, "name": {"type": "Property", "value": "Olea europaea"}},
    })
    with patch.object(scenes, "OrionClient", return_value=fake):
        species = asyncio.run(scenes._get_crop_species_from_orion("montiko", parcel_id))
    assert species == "Olea europaea"
    fake.close.assert_awaited_once()
