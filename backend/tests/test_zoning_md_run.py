"""Multi-date zoning: orchestration (dates -> proposal -> broker) with fake sources."""
import io
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from app.jobs.zoning_multidate.dates import RasterRow
from app.jobs.zoning_multidate.run import Sources, choose_index, propose_zoning, vra_job_result

PARCEL_URN = "urn:ngsi-ld:AgriParcel:acme-p1"
PARCEL = {"type": "Polygon", "coordinates": [[
    [-1.6510, 42.8000], [-1.6480, 42.8000], [-1.6480, 42.8020], [-1.6510, 42.8020], [-1.6510, 42.8000],
]]}
TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)


def _tif(level, split=True):
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True)
    minx, miny = to_utm.transform(-1.6515, 42.7995)
    maxx, maxy = to_utm.transform(-1.6475, 42.8025)
    x0, y0 = np.floor(minx / 10) * 10, np.ceil(maxy / 10) * 10
    w, h = int((maxx - x0) // 10) + 1, int((y0 - miny) // 10) + 1
    data = np.full((h, w), level, dtype="float32")
    if split:
        data[:, : w // 2] *= 1.25
        data[:, w // 2:] *= 0.8
    buf = io.BytesIO()
    with rasterio.open(buf, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
                       crs="EPSG:32630", transform=from_origin(x0, y0, 10, 10)) as dst:
        dst.write(data, 1)
    return buf.getvalue()


def _row(i, pending=False):
    return RasterRow(job_id=f"j{i}", sensing_date=date(2026, 4, 1 + i), raster_path=None if pending else f"r{i}.tif",
                     pending=pending, created_at=datetime(2026, 4, 2 + i))


def _sources(rows, tifs, species=None):
    return Sources(
        rows=lambda index: rows,
        raster_bytes=lambda row: tifs.get(row.job_id),
        parcel_geometry=lambda: PARCEL,
        crop_species=lambda: species,
    )


def _fiware(existing=()):
    fw = MagicMock()
    fw.context_broker_url = "http://broker"
    fw.create_entity.return_value = True
    fw.delete_entity.return_value = True
    resp = MagicMock()
    resp.json.return_value = list(existing)
    resp.raise_for_status.return_value = None
    fw.session.get.return_value = resp
    return fw


def test_proposal_publishes_and_retires_old_automatic():
    rows = [_row(i) for i in range(4)]
    tifs = {r.job_id: _tif(0.6) for r in rows}
    fw = _fiware([{"id": "old", "zoningMethod": "vegetationIndex", "zoningRunId": "r0"}])
    res = propose_zoning(_sources(rows, tifs), fw, PARCEL_URN, None, TODAY, NOW)
    assert res.status == "zones" and res.reason is None
    assert {z.level for z in res.zones} == {"high", "low"}
    assert fw.create_entity.call_count == len(res.zones)
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == ["old"]
    assert sum(z.area_ha for z in res.zones) == pytest.approx(res.parcel_area_ha, rel=0.005)


def test_not_enough_dates_is_no_zones_and_retires():
    rows = [_row(i) for i in range(2)]
    tifs = {r.job_id: _tif(0.6) for r in rows}
    fw = _fiware([{"id": "old", "zoningMethod": "vegetationIndex", "zoningRunId": "r0"}])
    res = propose_zoning(_sources(rows, tifs), fw, PARCEL_URN, None, TODAY, NOW)
    assert (res.status, res.reason) == ("no_zones", "not_enough_dates")
    fw.create_entity.assert_not_called()
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == ["old"]


def test_homogeneous_is_no_zones():
    rows = [_row(i) for i in range(4)]
    tifs = {r.job_id: _tif(0.6, split=False) for r in rows}
    res = propose_zoning(_sources(rows, tifs), _fiware(), PARCEL_URN, None, TODAY, NOW)
    assert (res.status, res.reason) == ("no_zones", "homogeneous")


def test_pending_failure_is_counted_not_fatal():
    rows = [_row(i) for i in range(4)] + [_row(9, pending=True)]
    tifs = {r.job_id: _tif(0.6) for r in rows[:4]}  # j9 cannot be materialised
    res = propose_zoning(_sources(rows, tifs), _fiware(), PARCEL_URN, None, TODAY, NOW)
    assert res.status == "zones"
    assert res.dropped["pending_failed"] == 1
    assert len(res.dates_used) == 4


def test_choose_index_by_crop():
    assert choose_index(None, "Olea europaea") == "OSAVI"
    assert choose_index(None, "Triticum aestivum") == "NDVI"
    assert choose_index(None, None) == "NDVI"
    assert choose_index("NDRE", "Olea europaea") == "NDRE"
    with pytest.raises(ValueError):
        choose_index("NDMI", None)


def test_vra_job_result_keeps_legacy_properties():
    rows = [_row(i) for i in range(4)]
    tifs = {r.job_id: _tif(0.6) for r in rows}
    res = propose_zoning(_sources(rows, tifs), _fiware(), PARCEL_URN, None, TODAY, NOW)
    out = vra_job_result(res)
    assert out["status"] == "zones" and out["index_type"] == "VRA_ZONES" and out["zoning_source"] == "NDVI"
    f = out["geojson"]["features"][0]["properties"]
    for key in ("zone_id", "cluster_id", "zone_class", "mean_value", "area_ha", "prescription_rate",
                "zone_level", "relative_pct", "confirming_dates", "total_dates"):
        assert key in f
    assert f["cluster_id"] == f["zone_id"] - 1


def test_vra_job_result_no_zones():
    res = propose_zoning(_sources([], {}), _fiware(), PARCEL_URN, None, TODAY, NOW)
    out = vra_job_result(res)
    assert out["status"] == "no_zones" and out["reason"] == "not_enough_dates"
    assert out["zones_created"] == 0 and out["geojson"]["features"] == []
