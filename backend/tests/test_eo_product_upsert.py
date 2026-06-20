"""Tests for EOProduct entity creation/update in Orion-LD."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from app.services.fiware_integration import upsert_eo_product, _entity_id_for_eo_product, _make_headers


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

    @patch("app.services.fiware_integration.requests.post")
    @patch("app.services.fiware_integration.requests.patch")
    def test_create_new_eo_product(self, mock_patch, mock_post):
        """POST returns 201 → entity created, no PATCH needed."""
        mock_post.return_value.status_code = 201
        mock_post.return_value.text = "Created"

        result = upsert_eo_product(
            tenant_id="test_tenant",
            parcel_id="urn:ngsi-ld:AgriParcel:parcel-4",
            vv_mean=-12.3,
            vh_mean=-18.7,
            acquisition_date=datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc),
        )

        assert result is not None
        assert mock_post.called
        mock_patch.assert_not_called()

    @patch("app.services.fiware_integration.requests.post")
    @patch("app.services.fiware_integration.requests.patch")
    def test_update_existing_eo_product(self, mock_patch, mock_post):
        """POST returns 409 → PATCH /attrs."""
        mock_post.return_value.status_code = 409
        mock_patch.return_value.status_code = 204

        result = upsert_eo_product(
            tenant_id="test_tenant",
            parcel_id="urn:ngsi-ld:AgriParcel:parcel-4",
            vv_mean=-10.5,
            vh_mean=-16.2,
            acquisition_date=datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc),
        )

        assert result is not None
        assert mock_post.called
        mock_patch.assert_called_once()

    @patch("app.services.fiware_integration.requests.post")
    def test_eo_product_contains_correct_attributes(self, mock_post):
        """EOProduct entity payload has required SAR attributes."""
        mock_post.return_value.status_code = 201

        upsert_eo_product(
            tenant_id="test_tenant",
            parcel_id="urn:ngsi-ld:AgriParcel:parcel-4",
            vv_mean=-12.3,
            vh_mean=-18.7,
            acquisition_date=datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc),
        )

        call_json = mock_post.call_args[1]["json"]
        assert call_json["type"] == "EOProduct"
        assert call_json["productType"]["value"] == "GRD"
        assert call_json["processingLevel"]["value"] == "L1"
        assert call_json["backscatterVV"]["value"] == -12.3
        assert call_json["backscatterVH"]["value"] == -18.7
        assert "observedAt" in call_json["backscatterVV"]
        assert "observedAt" in call_json["backscatterVH"]
        assert call_json["hasAgriParcel"]["object"] == "urn:ngsi-ld:AgriParcel:parcel-4"
        assert call_json["source"]["value"] == "vegetation_health"
