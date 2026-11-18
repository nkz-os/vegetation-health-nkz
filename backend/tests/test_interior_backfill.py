"""Interior statistics recomputed from the stored rasters for already published dates."""
from datetime import date

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import app.jobs.interior_backfill as ib
from app.services import fiware_integration as fi

TENANT = "acme"
PARCEL = "urn:ngsi-ld:AgriParcel:p1"
DAY = date(2026, 6, 21)
# Saved rasters are geographic (EPSG:4326, ~0.0001 deg): 50 x 40 pixels; the parcel
# (~7 ha) covers columns 10-40 and rows 5-35, so its interior is the parcel minus a ~10 m ring.
X0, Y0, RES = -3.001, 40.0035, 0.0001
PARCEL_GEOJSON = {"type": "Polygon", "coordinates": [[
    [-3.0, 40.003], [-2.997, 40.003], [-2.997, 40.0], [-3.0, 40.0], [-3.0, 40.003]]]}


def _tif_bytes(nan_block=10, edge=0.1, core=0.8):
    arr = np.full((40, 50), np.nan, dtype="float32")
    arr[5:35, 10:40] = edge         # the whole parcel, border ring included
    arr[6:34, 11:39] = core         # its interior
    if nan_block:
        arr[10:10 + nan_block, 15:15 + nan_block] = np.nan
    with rasterio.MemoryFile() as mem:
        with mem.open(driver="GTiff", height=40, width=50, count=1, dtype="float32",
                      crs="EPSG:4326", transform=from_origin(X0, Y0, RES, RES), nodata=-9999) as dst:
            dst.write(arr, 1)
        return mem.read()


def test_interior_from_raster_core_and_nan():
    out = ib.interior_from_raster(_tif_bytes(), PARCEL_GEOJSON)
    assert out["mask"] == "core"  # a geographic grid must still shrink in metres, not degrees
    assert 700 <= out["total_pixels"] <= 900
    assert out["valid_pixels"] == out["total_pixels"] - 100
    assert out["median"] == pytest.approx(0.8)  # the 0.1 border ring is outside the interior
    assert out["valid_fraction"] == pytest.approx(out["valid_pixels"] / out["total_pixels"])


def _job(path, mean, fraction=None, day=DAY, index="NDVI"):
    return {"entity_id": PARCEL, "index_type": index, "sensing_date": day, "raster_path": path,
            "statistics": {"mean": mean, "min": 0.0, "max": 1.0, "std": 0.1, "pixel_count": 700}}


def _interior(fraction):
    return {"median": 0.5, "mean": 0.5, "p10": 0.4, "p90": 0.6, "valid_pixels": 10,
            "total_pixels": 20, "valid_fraction": fraction, "mask": "core"}


def test_pick_job_matches_published_value():
    a, b = _job("a.tif", 0.5), _job("b.tif", 0.6123456)
    picked = ib.pick_job_for_date([a, b], 0.612346, {"a.tif": _interior(0.9), "b.tif": _interior(0.2)})
    assert picked is b  # the match wins even with the lower valid fraction


def test_pick_job_falls_back_to_best_fraction_and_republishes_its_stats():
    a, b = _job("a.tif", 0.5), _job("b.tif", 0.6)
    interiors = {"a.tif": _interior(0.3), "b.tif": _interior(0.9)}
    assert ib.pick_job_for_date([a, b], 0.99, interiors) is b
    assert ib.pick_job_for_date([a, b], None, interiors) is b
    assert ib.pick_job_for_date([a, b], 0.99, {"a.tif": _interior(0.3)}) is a  # b unreadable
    assert ib.pick_job_for_date([a, b], 0.99, {}) is None


class FakeBroker:
    def __init__(self, geometry=PARCEL_GEOJSON, published_value=0.5, published_fraction=None):
        self.geometry = geometry
        self.writes = []
        eid = fi._entity_id_for_acquisition(TENANT, PARCEL, DAY.isoformat())
        ndvi = {"type": "Property", "value": published_value}
        if published_fraction is not None:
            ndvi["interiorValidFraction"] = {"type": "Property", "value": published_fraction}
        self.products = {eid: {"id": eid, "type": "EOProduct", "ndvi": ndvi}}

    def parcel_geometry(self, tenant_id, parcel_urn):
        if self.geometry is None:
            raise ValueError(f"parcel {parcel_urn} has no geometry")
        return self.geometry

    def get_product(self, tenant_id, entity_id):
        return self.products.get(entity_id)

    def upsert(self, tenant_id, parcel_id, index_type, **kwargs):
        self.writes.append((index_type, kwargs))
        eid = fi._entity_id_for_acquisition(tenant_id, parcel_id, kwargs["sensing_date"].isoformat())
        attr = self.products[eid][index_type.lower()]
        attr["value"] = round(kwargs["statistics"]["mean"], 6)
        attr["interiorValidFraction"] = {"type": "Property",
                                         "value": round(kwargs["interior"]["valid_fraction"], 4)}
        return eid


@pytest.fixture
def rasters():
    return {"x/NDVI.tif": _tif_bytes()}


def _reader(rasters):
    def read(path):
        if path not in rasters:
            raise FileNotFoundError(path)
        return rasters[path]
    return read


@pytest.fixture
def one_job(monkeypatch):
    monkeypatch.setattr(ib, "_load_jobs", lambda db, tenant, parcel=None: [_job("x/NDVI.tif", 0.5)])


def test_dry_run_never_writes(one_job, rasters):
    broker = FakeBroker()
    report = ib.backfill_tenant(None, TENANT, reader=_reader(rasters), broker=broker)
    assert report.updated == 1 and broker.writes == []


def test_apply_writes_interior_and_is_idempotent(one_job, rasters):
    broker = FakeBroker()
    first = ib.backfill_tenant(None, TENANT, apply=True, reader=_reader(rasters), broker=broker)
    assert (first.updated, first.unchanged) == (1, 0)
    index, kwargs = broker.writes[0]
    assert index == "NDVI" and kwargs["interior"]["mask"] == "core"
    assert kwargs["raster_url"].endswith("/x/NDVI.tif") and kwargs["preview_url"].endswith("/x/NDVI.png")
    assert "parcel_cloud_fraction" not in kwargs or kwargs["parcel_cloud_fraction"] is None
    second = ib.backfill_tenant(None, TENANT, apply=True, reader=_reader(rasters), broker=broker)
    assert (second.updated, second.unchanged) == (0, 1)
    assert len(broker.writes) == 1


def test_missing_raster_counted(one_job):
    broker = FakeBroker()
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=_reader({}), broker=broker)
    assert report.missing_raster == 1 and report.updated == 0 and broker.writes == []


def test_parcel_without_geometry_is_skipped(one_job, rasters):
    report = ib.backfill_tenant(None, TENANT, apply=True, reader=_reader(rasters),
                                broker=FakeBroker(geometry=None))
    assert report.no_geometry == 1 and report.updated == 0


def test_fallback_republishes_stats_of_the_chosen_scene(monkeypatch, rasters):
    other = _tif_bytes(nan_block=0)  # fuller interior than the first raster
    rasters["y/NDVI.tif"] = other
    monkeypatch.setattr(ib, "_load_jobs", lambda db, tenant, parcel=None: [
        _job("x/NDVI.tif", 0.5), _job("y/NDVI.tif", 0.7)])
    broker = FakeBroker(published_value=0.99)  # matches neither scene
    ib.backfill_tenant(None, TENANT, apply=True, reader=_reader(rasters), broker=broker)
    _, kwargs = broker.writes[0]
    assert kwargs["statistics"]["mean"] == 0.7 and kwargs["interior"]["valid_fraction"] == 1.0
    assert kwargs["raster_url"].endswith("/y/NDVI.tif")
