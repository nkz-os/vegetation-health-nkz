"""Regression test for the parcel check-size endpoint.

The endpoint used to import `ORION_URL` from `app.services.fiware_integration`,
a constant removed by the SyncOrionClient refactor — every call raised
`ImportError` and surfaced as a 502. It also double-prefixed the entity URN.
This test pins the SDK contract: SyncOrionClient(tenant_id) + get_entity with
keyValues, using the entity_id directly (full URN, no re-prefix).
"""
from unittest.mock import MagicMock, patch

import pytest

from app.api.parcels import check_parcel_size


class _StubOrion:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id
        self.calls = []

    def get_entity(self, entity_id, options=None):
        self.calls.append((entity_id, options))
        return {"id": entity_id, "location": None}


@pytest.mark.asyncio
async def test_check_size_uses_sdk_and_full_urn():
    req = MagicMock()
    req.headers.get.return_value = None

    stub = _StubOrion("montiko")
    with patch("app.api.parcels.SyncOrionClient", return_value=stub) as MockClient:
        result = await check_parcel_size(
            entity_id="urn:ngsi-ld:AgriParcel:12d68d51-35f5-4957-9222-caa961f9a427",
            request=req,
            current_user={"tenant_id": "montiko", "roles": []},
        )

    assert result["area_ha"] == 0.0
    assert result["exceeds_limit"] is False
    assert result["limit_ha"] == 500.0

    MockClient.assert_called_once_with("montiko")
    # Full URN passed through untouched (no `urn:ngsi-ld:AgriParcel:{entity_id}` prefix)
    assert stub.calls == [
        ("urn:ngsi-ld:AgriParcel:12d68d51-35f5-4957-9222-caa961f9a427", "keyValues")
    ]
