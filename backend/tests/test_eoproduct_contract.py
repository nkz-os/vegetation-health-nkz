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


from unittest.mock import MagicMock, patch
from datetime import date as _d
from app.tasks import processing_tasks as pt


def test_persist_results_writes_eoproduct_only():
    job = MagicMock(); job.entity_id = "urn:ngsi-ld:AgriParcel:p"; job.id = "j1"
    scene = MagicMock(); scene.sensing_date = _d(2025, 9, 6)
    stats = {"mean": 0.7, "min": 0.2, "max": 0.9, "std": 0.1, "pixel_count": 10}
    with patch.object(pt, "upsert_eo_index", return_value="eid") as eo, \
         patch.object(pt, "generate_tenant_bucket_name", return_value="bkt"):
        assert not hasattr(pt, "upsert_vegetation_index_entity")  # removed in Task 3
        pt._persist_results("montiko", job, "NDVI", None, stats, "path/ndvi.tif", scene)
    eo.assert_called_once()
    assert eo.call_args.kwargs["index_type"] == "NDVI"
    assert eo.call_args.kwargs["raster_url"].endswith("path/ndvi.tif")


def test_vegetationindex_writers_removed():
    assert not hasattr(fi, "upsert_vegetation_index_entity")
    assert not hasattr(fi, "_entity_id_for_parcel")
    import inspect
    src = inspect.getsource(fi)
    assert "\"type\": \"VegetationIndex\"" not in src and "'type': 'VegetationIndex'" not in src


from app.tasks import historical_baseline as hb


def test_historical_baseline_writes_eoproduct():
    assert not hasattr(hb, "_upsert_agri_parcel_record")
    import inspect
    assert "upsert_eo_index" in inspect.getsource(hb._process_window)
