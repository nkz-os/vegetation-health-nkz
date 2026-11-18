"""Published zero-pixel indices are retired by deleting the attribute, never by writing null."""
from unittest.mock import MagicMock

import pytest

import app.jobs.interior_backfill as ib
from app.services import fiware_integration as fi

TENANT = "acme"
NS = "https://nkz-os.org/ns/"
ETSI = "https://uri.etsi.org/ngsi-ld/default-context/"


def _product(pixel_count=0, **indices):
    entity = {"id": "urn:ngsi-ld:EOProduct:acme:p1:2026-06-21", "type": "EOProduct",
              "pixelCount": {"type": "Property", "value": pixel_count}}
    for name, value in indices.items():
        entity[name] = {"type": "Property", "value": value}
    return entity


def test_zero_attributes_only_when_pixelcount_zero():
    assert ib.zero_index_attributes(_product(0, ndvi=0, ndre=0.31, evi=0.0)) == ["ndvi", "evi"]
    assert ib.zero_index_attributes(_product(42, ndvi=0)) == []
    assert ib.zero_index_attributes(_product(0, ndvi=0.2)) == []
    assert ib.zero_index_attributes({"id": "x", "type": "EOProduct", "ndvi": {"type": "Property", "value": 0}}) == []


def test_iri_names_handled():
    entity = {"id": "x", "type": "EOProduct",
              NS + "pixelCount": {"type": "Property", "value": 0},
              NS + "ndvi": {"type": "Property", "value": 0},
              ETSI + "savi": {"type": "Property", "value": 0},
              "sensingDate": {"type": "Property", "value": "2026-06-21"}}
    # the key is returned exactly as the entity carries it
    assert ib.zero_index_attributes(entity) == [NS + "ndvi", ETSI + "savi"]


class FakeBroker:
    def __init__(self, products):
        self.products = products
        self.deleted = []

    def list_products(self, tenant_id):
        return list(self.products)

    def delete_attr(self, tenant_id, entity_id, attr):
        self.deleted.append((entity_id, attr))
        return "deleted"


@pytest.fixture(autouse=True)
def _no_jobs(monkeypatch):
    monkeypatch.setattr(ib, "_load_jobs", lambda db, tenant, parcel=None: [])


def test_dry_run_lists_without_deleting():
    broker = FakeBroker([_product(0, ndvi=0, ndre=0)])
    report = ib.backfill_tenant(None, TENANT, reader=lambda p: b"", broker=broker)
    assert report.zeros_retired == 2 and broker.deleted == []


def test_apply_deletes_only_zero_attrs():
    broker = FakeBroker([_product(0, ndvi=0, ndre=0.4), _product(30, evi=0)])
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: b"", broker=broker)
    assert broker.deleted == [("urn:ngsi-ld:EOProduct:acme:p1:2026-06-21", "ndvi")]
    assert report.zeros_retired == 1


def test_retire_zeros_can_be_turned_off():
    broker = FakeBroker([_product(0, ndvi=0)])
    report = ib.backfill_tenant(None, TENANT, apply=True, retire_zeros=False,
                                reader=lambda p: b"", broker=broker)
    assert broker.deleted == [] and report.zeros_retired == 0


def test_failed_delete_counts_as_error():
    broker = FakeBroker([_product(0, ndvi=0)])
    broker.delete_attr = lambda *a: "error"
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: b"", broker=broker)
    assert report.zeros_retired == 0 and report.errors == 1


def test_not_found_is_not_counted_as_retired():
    broker = FakeBroker([_product(0, ndvi=0)])
    broker.delete_attr = lambda *a: "not_found"
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: b"", broker=broker)
    assert (report.zeros_retired, report.not_found, report.errors) == (0, 1, 0)


def test_iri_key_is_the_one_deleted():
    entity = _product(0)
    entity[NS + "ndvi"] = {"type": "Property", "value": 0}
    broker = FakeBroker([entity])
    ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: b"", broker=broker)
    assert broker.deleted == [(entity["id"], NS + "ndvi")]


def test_parcel_scope_limits_retirement():
    mine = _product(0, ndvi=0)
    mine["hasAgriParcel"] = {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:p1"}
    other = _product(0, ndvi=0)
    other["id"] = "urn:ngsi-ld:EOProduct:acme:p2:2026-06-21"
    other["hasAgriParcel"] = {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:p2"}
    legacy_iri = _product(0, evi=0)
    legacy_iri["id"] = "urn:ngsi-ld:EOProduct:acme:p1:2026-06-22"
    legacy_iri[NS + "refAgriParcel"] = {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:p1"}
    broker = FakeBroker([mine, other, legacy_iri])
    report = ib.backfill_tenant(None, TENANT, parcel="urn:ngsi-ld:AgriParcel:p1", apply=True,
                                reader=lambda p: b"", broker=broker)
    assert sorted(broker.deleted) == sorted([(mine["id"], "ndvi"), (legacy_iri["id"], "evi")])
    assert report.zeros_retired == 2


class FullBroker(FakeBroker):
    """Zeros and a valid job on the same date: the upsert rewrites the product's pixelCount."""

    def __init__(self, products):
        super().__init__(products)
        self.upserts = []

    def parcel_geometry(self, tenant_id, parcel_urn):
        from tests.test_interior_backfill import PARCEL_GEOJSON
        return PARCEL_GEOJSON

    def get_product(self, tenant_id, entity_id):
        return next((p for p in self.products if p["id"] == entity_id), None)

    def upsert(self, tenant_id, parcel_id, index_type, **kwargs):
        self.upserts.append(index_type)
        eid = fi._entity_id_for_acquisition(tenant_id, parcel_id, kwargs["sensing_date"].isoformat())
        product = self.get_product(tenant_id, eid)
        product["pixelCount"]["value"] = kwargs["statistics"]["pixel_count"]  # last index wins
        return eid


def _full_setup(monkeypatch, zero_indices):
    from datetime import date
    from tests.test_interior_backfill import PARCEL, _job, _tif_bytes
    eid = fi._entity_id_for_acquisition(TENANT, PARCEL, "2026-06-21")
    product = {"id": eid, "type": "EOProduct", "pixelCount": {"type": "Property", "value": 0},
               "ndvi": {"type": "Property", "value": 0.5}}
    for name in zero_indices:
        product[name] = {"type": "Property", "value": 0}
    jobs = [_job("x/NDVI.tif", 0.5, day=date(2026, 6, 21))]
    monkeypatch.setattr(ib, "_load_jobs", lambda db, tenant, parcel=None: jobs)
    return FullBroker([product]), {"x/NDVI.tif": _tif_bytes()}, eid


def test_apply_deletes_zero_even_though_the_upsert_changed_pixelcount(monkeypatch):
    broker, rasters, eid = _full_setup(monkeypatch, ["evi"])
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: rasters[p], broker=broker)
    assert broker.upserts == ["NDVI"]
    assert broker.deleted == [(eid, "evi")] and report.zeros_retired == 1


def test_zero_index_republished_in_this_run_is_not_deleted(monkeypatch):
    broker, rasters, eid = _full_setup(monkeypatch, [])
    broker.products[0]["ndvi"]["value"] = 0  # a stale zero the valid job republishes
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: rasters[p], broker=broker)
    assert broker.upserts == ["NDVI"] and broker.deleted == [] and report.zeros_retired == 0


def test_dry_run_reports_what_apply_deletes_and_writes_nothing(monkeypatch):
    broker, rasters, eid = _full_setup(monkeypatch, ["evi", "savi"])
    dry = ib.backfill_tenant(None, TENANT, reader=lambda p: rasters[p], broker=broker)
    assert broker.upserts == [] and broker.deleted == []
    broker2, rasters2, _ = _full_setup(monkeypatch, ["evi", "savi"])
    applied = ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: rasters2[p], broker=broker2)
    assert dry.zeros_retired == applied.zeros_retired == 2 and dry.updated == applied.updated == 1


def _http_error(status):
    exc = Exception(f"http {status}")
    exc.response = MagicMock(status_code=status)
    return exc


@pytest.mark.parametrize("status,expected", [(None, "deleted"), (404, "not_found"), (500, "error")])
def test_delete_eo_attribute_outcomes(monkeypatch, status, expected):
    client = MagicMock()
    if status:
        client.delete_entity_attr.side_effect = _http_error(status)
    monkeypatch.setattr(fi, "SyncOrionClient", lambda tenant: client)
    assert fi.delete_eo_attribute(TENANT, "urn:x", NS + "ndvi") == expected
    client.delete_entity_attr.assert_called_once_with("urn:x", NS + "ndvi")
