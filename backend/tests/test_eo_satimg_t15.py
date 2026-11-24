"""Whole-phase review fixes (T1.5): optical winner per take, sync upserts, delete guards, retry, baseline, radar/LST."""
import logging
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
for _m in ("rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows", "rasterio.control",
           "rasterio.transform", "rasterio.mask", "simpleeval"):
    sys.modules.setdefault(_m, MagicMock())

import pytest

from app.services import eo_satimg_optical as so
from app.services import fiware_integration as fi
from tests import test_eo_satimg_optical_writers as ow

PID_A, PID_B, PARCEL_ID, TENANT = ow.PID_A, ow.PID_B, ow.PARCEL_ID, ow.TENANT


@pytest.fixture
def store(monkeypatch):
    s = ow.Store()
    monkeypatch.setattr(fi, "get_eo_satellite_imagery", s.get)
    monkeypatch.setattr(fi, "upsert_eo_satellite_imagery", s.upsert)
    monkeypatch.setattr(fi, "delete_eo_satellite_imagery", s.delete)
    monkeypatch.setattr(fi, "delete_eo_attribute", s.delete_attr)
    monkeypatch.setenv("EO_SATIMG_WRITE_TENANTS", TENANT)
    monkeypatch.setenv("VEGETATION_COG_BUCKET", "bucket")
    return s


def _vals(store):
    return ow._analytics(store.entities[PARCEL_ID])


# ── I1 ──────────────────────────────────────────────────────────────────────

def test_i1_losing_scene_cannot_replace_through_another_index(store):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.80)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.84)
    ow._write_scene(PID_A, "NDMI", 0.4, 0.84)
    assert ow._write_scene(PID_B, "NDVI", 0.5, 0.79) == so.SKIP
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.85) == so.SKIP  # not the reference index
    assert ow._write_scene(PID_B, "NDMI", 0.45, 0.85) == so.SKIP
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_A
    values = _vals(store)
    assert {"ndvi", "ndre", "ndmi"} <= set(values) and values["ndvi"] == "0.600000"


def test_i1_reference_index_arriving_last_replaces_whole_entity(store, caplog):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.80)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.84)
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.95) == so.SKIP
    with caplog.at_level(logging.INFO):
        assert ow._write_scene(PID_B, "NDVI", 0.7, 0.90) == so.REPLACE
    entity = store.entities[PARCEL_ID]
    assert entity["productID"]["value"] == PID_B
    values = _vals(store)
    assert values["ndvi"] == "0.700000" and "ndre" not in values  # nothing of A survives
    assert any("ndre" in r.getMessage() and "dropped" in r.getMessage() for r in caplog.records)


def test_i1_better_scene_ends_entirely_from_b(store):
    ow._write_scene(PID_A, "NDVI", 0.6, 0.70)
    ow._write_scene(PID_A, "NDRE", 0.5, 0.70)
    assert ow._write_scene(PID_B, "NDVI", 0.7, 0.90) == so.REPLACE
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.91) == so.MERGE
    entity = store.entities[PARCEL_ID]
    values = _vals(store)
    assert entity["productID"]["value"] == PID_B
    assert values["ndvi"] == "0.700000" and values["ndre"] == "0.550000"


def test_i1_stored_without_fraction_keeps_today_semantics_for_reference_only(store):
    ow._write_scene(PID_A, "NDVI", 0.6)
    ow._write_scene(PID_A, "NDRE", 0.5)
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.9) == so.SKIP
    assert ow._write_scene(PID_B, "NDVI", 0.7, 0.9) == so.REPLACE  # nothing to defend on the reference index
    assert store.entities[PARCEL_ID]["productID"]["value"] == PID_B


def test_i1_scene_over_statistical_api_only_through_reference_index(store):
    day = ow.DAY
    for idx, mean in (("NDVI", 0.6), ("NDRE", 0.5)):
        analytics = so.index_analytics(idx, {**ow.STATS, "mean": mean})
        analytics["pixelCount"] = 40
        so.write_optical(TENANT, so.parcel_target(ow.PARCEL, ow.GEOM), so.no_scene_take(day), analytics,
                         index_type=idx)
    assert ow._write_scene(PID_B, "NDRE", 0.55, 0.9) == so.SKIP
    assert store.entities[PARCEL_ID].get("productID") is None
    assert ow._write_scene(PID_B, "NDVI", 0.7, 0.9) == so.REPLACE
