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
