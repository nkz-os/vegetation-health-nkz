"""Per-zone statistics, zone discovery and per-zone EOProduct publication."""
import asyncio
from datetime import date, datetime, timezone
from unittest.mock import patch

import numpy as np

import app.services.fiware_integration as fi
import app.services.zone_products as zp
from app.services.processor import VegetationIndexProcessor
from tests.fake_orion import FakeAsyncOrion

PARCEL = "urn:ngsi-ld:AgriParcel:acme:p1"


class _Proc(VegetationIndexProcessor):
    """Real calculate_statistics; the zone mask is injected (no rasterio)."""

    def __init__(self, inside):
        self._inside = inside

    def create_geometry_mask(self, geometry):
        return ~self._inside  # True = excluded


def _zone_inside(n_pixels, shape=(10, 10)):
    inside = np.zeros(shape, dtype=bool)
    inside.flat[:n_pixels] = True
    return inside


class TestZoneStatistics:
    def test_too_few_pixels_is_none(self):
        arr = np.full((10, 10), 0.5)
        assert zp.zone_statistics(_Proc(_zone_inside(15)), arr, {"type": "Polygon"}) is None

    def test_low_valid_fraction_is_none(self):
        arr = np.full((10, 10), np.nan)
        arr.flat[:12] = 0.5  # 12 valid of 30 = 40 %
        assert zp.zone_statistics(_Proc(_zone_inside(30)), arr, {"type": "Polygon"}) is None

    def test_enough_valid_returns_stats(self):
        arr = np.full((10, 10), np.nan)
        arr.flat[:24] = 0.5  # 24 valid of 30 = 80 %
        stats = zp.zone_statistics(_Proc(_zone_inside(30)), arr, {"type": "Polygon"})
        assert stats["pixel_count"] == 24
        assert abs(stats["valid_fraction"] - 0.8) < 1e-9
        assert stats["mean"] == 0.5

    def test_mask_on_coarser_grid_is_resampled(self):
        arr = np.full((20, 20), 0.4)
        stats = zp.zone_statistics(_Proc(_zone_inside(30)), arr, {"type": "Polygon"})
        assert stats is not None and stats["pixel_count"] >= 20


def _zone_entity(zid, ztype, parcel_attr="hasAgriParcel", parcel=PARCEL, type_key="nkz:zoneType"):
    return {
        "id": f"urn:ngsi-ld:AgriParcelZone:acme:p1:{zid}",
        "type": "AgriParcelZone",
        type_key: {"type": "Property", "value": ztype},
        "nkz:zoneId": {"type": "Property", "value": zid},
        "nkz:zoneVersion": {"type": "Property", "value": 3},
        "location": {"type": "GeoProperty", "value": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
        parcel_attr: {"type": "Relationship", "object": parcel},
    }


class TestFetchManagementZones:
    def _fetch(self, entities):
        fake_holder = {}

        class _Fake(FakeAsyncOrion):
            def __init__(self, tenant_id, *a, **k):
                super().__init__(tenant_id)
                self.entities.extend(entities)
                fake_holder["f"] = self

        with patch.object(zp, "OrionClient", _Fake):
            return zp.fetch_management_zones("acme", PARCEL)

    def test_management_only_all_spellings_and_legacy_relationship(self):
        zones = self._fetch([
            _zone_entity("z1", "management"),
            _zone_entity("z2", "management", parcel_attr="refAgriParcel", type_key="zoneType"),
            _zone_entity("z3", "management", type_key="https://nkz-os.org/ns/zoneType"),
            _zone_entity("z4", "sampling"),
            _zone_entity("z5", "management", parcel="urn:ngsi-ld:AgriParcel:acme:other"),
        ])
        assert sorted(z["zone_id"] for z in zones) == ["z1", "z2", "z3"]
        z1 = next(z for z in zones if z["zone_id"] == "z1")
        assert z1["zone_urn"] == "urn:ngsi-ld:AgriParcelZone:acme:p1:z1"
        assert z1["zone_version"] == 3
        assert z1["geometry"]["type"] == "Polygon"

    def test_orion_failure_is_empty_list(self):
        class _Boom(FakeAsyncOrion):
            async def query_entities(self, *a, **k):
                raise RuntimeError("down")

        with patch.object(zp, "OrionClient", _Boom):
            assert zp.fetch_management_zones("acme", PARCEL) == []


ZONE = {"zone_id": "z1", "zone_urn": "urn:ngsi-ld:AgriParcelZone:acme:p1:z1",
        "zone_version": 3, "geometry": {"type": "Polygon"}}
STATS = {"mean": 0.61, "min": 0.4, "max": 0.8, "std": 0.05, "pixel_count": 42, "valid_fraction": 0.9}


class TestZoneUpserts:
    def test_index_entity_shape(self):
        with patch.object(fi, "OrionClient", FakeAsyncOrion):
            eid = fi.upsert_eo_zone_index("acme", "p1", ZONE, date(2026, 6, 1), "ndvi", STATS, cloud_cover=3.0)
            body = FakeAsyncOrion.last_instance.entities[0]
        assert eid == body["id"] == "urn:ngsi-ld:EOProduct:acme:p1:z1:2026-06-01"
        assert "hasAgriParcel" not in body
        assert body["hasAgriParcelZone"] == {"type": "Relationship", "object": ZONE["zone_urn"]}
        assert body["nkz:zoneId"]["value"] == "z1"
        assert body["nkz:zoneVersion"]["value"] == 3
        assert body["pixelCount"]["value"] == 42
        assert body["nkz:validPixelFraction"]["value"] == 0.9
        assert body["source"]["value"] == "vegetation_health"
        assert body["ndvi"]["value"] == 0.61 and body["ndvi"]["min"]["value"] == 0.4
        assert "rasterUrl" not in body["ndvi"] and "previewUrl" not in body["ndvi"]

    def test_sar_entity_shape(self):
        stats = {"pixel_count": 30, "valid_fraction": 1.0}
        acq = datetime(2026, 6, 1, tzinfo=timezone.utc)
        with patch.object(fi, "OrionClient", FakeAsyncOrion):
            eid = fi.upsert_eo_zone_product("acme", "p1", ZONE, acq, -12.5, -18.0, stats)
            body = FakeAsyncOrion.last_instance.entities[0]
        assert eid == "urn:ngsi-ld:EOProduct:acme:p1:z1:GRD:2026-06-01"
        assert "hasAgriParcel" not in body
        assert body["hasAgriParcelZone"]["object"] == ZONE["zone_urn"]
        assert body["productType"]["value"] == "GRD"
        assert body["backscatterVV"]["value"] == -12.5
        assert body["backscatterVH"]["value"] == -18.0
        assert body["pixelCount"]["value"] == 30
        assert "acquisitionDate" in body


def test_parcel_query_returns_only_parcel_product():
    """Zone products never answer a hasAgriParcel query."""
    broker = FakeAsyncOrion("acme")

    class _Shared(FakeAsyncOrion):
        def __init__(self, tenant_id, *a, **k):
            super().__init__(tenant_id)
            self.entities = broker.entities

    with patch.object(fi, "OrionClient", _Shared):
        fi.upsert_eo_index("acme", PARCEL, "NDVI", STATS, date(2026, 6, 1))
        fi.upsert_eo_zone_index("acme", "p1", ZONE, date(2026, 6, 1), "ndvi", STATS)
        fi.upsert_eo_zone_index("acme", "p1", {**ZONE, "zone_id": "z2", "zone_urn": ZONE["zone_urn"][:-2] + "z2"},
                                date(2026, 6, 1), "ndvi", STATS)
    assert len(broker.entities) == 3
    found = asyncio.run(broker.query_entities(type="EOProduct", q=f'hasAgriParcel=="{PARCEL}"'))
    assert len(found) == 1
    assert found[0]["id"].endswith(":p1:2026-06-01")


def test_sar_zone_sigma0_from_calibrated_parcel_raster(tmp_path):
    import math
    import rasterio
    from shapely.geometry import box
    from tests.test_sar_calibrated_parcel import LUT, _grd
    from app.tasks import sar_tasks

    band, xml = tmp_path / "vv.tif", tmp_path / "vv.xml"
    _grd(band)
    xml.write_bytes(LUT)
    parcel = box(-1.57, 42.83, -1.53, 42.87)
    _, raster = sar_tasks._calibrated_parcel(str(band), str(xml), parcel, str(tmp_path))
    stats = sar_tasks._zone_sigma0_stats(raster, box(-1.57, 42.83, -1.55, 42.85))
    assert stats is not None and stats["pixel_count"] >= 20
    assert math.isclose(stats["mean"], 10 * math.log10(0.25), abs_tol=0.05)
    assert stats["valid_fraction"] > 0.9
    # a sliver zone has too few pixels
    assert sar_tasks._zone_sigma0_stats(raster, box(-1.57, 42.83, -1.5698, 42.8302)) is None
