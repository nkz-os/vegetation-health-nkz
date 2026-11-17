"""Zone products for the current (multi-date) zoning of a parcel."""
import asyncio
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
from rasterio.transform import from_origin

import app.services.zone_products as zp
from app.services.fiware_integration import _zone_base_entity
from app.services.processor import VegetationIndexProcessor
from app.services.zone_products import fetch_current_zones, zone_eo_product_id
from tests.fake_orion import FakeAsyncOrion

P1 = "urn:ngsi-ld:AgriParcel:p1"
RUN = "abcdef12" + "0" * 24
OLD_RUN = "99999999" + "0" * 24
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}


def _amz(n, run, method, date_zoned, level=None, level_key="zoneLevel"):
    e = {
        "id": f"urn:ngsi-ld:AgriManagementZone:p1:{run[:8]}:{n}",
        "type": "AgriManagementZone",
        "hasAgriParcel": {"type": "Relationship", "object": P1},
        "nkz:zoningRunId": {"type": "Property", "value": run},
        "nkz:zoningMethod": {"type": "Property", "value": method},
        "nkz:dateZoned": {"type": "Property", "value": date_zoned},
        "location": {"type": "GeoProperty", "value": SQUARE},
    }
    if level is not None:
        e[level_key] = {"type": "Property", "value": level}
    return e


def _fetch(entities):
    class _Sync:
        def __init__(self, tenant_id):
            self.fake = FakeAsyncOrion(tenant_id)
            self.fake.entities.extend(entities)

        def query_entities(self, **kw):
            return asyncio.run(self.fake.query_entities(**kw))

    with patch.object(zp, "SyncOrionClient", _Sync):
        return fetch_current_zones("acme", P1)


def test_current_zones_from_multidate_vigente():
    zones = _fetch([
        _amz(1, RUN, "vegetationIndexMultiDate", "2026-06-01", "high"),
        _amz(2, RUN, "vegetationIndexMultiDate", "2026-06-01", "medium"),
        _amz(3, OLD_RUN, "manual", "2026-07-01", "high"),
    ])
    assert [z["zone_id"] for z in sorted(zones, key=lambda z: z["level"])] == ["abcdef12-high", "abcdef12-medium"]
    assert {z["relation"] for z in zones} == {"hasAgriManagementZone"}
    z = next(z for z in zones if z["level"] == "high")
    assert z["zone_urn"] == f"urn:ngsi-ld:AgriManagementZone:p1:abcdef12:1"
    assert z["zone_version"] == z["run_id"] == RUN
    assert z["geometry"] == SQUARE


def test_current_zones_empty_when_vigente_not_multidate(caplog):
    with caplog.at_level("INFO", logger="app.services.zone_products"):
        assert _fetch([_amz(1, RUN, "vegetationIndex", "2026-06-01", "high")]) == []
    assert "reason=method=vegetationIndex" in caplog.text and P1 in caplog.text


def test_current_zones_empty_without_zoning_logs_reason(caplog):
    with caplog.at_level("INFO", logger="app.services.zone_products"):
        assert _fetch([]) == []
    assert "reason=no_zoning" in caplog.text


def test_current_zones_empty_when_level_missing_or_duplicated():
    m = "vegetationIndexMultiDate"
    assert _fetch([_amz(1, RUN, m, "2026-06-01", "high"), _amz(2, RUN, m, "2026-06-01")]) == []
    assert _fetch([_amz(1, RUN, m, "2026-06-01", "high"), _amz(2, RUN, m, "2026-06-01", "high")]) == []


def test_current_zones_reads_level_in_three_forms():
    m = "vegetationIndexMultiDate"
    zones = _fetch([
        _amz(1, RUN, m, "2026-06-01", "high", "zoneLevel"),
        _amz(2, RUN, m, "2026-06-01", "medium", "nkz:zoneLevel"),
        _amz(3, RUN, m, "2026-06-01", "low", "https://nkz-os.org/ns/zoneLevel"),
    ])
    assert {z["level"] for z in zones} == {"high", "medium", "low"}


def test_current_zones_orion_failure_is_empty():
    class _Boom:
        def __init__(self, tenant_id):
            pass

        def query_entities(self, **kw):
            raise RuntimeError("down")

    with patch.object(zp, "SyncOrionClient", _Boom):
        assert fetch_current_zones("acme", P1) == []


def test_zone_targets_are_terrain_units_plus_current():
    unit = {"zone_id": "u1", "relation": "hasAgriParcelZone"}
    cur = {"zone_id": "abcdef12-high", "relation": "hasAgriManagementZone"}
    with patch.object(zp, "fetch_management_zones", lambda t, p: [unit]), \
            patch.object(zp, "fetch_current_zones", lambda t, p: [cur]):
        assert zp.fetch_zone_targets("acme", P1) == [unit, cur]


def test_zone_entity_relation_and_level():
    zone = {"zone_id": "abcdef12-high", "zone_urn": "urn:ngsi-ld:AgriManagementZone:p1:abcdef12:L1",
            "zone_version": RUN, "relation": "hasAgriManagementZone", "level": "high",
            "run_id": RUN, "geometry": SQUARE}
    e = _zone_base_entity("urn:x", zone, {"pixel_count": 30, "valid_fraction": 1.0})
    assert e["hasAgriManagementZone"]["object"] == zone["zone_urn"] and "hasAgriParcelZone" not in e
    assert e["nkz:zoneLevel"]["value"] == "high" and e["nkz:zoningRunId"]["value"] == RUN
    assert e["nkz:zoneId"]["value"] == "abcdef12-high" and e["nkz:zoneVersion"]["value"] == RUN


def test_zone_entity_terrain_unit_unchanged():
    zone = {"zone_id": "z1", "zone_urn": "urn:ngsi-ld:AgriParcelZone:p1:z1", "zone_version": 3, "geometry": SQUARE}
    e = _zone_base_entity("urn:x", zone, {"pixel_count": 30, "valid_fraction": 1.0})
    assert e["hasAgriParcelZone"]["object"] == zone["zone_urn"]
    assert "hasAgriManagementZone" not in e and "nkz:zoneLevel" not in e and "nkz:zoningRunId" not in e


def test_product_id_keeps_zone_segment():
    assert zone_eo_product_id("acme", "p1", "abcdef12-high", date(2026, 6, 13)) == \
        "urn:ngsi-ld:EOProduct:acme:zone:p1:abcdef12-high:2026-06-13"


def test_parcel_series_skips_management_zone_products():
    from app.api import timeseries_adapter as ts

    when = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rel = {"type": "Relationship", "object": "urn:z"}
    rows = [{"observed_at": when, "payload": {"ndvi": {"value": 0.7}}}] + [
        {"observed_at": when, "payload": {"ndvi": {"value": 0.2}, key: rel}}
        for key in ("hasAgriManagementZone", "nkz:hasAgriManagementZone",
                    "https://nkz-os.org/ns/hasAgriManagementZone", "hasAgriParcelZone")
    ]
    cur = MagicMock(); cur.fetchall.return_value = rows
    conn = MagicMock(); conn.cursor.return_value = cur
    with patch("psycopg2.connect", return_value=conn):
        pts = asyncio.run(ts._history_from_telemetry_events(
            "acme", "urn:ngsi-ld:AgriParcel:acme:p1", "NDVI",
            datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc)))
    assert [p["mean"] for p in pts] == [0.7]


def test_parcel_series_skips_terrain_zone_products_all_forms():
    from app.api import timeseries_adapter as ts

    when = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rel = {"type": "Relationship", "object": "urn:z"}
    rows = [{"observed_at": when, "payload": {"ndvi": {"value": 0.7}}}] + [
        {"observed_at": when, "payload": {"ndvi": {"value": 0.2}, key: rel}}
        for key in ("hasAgriParcelZone", "nkz:hasAgriParcelZone",
                    "https://nkz-os.org/ns/hasAgriParcelZone")
    ]
    cur = MagicMock(); cur.fetchall.return_value = rows
    conn = MagicMock(); conn.cursor.return_value = cur
    with patch("psycopg2.connect", return_value=conn):
        pts = asyncio.run(ts._history_from_telemetry_events(
            "acme", "urn:ngsi-ld:AgriParcel:acme:p1", "NDVI",
            datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc)))
    assert [p["mean"] for p in pts] == [0.7]


class _GridProc(VegetationIndexProcessor):
    """Real create_geometry_mask / calculate_statistics on a synthetic 1-degree grid."""

    def __init__(self, size):
        self.band_meta = {"transform": from_origin(0, size, 1, 1), "crs": "EPSG:4326",
                          "height": size, "width": size}


def _box(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def test_zone_stats_unbuffered_mask():
    size = 9
    arr = np.full((size, size), 0.2)
    arr[2:7, 2:7] = 0.8  # inner zone: 5x5 pixels (x 2..7, y 2..7) inside the outer ring
    proc = _GridProc(size)
    inner = _box(2, 2, 7, 7)
    exact = zp.zone_statistics(proc, arr, inner, exact=True)
    assert exact["pixel_count"] == 25 and abs(exact["mean"] - 0.8) < 1e-9
    # the legacy buffered mask (10 "m" = 10 degrees here) swallows the ring
    buffered = zp.zone_statistics(proc, arr, inner)
    assert buffered["pixel_count"] > 25


def _hole_polygon():
    """Outer 9x9 zone whose hole is the inner 5x5 zone (nested levels)."""
    outer = _box(0, 0, 9, 9)["coordinates"][0]
    hole = _box(2, 2, 7, 7)["coordinates"][0]
    return {"type": "Polygon", "coordinates": [outer, hole]}


def test_zone_stats_unbuffered_mask_with_hole():
    size = 9
    arr = np.full((size, size), 0.2)
    arr[2:7, 2:7] = 0.8
    stats = zp.zone_statistics(_GridProc(size), arr, _hole_polygon(), exact=True)
    assert stats["pixel_count"] == size * size - 25
    assert abs(stats["mean"] - 0.2) < 1e-9


def test_sar_zone_stats_exact_excludes_hole(tmp_path):
    import rasterio
    from shapely.geometry import shape
    from app.tasks import sar_tasks

    size = 9
    data = np.full((size, size), -10.0, dtype="float32")
    data[2:7, 2:7] = -5.0
    path = tmp_path / "vv.tif"
    with rasterio.open(path, "w", driver="GTiff", height=size, width=size, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(0, size, 1, 1)) as dst:
        dst.write(data, 1)
    # hole edges fall mid-pixel (as real zone borders do): all_touched would take them
    hole = _box(2.4, 2.4, 6.6, 6.6)["coordinates"][0]
    geom = shape({"type": "Polygon", "coordinates": [_box(0, 0, 9, 9)["coordinates"][0], hole]})
    exact = sar_tasks._zone_sigma0_stats(str(path), geom, exact=True)
    assert exact["pixel_count"] == size * size - 25
    assert exact["mean"] < -9.9  # no inner-level pixels (-5 dB) leak in
    touched = sar_tasks._zone_sigma0_stats(str(path), geom)  # terrain-unit behaviour
    assert touched["pixel_count"] > exact["pixel_count"]
