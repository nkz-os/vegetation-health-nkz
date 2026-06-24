# backend/tests/test_eoproduct_contract.py
from datetime import date
from unittest.mock import MagicMock, patch
from app.services import fiware_integration as fi


def _resp(code):
    r = MagicMock(); r.status_code = code; r.text = ""; return r


def test_entity_id_is_acquisition_level_no_product_type():
    eid = fi._entity_id_for_acquisition("montiko", "urn:ngsi-ld:AgriParcel:da36ccd2-1111", "2025-09-06")
    assert eid == "urn:ngsi-ld:EOProduct:montiko:da36ccd2-1:2025-09-06"


def test_upsert_eo_index_creates_named_index_attribute():
    orion = MagicMock()
    orion.post.return_value = _resp(201)
    stats = {"mean": 0.72, "min": 0.31, "max": 0.88, "std": 0.12, "pixel_count": 1180}
    with patch.object(fi, "SyncOrionClient", return_value=orion):
        eid = fi.upsert_eo_index(
            "montiko", "urn:ngsi-ld:AgriParcel:da36ccd2-1111", "NDVI", stats,
            date(2025, 9, 6), raster_url="s3://b/ndvi.tif", preview_url="s3://b/ndvi.png",
        )
    assert eid.endswith(":2025-09-06")
    body = orion.post.call_args.kwargs["json"]
    assert body["type"] == "EOProduct"
    ndvi = body["ndvi"]
    assert ndvi["value"] == 0.72
    assert ndvi["min"]["value"] == 0.31 and ndvi["std"]["value"] == 0.12
    assert ndvi["rasterUrl"]["value"] == "s3://b/ndvi.tif"
    assert ndvi["previewUrl"]["value"] == "s3://b/ndvi.png"
    assert body["hasAgriParcel"]["object"] == "urn:ngsi-ld:AgriParcel:da36ccd2-1111"


def test_upsert_eo_index_merges_second_index_via_patch():
    orion = MagicMock()
    orion.post.return_value = _resp(409)          # entity already exists
    orion.patch.return_value = _resp(204)
    stats = {"mean": 0.41, "min": 0.2, "max": 0.6, "std": 0.1, "pixel_count": 1180}
    with patch.object(fi, "SyncOrionClient", return_value=orion):
        fi.upsert_eo_index("montiko", "urn:ngsi-ld:AgriParcel:p", "NDRE", stats, date(2025, 9, 6))
    attrs = orion.patch.call_args.kwargs["json"]
    assert "ndre" in attrs and attrs["ndre"]["value"] == 0.41
    assert "id" not in attrs and "type" not in attrs
