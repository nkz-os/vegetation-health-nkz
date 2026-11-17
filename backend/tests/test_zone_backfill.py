"""Zone products are filled for the current zoning when it changes."""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from affine import Affine
from pyproj import Transformer

import app.services.zone_backfill as zb

P1 = "urn:ngsi-ld:AgriParcel:p1"
RUN = "abcdef12" + "0" * 24
# ~300 m square near lon -3, lat 40 (UTM 30N)
PARCEL = {"type": "Polygon", "coordinates": [[[-3.0, 40.0], [-2.9965, 40.0], [-2.9965, 40.0027],
                                             [-3.0, 40.0027], [-3.0, 40.0]]]}
LEFT = {"type": "Polygon", "coordinates": [[[-3.0, 40.0], [-2.99825, 40.0], [-2.99825, 40.0027],
                                           [-3.0, 40.0027], [-3.0, 40.0]]]}
RIGHT = {"type": "Polygon", "coordinates": [[[-2.99825, 40.0], [-2.9965, 40.0], [-2.9965, 40.0027],
                                            [-2.99825, 40.0027], [-2.99825, 40.0]]]}
DAYS = [date(2026, 10, d) for d in (1, 5, 9, 13, 17)]


def _row(day, path="x.tif", pending=False, created=1):
    return SimpleNamespace(job_id=f"j{day.day}", sensing_date=day, raster_path=path, pending=pending,
                           created_at=datetime(2026, 10, 20, created, tzinfo=timezone.utc))


def _zones():
    return [
        {"zone_id": "abcdef12-high", "zone_urn": "urn:ngsi-ld:AgriManagementZone:p1:abcdef12:L1",
         "zone_version": RUN, "geometry": LEFT, "relation": "hasAgriManagementZone",
         "level": "high", "run_id": RUN},
        {"zone_id": "abcdef12-medium", "zone_urn": "urn:ngsi-ld:AgriManagementZone:p1:abcdef12:L2",
         "zone_version": RUN, "geometry": RIGHT, "relation": "hasAgriManagementZone",
         "level": "medium", "run_id": RUN},
    ]


@pytest.fixture
def wired(monkeypatch):
    import app.jobs.zoning_multidate.dates as dates
    import app.services.fiware_integration as fi
    import app.services.zone_products as zp

    state = SimpleNamespace(rows=[_row(d) for d in DAYS], zones=_zones(), upserts=[])
    monkeypatch.setattr(zp, "fetch_current_zones", lambda tenant, parcel: state.zones)
    monkeypatch.setattr(dates, "load_raster_rows", lambda db, tenant, parcel, index: state.rows)
    monkeypatch.setattr(zb, "_parcel_geometry", lambda tenant, parcel: PARCEL)

    def upsert(tenant, short, zone, sensing_date, index_key, stats, cloud_cover=None):
        state.upserts.append(SimpleNamespace(tenant=tenant, short=short, zone=zone,
                                             sensing_date=sensing_date, index_key=index_key, stats=stats))
        return True

    monkeypatch.setattr(fi, "upsert_eo_zone_index", upsert)
    return state


def _constant(value):
    return lambda row, grid: np.full(grid.shape, value, dtype="float32")


def test_backfill_publishes_last_three_dates_per_current_zone(wired):
    n = zb.backfill_current_zone_products(None, "acme", P1, read_raster=_constant(0.6))
    assert n == 6
    assert {c.zone["zone_id"] for c in wired.upserts} == {"abcdef12-high", "abcdef12-medium"}
    assert sorted({c.sensing_date for c in wired.upserts}) == DAYS[2:]
    assert {c.index_key for c in wired.upserts} == {"ndvi"}
    assert {c.short for c in wired.upserts} == {"p1"}
    assert all(abs(c.stats["mean"] - 0.6) < 1e-5 and c.stats["valid_fraction"] == 1.0 for c in wired.upserts)


def test_backfill_skips_pending_rasters_and_zone_with_few_pixels(wired):
    wired.rows = [_row(DAYS[4], path=None, pending=True), _row(DAYS[3]), _row(DAYS[2])]

    def reader(row, grid):
        arr = np.full(grid.shape, 0.5, dtype="float32")
        # the right half of the parcel has no valid data: below the valid fraction
        to_utm = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
        x, _ = to_utm.transform(-2.99825, 40.0)
        col = int((x - grid.transform.c) / grid.transform.a)
        arr[:, col:] = np.nan
        return arr

    n = zb.backfill_current_zone_products(None, "acme", P1, last_n=3, read_raster=reader)
    assert n == 2  # pending date ignored (only 2 ready dates), one zone each
    assert {c.zone["zone_id"] for c in wired.upserts} == {"abcdef12-high"}
    assert sorted(c.sensing_date for c in wired.upserts) == [DAYS[2], DAYS[3]]


def test_backfill_latest_row_wins_per_date_and_unreadable_date_is_skipped(wired):
    wired.rows = [_row(DAYS[4], path="old.tif", created=1), _row(DAYS[4], path="new.tif", created=5),
                  _row(DAYS[3], path="bad.tif")]
    seen = []

    def reader(row, grid):
        seen.append(row.raster_path)
        if row.raster_path == "bad.tif":
            raise OSError("gone")
        return np.full(grid.shape, 0.4, dtype="float32")

    n = zb.backfill_current_zone_products(None, "acme", P1, read_raster=reader)
    assert n == 2
    assert seen == ["new.tif", "bad.tif"]


def test_backfill_noop_without_current_zoning(wired):
    wired.zones = []
    assert zb.backfill_current_zone_products(None, "acme", P1, read_raster=_constant(0.6)) == 0
    assert wired.upserts == []


def test_backfill_never_raises(wired, monkeypatch):
    monkeypatch.setattr(zb, "_parcel_geometry", lambda t, p: (_ for _ in ()).throw(RuntimeError("down")))
    assert zb.backfill_current_zone_products(None, "acme", P1, read_raster=_constant(0.6)) == 0


def test_accept_and_merge_enqueue_backfill(monkeypatch):
    from app.jobs.zoning_multidate import actions

    calls = []
    monkeypatch.setattr(actions, "_enqueue_backfill", lambda t, p: calls.append((t, p)))
    monkeypatch.setattr(actions, "_accept", lambda *a: ("v", True))
    assert actions.accept(None, None, "acme", P1, RUN, "u", None) == "v"
    assert calls == [("acme", P1)]

    calls.clear()
    monkeypatch.setattr(actions, "_accept", lambda *a: ("v", False))  # already-current run accepted as is
    actions.accept(None, None, "acme", P1, RUN, "u", None)
    assert calls == []


def test_merge_enqueues_backfill(zoning_db, monkeypatch):
    from app.jobs.zoning_multidate import actions
    from app.jobs.zoning_multidate import versions as v
    from tests.zoning_fakes import PARCEL as ZP, TENANT, FakeBroker, run_neighbours

    old = "a" * 32
    db = zoning_db()
    with v.parcel_lock(db, TENANT, ZP):
        v.record_current(db, TENANT, ZP, old, run_neighbours(old), source="proposal", index_type="NDVI",
                         accepted_at=datetime(2026, 11, 10, tzinfo=timezone.utc), accepted_by="u0")
    calls = []
    monkeypatch.setattr(actions, "_enqueue_backfill", lambda t, p: calls.append((t, p)))
    actions.merge(db, FakeBroker(run_neighbours(old)), TENANT, ZP, old, (1, 2), 2, "u1", datetime.now(timezone.utc))
    assert calls == [(TENANT, ZP)]
