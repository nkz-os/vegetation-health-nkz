# backend/tests/test_eoproduct_contract.py
from datetime import date
from unittest.mock import MagicMock, patch
from app.services import fiware_integration as fi
from tests.fake_orion import FakeAsyncOrion

_STATS = {"mean": 0.72, "min": 0.31, "max": 0.91, "std": 0.12, "pixel_count": 1000}


def _resp(code):
    r = MagicMock(); r.status_code = code; r.text = ""; return r


def test_entity_id_is_acquisition_level_no_product_type():
    eid = fi._entity_id_for_acquisition("montiko", "urn:ngsi-ld:AgriParcel:da36ccd2-1111", "2025-09-06")
    assert eid == "urn:ngsi-ld:EOProduct:montiko:da36ccd2-1:2025-09-06"


def test_upsert_eo_index_writes_named_index_via_upsert_batch():
    fake = None
    with patch.object(fi, "OrionClient", FakeAsyncOrion):
        eid = fi.upsert_eo_index(
            "montiko", "urn:ngsi-ld:AgriParcel:da36ccd2-1111", "NDVI",
            _STATS, date(2025, 9, 6),
            raster_url="s3://b/ndvi.tif", preview_url="s3://b/ndvi.png",
        )
        fake = FakeAsyncOrion.last_instance
    assert eid.endswith(":2025-09-06")
    assert len(fake.calls) == 1 and len(fake.calls[0]) == 1   # one upsert, one entity
    body = fake.entities[0]
    assert body["type"] == "EOProduct"
    assert body["id"] == eid
    assert "@context" not in body                              # SDK injects it
    ndvi = body["ndvi"]
    assert ndvi["value"] == 0.72
    assert ndvi["min"]["value"] == 0.31 and ndvi["std"]["value"] == 0.12
    assert ndvi["rasterUrl"]["value"] == "s3://b/ndvi.tif"
    assert ndvi["previewUrl"]["value"] == "s3://b/ndvi.png"
    assert body["hasAgriParcel"]["object"] == "urn:ngsi-ld:AgriParcel:da36ccd2-1111"


def test_upsert_eo_index_second_index_sends_its_property():
    stats = {"mean": 0.41, "min": 0.1, "max": 0.6, "std": 0.05, "pixel_count": 10}
    with patch.object(fi, "OrionClient", FakeAsyncOrion):
        fi.upsert_eo_index("montiko", "urn:ngsi-ld:AgriParcel:p", "NDRE", stats, date(2025, 9, 6))
        fake = FakeAsyncOrion.last_instance
    body = fake.entities[0]
    # Server-side options=update merges this into the existing acquisition entity.
    assert "ndre" in body and body["ndre"]["value"] == 0.41
    assert body["type"] == "EOProduct" and "@context" not in body


def test_upsert_eo_index_does_not_use_post_or_patch():
    # Scoped to the optical writer (Task 1). Task 2 extends this to upsert_eo_product.
    # NOT a whole-module scan — FIWAREClient.session.post/.patch are legitimate.
    import inspect
    src = inspect.getsource(fi.upsert_eo_index)
    assert ".post(" not in src and ".patch(" not in src, "upsert_eo_index must not call .post/.patch"


def test_upsert_eo_product_does_not_use_post_or_patch():
    import inspect
    src = inspect.getsource(fi.upsert_eo_product)
    assert ".post(" not in src and ".patch(" not in src, "upsert_eo_product must not call .post/.patch"


def test_real_orion_client_has_upsert_batch():
    from nkz_platform_sdk import OrionClient, SyncOrionClient
    assert hasattr(OrionClient, "upsert_entities_batch")
    assert not hasattr(SyncOrionClient, "post") and not hasattr(SyncOrionClient, "patch")


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


def test_upsert_eo_index_cloud_cover_na_does_not_raise():
    stats = {"mean": 0.5, "min": 0.1, "max": 0.9, "std": 0.2, "pixel_count": 100}
    with patch.object(fi, "OrionClient", FakeAsyncOrion):
        eid = fi.upsert_eo_index(
            "montiko", "urn:ngsi-ld:AgriParcel:p", "NDVI", stats, date(2025, 9, 6),
            cloud_cover="N/A",
        )
        fake = FakeAsyncOrion.last_instance
    assert eid is not None
    body = fake.entities[0]
    assert "cloudCoverPercentage" not in body
    assert body["ndvi"]["value"] == 0.5


def test_upsert_eo_index_cloud_cover_empty_string_does_not_raise():
    stats = {"mean": 0.5, "min": 0.1, "max": 0.9, "std": 0.2, "pixel_count": 100}
    with patch.object(fi, "OrionClient", FakeAsyncOrion):
        eid = fi.upsert_eo_index(
            "montiko", "urn:ngsi-ld:AgriParcel:p", "NDVI", stats, date(2025, 9, 6),
            cloud_cover="",
        )
        fake = FakeAsyncOrion.last_instance
    assert eid is not None
    body = fake.entities[0]
    assert "cloudCoverPercentage" not in body
