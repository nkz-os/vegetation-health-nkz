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


def test_upsert_eo_index_returns_none_on_upsert_failure():
    """Locks in the error-surfacing branch: a failed upsert (errors present or
    upserted==0) must return None, not silently report success. Characterization
    test for the fix that replaced the old swallowed-failure behavior."""
    stats = {"mean": 0.5, "min": 0.1, "max": 0.9, "std": 0.1, "pixel_count": 100}

    class Failing(FakeAsyncOrion):
        async def upsert_entities_batch(self, entities):
            await super().upsert_entities_batch(entities)
            return {"upserted": 0, "errors": [{"detail": "boom"}], "entity_ids": []}

    with patch.object(fi, "OrionClient", Failing):
        eid = fi.upsert_eo_index("montiko", "urn:ngsi-ld:AgriParcel:p", "NDVI", stats, date(2025, 9, 6))
    assert eid is None


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


def test_real_orion_client_contract_for_wrapper():
    # The wrapper uses upsert_entities_batch + close (no async context manager).
    # Locks the real SDK surface so a reintroduced `async with` or a missing
    # method is caught here, not silently in production.
    from nkz_platform_sdk import OrionClient
    assert hasattr(OrionClient, "upsert_entities_batch")
    assert hasattr(OrionClient, "close")


# ---------------------------------------------------------------------------
# Deleting one index must not take the whole acquisition with it.
#
# The EOProduct id is per (parcel, sensingDate) and every index for that date
# merges into it as a named Property. Both delete paths removed the entity, so
# deleting one NDVI job also erased NDRE/EVI/SAVI/GNDVI for that date. After the
# July job cleanups the broker was left with no EOProduct at all.
# ---------------------------------------------------------------------------

_EID = "urn:ngsi-ld:EOProduct:montiko:da36ccd2-1:2025-09-06"


def _entity(*index_keys):
    e = {
        "id": _EID, "type": "EOProduct",
        "hasAgriParcel": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:p"},
        "sensingDate": {"type": "Property", "value": "2025-09-06"},
        "pixelCount": {"type": "Property", "value": 10},
        "source": {"type": "Property", "value": "vegetation_health"},
    }
    for k in index_keys:
        e[k] = {"type": "Property", "value": 0.5}
    return e


def test_delete_eo_index_removes_only_that_index_when_others_remain():
    orion = MagicMock()
    orion.delete.return_value = _resp(204)
    get = MagicMock(); get.status_code = 200; get.json.return_value = _entity("ndre", "evi")
    orion.get.return_value = get
    with patch.object(fi, "SyncOrionClient", return_value=orion):
        assert fi.delete_eo_index("montiko", _EID, "NDVI") is True
    paths = [c.args[0] for c in orion.delete.call_args_list]
    assert paths == [f"/ngsi-ld/v1/entities/{_EID}/attrs/ndvi"]


def test_delete_eo_index_removes_entity_once_no_index_is_left():
    orion = MagicMock()
    orion.delete.return_value = _resp(204)
    get = MagicMock(); get.status_code = 200; get.json.return_value = _entity()
    orion.get.return_value = get
    with patch.object(fi, "SyncOrionClient", return_value=orion):
        assert fi.delete_eo_index("montiko", _EID, "NDVI") is True
    paths = [c.args[0] for c in orion.delete.call_args_list]
    assert paths[0] == f"/ngsi-ld/v1/entities/{_EID}/attrs/ndvi"
    assert paths[-1] == f"/ngsi-ld/v1/entities/{_EID}"


def test_delete_eo_index_keeps_entity_when_it_cannot_be_read_back():
    """A failed read must not be taken as "no indices left"."""
    orion = MagicMock()
    orion.delete.return_value = _resp(204)
    get = MagicMock(); get.status_code = 500; get.json.return_value = {}
    orion.get.return_value = get
    with patch.object(fi, "SyncOrionClient", return_value=orion):
        fi.delete_eo_index("montiko", _EID, "NDVI")
    paths = [c.args[0] for c in orion.delete.call_args_list]
    assert f"/ngsi-ld/v1/entities/{_EID}" not in paths


def test_delete_paths_do_not_remove_the_shared_entity_directly():
    """Neither delete path may call delete_eo_product on an acquisition entity."""
    import inspect
    from app.api import jobs as jobs_api
    from app.api import monitoring_periods as mp_api
    for mod in (jobs_api, mp_api):
        src = inspect.getsource(mod)
        assert "delete_eo_product(" not in src, f"{mod.__name__} still deletes the whole EOProduct"
