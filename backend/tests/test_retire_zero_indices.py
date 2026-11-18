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
    assert ib.zero_index_attributes(entity) == ["ndvi", "savi"]


class FakeBroker:
    def __init__(self, products):
        self.products = products
        self.deleted = []

    def list_products(self, tenant_id):
        return list(self.products)

    def delete_attr(self, tenant_id, entity_id, attr):
        self.deleted.append((entity_id, attr))
        return True


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
    broker.delete_attr = lambda *a: False
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=lambda p: b"", broker=broker)
    assert report.zeros_retired == 0 and report.errors == 1


def _http_error(status):
    exc = Exception(f"http {status}")
    exc.response = MagicMock(status_code=status)
    return exc


@pytest.mark.parametrize("status,expected", [(None, True), (404, True), (500, False)])
def test_delete_eo_attribute_outcomes(monkeypatch, status, expected):
    client = MagicMock()
    if status:
        client.delete_entity_attr.side_effect = _http_error(status)
    monkeypatch.setattr(fi, "SyncOrionClient", lambda tenant: client)
    assert fi.delete_eo_attribute(TENANT, "urn:x", "ndvi") is expected
    client.delete_entity_attr.assert_called_once_with("urn:x", "ndvi")
