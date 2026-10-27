import pytest

from app.api import entities

PARENT = "urn:ngsi-ld:AgriParcel:p-1"


class _Orion:
    created = []

    def __init__(self, *a, **k):
        pass

    async def create_entity(self, entity):
        _Orion.created.append(entity)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_roi_links_to_its_parent_parcel(monkeypatch):
    monkeypatch.setattr(entities, "OrionClient", _Orion)
    _Orion.created.clear()
    await entities.create_roi(
        {"name": "z", "geometry": {"type": "Polygon", "coordinates": []}, "parent_id": PARENT},
        current_user={"tenant_id": "tenant-a"},
    )
    entity = _Orion.created[0]
    assert entity["hasAgriParcel"] == {"type": "Relationship", "object": PARENT}
    assert "hasParent" not in entity
