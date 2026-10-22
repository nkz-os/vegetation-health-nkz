"""Tests for EOProduct entity creation/update in Orion-LD."""
from unittest.mock import patch
from datetime import datetime, timezone

import app.services.fiware_integration as fi
from app.services.fiware_integration import upsert_eo_product, _entity_id_for_eo_product, _make_headers
from tests.fake_orion import FakeAsyncOrion


class TestEntityIdForEOProduct:
    """EOProduct entity ID generation."""

    def test_entity_id_format(self):
        """EOProduct entity ID follows deterministic format."""
        eid = _entity_id_for_eo_product(
            "mytenant",
            "urn:ngsi-ld:AgriParcel:parcel-4",
            "2026-06-01",
        )
        assert eid.startswith("urn:ngsi-ld:EOProduct:")
        assert "mytenant" in eid
        assert "parcel-4" in eid
        assert "2026-06-01" in eid
        assert "GRD" in eid

    def test_entity_id_handles_different_parcel_id_format(self):
        """EOProduct entity ID works with short parcel IDs (no URN prefix)."""
        eid = _entity_id_for_eo_product("test", "parcel-42", "2026-06-01")
        assert "parcel-42" in eid


class TestUpsertEOProduct:
    """upsert_eo_product() — create or update EOProduct in Orion-LD."""

    def test_upsert_eo_product_writes_via_upsert_batch(self):
        with patch.object(fi, "OrionClient", FakeAsyncOrion):
            result = fi.upsert_eo_product(
                tenant_id="test_tenant",
                parcel_id="urn:ngsi-ld:AgriParcel:parcel-4",
                vv_mean=-12.3, vh_mean=-18.7,
                acquisition_date=datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc),
            )
            fake = FakeAsyncOrion.last_instance
        assert result is not None
        assert len(fake.calls) == 1 and len(fake.calls[0]) == 1
        body = fake.entities[0]
        assert body["type"] == "EOProduct"
        assert "@context" not in body                       # SDK injects it
        assert body["productType"]["value"] == "GRD"
        assert body["processingLevel"]["value"] == "L1"
        assert body["backscatterVV"]["value"] == -12.3
        assert body["backscatterVH"]["value"] == -18.7
        assert "observedAt" in body["backscatterVV"]
        assert body["hasAgriParcel"]["object"] == "urn:ngsi-ld:AgriParcel:parcel-4"
        assert body["source"]["value"] == "vegetation_health"

    def test_upsert_eo_product_returns_none_without_acquisition_date(self):
        with patch.object(fi, "OrionClient", FakeAsyncOrion):
            result = fi.upsert_eo_product(
                tenant_id="t", parcel_id="urn:ngsi-ld:AgriParcel:p",
                vv_mean=-1.0, vh_mean=-2.0, acquisition_date=None,
            )
        assert result is None
