"""Zoning versions and notes: models and their migration."""
import re
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "016_zoning_versions.sql"


def test_models_map_columns():
    from app.models import ZoningNote, ZoningVersion

    versions = ZoningVersion.__table__.columns
    assert ZoningVersion.__tablename__ == "zoning_versions"
    assert [c.name for c in versions if c.primary_key] == ["run_id"]
    for name in ("tenant_id", "parcel_id", "status", "source", "derived_from", "index_type",
                 "outcome_reason", "entities", "job_id", "created_at", "accepted_at", "accepted_by"):
        assert name in versions, name
    for name in ("tenant_id", "parcel_id", "status", "source", "entities"):
        assert not versions[name].nullable, name
    assert versions["accepted_at"].type.timezone is True

    notes = ZoningNote.__table__.columns
    assert ZoningNote.__tablename__ == "zoning_notes"
    for name in ("id", "tenant_id", "parcel_id", "zone_id", "geometry", "cause", "created_at", "created_by"):
        assert name in notes, name
    assert [c.name for c in notes if c.primary_key] == ["id"]


def test_migration_is_idempotent_sql():
    sql = MIGRATION.read_text()
    creates = re.findall(r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(\S+)", sql, re.I)
    assert creates and all(c.upper() == "IF" for c in creates), creates
    assert re.search(r"UNIQUE INDEX IF NOT EXISTS \w+\s+ON zoning_versions \(tenant_id, parcel_id\)\s+WHERE status = 'current'", sql)
    assert re.search(r"UNIQUE INDEX IF NOT EXISTS \w+\s+ON zoning_versions \(tenant_id, parcel_id\)\s+WHERE status = 'pending'", sql)
    assert "CHECK (status IN ('current', 'pending', 'superseded', 'discarded'))" in sql
    assert "CHECK (source IN ('proposal', 'merge'))" in sql
    assert "char_length(cause) BETWEEN 1 AND 500" in sql
    for table in ("zoning_versions", "zoning_notes"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert re.search(rf"policyname = 'tenant_isolation_{table}'", sql)
