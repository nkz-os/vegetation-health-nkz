"""The VRA pill is selectable exactly when the zoning layer has something to draw."""
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql


def _job(result):
    return SimpleNamespace(result=result)


def test_zoning_with_features_is_drawable():
    from app.services.zoning_layer import zoning_drawable
    job = _job({"index_type": "VRA_ZONES", "geojson": {"type": "FeatureCollection", "features": [{"type": "Feature"}]}})
    assert zoning_drawable(job) is True


def test_no_zones_result_or_missing_job_is_not_drawable():
    from app.services.zoning_layer import zoning_drawable
    assert zoning_drawable(None) is False
    assert zoning_drawable(_job(None)) is False
    assert zoning_drawable(_job({"status": "no_zones", "geojson": {"type": "FeatureCollection", "features": []}})) is False
    assert zoning_drawable(_job({"status": "no_zones"})) is False


def test_latest_zoning_query_skips_deleted_and_other_indices():
    from app.services.zoning_layer import latest_zoning_query

    class _Db:
        def query(self, model):
            from sqlalchemy.orm import Query
            return Query(model)

    sql = str(
        latest_zoning_query(_Db(), "acme", "urn:ngsi-ld:AgriParcel:p1").statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "deleted_at IS NULL" in sql
    assert "'VRA_ZONES'" in sql
    assert "'completed'" in sql
    assert "ORDER BY vegetation_jobs.created_at DESC" in sql


# ------------------------------------------------- the zoning in force (real Postgres)

def _seed_versions(dbf, *, current=None, pending=None, accepted=True):
    from datetime import datetime, timezone

    from app.jobs.zoning_multidate import versions as v
    from tests.zoning_fakes import PARCEL, TENANT, run
    db = dbf()
    with v.parcel_lock(db, TENANT, PARCEL):
        if current:
            v.record_current(db, TENANT, PARCEL, current, run(current), source="proposal", index_type="NDVI",
                             accepted_at=datetime(2026, 11, 10, tzinfo=timezone.utc) if accepted else None)
        if pending:
            v.record_pending(db, TENANT, PARCEL, pending, run(pending), index_type="NDVI",
                             outcome_reason=None, job_id="j", parked_on_run_id=current)
    return db


def test_layer_draws_the_current_version_not_the_pending_one(zoning_db, monkeypatch):
    from shapely.geometry import Point, shape

    from app.services import zoning_layer
    from tests.zoning_fakes import PARCEL, TENANT
    db = _seed_versions(zoning_db, current="a" * 32, pending="b" * 32)
    monkeypatch.setattr(zoning_layer, "latest_zoning_job", lambda *a: pytest.fail("job read with versions"))
    features = zoning_layer.zoning_features(db, TENANT, PARCEL)
    assert [f["properties"]["run_id"] for f in features] == ["a" * 32] * 2
    high, low = features
    p = high["properties"]
    assert (p["zone_level"], p["zone_id"], p["cluster_id"], p["zone_class"]) == ("high", 1, 0, "high")
    assert (p["relative_pct"], p["confirming_dates"], p["total_dates"], p["area_ha"]) == (112, 5, 6, 1.0)
    assert p["mean_value"] == 0.7 and "prescription_rate" in p and p["accepted"] is True
    assert shape(high["geometry"]).buffer(1e-9).contains(Point(*p["label_point"]))
    assert low["properties"]["technician_cause"] is None


def test_layer_is_empty_when_versions_exist_but_none_is_current(zoning_db, monkeypatch):
    from app.jobs.zoning_multidate import versions as v
    from app.services import zoning_layer
    from tests.zoning_fakes import PARCEL, TENANT
    db = _seed_versions(zoning_db, current="a" * 32, accepted=False)
    with v.parcel_lock(db, TENANT, PARCEL):
        v.supersede_current(db, TENANT, PARCEL)
    monkeypatch.setattr(zoning_layer, "latest_zoning_job", lambda *a: pytest.fail("job read with versions"))
    assert zoning_layer.zoning_features(db, TENANT, PARCEL) == []


def test_layer_falls_back_to_the_latest_job_without_versions(zoning_db, monkeypatch):
    from app.services import zoning_layer
    from tests.zoning_fakes import PARCEL, TENANT
    feature = {"type": "Feature", "geometry": None, "properties": {"zone_level": "high"}}
    job = _job({"geojson": {"type": "FeatureCollection", "features": [feature]}})
    monkeypatch.setattr(zoning_layer, "latest_zoning_job", lambda *a: job)
    assert zoning_layer.zoning_features(zoning_db(), TENANT, PARCEL) == [feature]
