"""Migration 017 and `record` against a real Postgres (skipped without ZONING_PG_URL)."""
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "017_parcel_scenes.sql"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def parcel_scenes_db():
    """Engine + session factory with a minimal vegetation_jobs and an empty 017 table.

    Only drops/creates its own tables, so it can share the database with zoning_db.
    """
    url = os.getenv("ZONING_PG_URL")
    if not url:
        pytest.skip("ZONING_PG_URL not set")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_parcel_scenes, vegetation_jobs")
        conn.exec_driver_sql(
            "CREATE TABLE vegetation_jobs (id uuid PRIMARY KEY, tenant_id text, entity_id text, "
            "job_type text, status text, parameters jsonb, result jsonb, created_at timestamptz DEFAULT now())"
        )
    factory = sessionmaker(bind=engine)
    opened = []

    def make():
        s = factory()
        opened.append(s)
        return s

    yield engine, make
    for s in opened:
        s.close()
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_parcel_scenes, vegetation_jobs")
    engine.dispose()


def _job(conn, tenant, entity, job_type, status, parameters, result=None):
    import json
    conn.exec_driver_sql(
        "INSERT INTO vegetation_jobs (id, tenant_id, entity_id, job_type, status, parameters, result) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
        (str(uuid.uuid4()), tenant, entity, job_type, status,
         json.dumps(parameters), json.dumps(result) if result is not None else None),
    )


def _rows(conn):
    return conn.exec_driver_sql(
        "SELECT entity_id, scene_id, status, parcel_cloud_pct, sensing_date FROM vegetation_parcel_scenes "
        "ORDER BY entity_id, scene_id"
    ).fetchall()


def test_migration_is_idempotent_and_backfills(parcel_scenes_db):
    engine, _ = parcel_scenes_db
    with engine.begin() as conn:
        _job(conn, "acme", "parcel-a", "download", "completed", {"scene_id": "S1", "date": "2026-11-01"})
        _job(conn, "acme", "parcel-a", "download", "completed", {"scene_id": "S2", "date": "2026-11-02"},
             {"skipped_due_to_clouds": "true", "local_cloud_pct": 42.5})
        _job(conn, "acme", "parcel-b", "download", "failed", {"scene_id": "S1", "date": "2026-11-01"})
        _job(conn, "acme", "parcel-b", "download", "completed", {"scene_id": "S1", "date": "2026-11-01"})
        _job(conn, "acme", "parcel-a", "calculate_index", "completed", {"scene_id": "S1"}, {"raster_path": "x.tif"})
        _job(conn, "acme", "parcel-b", "calculate_index", "completed", {"scene_id": "S1"}, {"raster_path": "y.tif"})
        _job(conn, "acme", "parcel-c", "download", "completed", {"scene_id": "S3", "date": "n/a"},
             {"skipped_due_to_clouds": "true", "local_cloud_pct": "x"})
        _job(conn, "acme", "parcel-d", "download", "completed", {"scene_id": "S4", "date": "2026-02-31"})
        _job(conn, "acme", "parcel-e", "download", "completed", {"scene_id": "S5"},
             {"skipped_due_to_clouds": "true", "local_cloud_pct": "123456"})
        _job(conn, "acme", None, "download", "completed", {"scene_id": "S6"})
    with engine.begin() as conn:
        conn.exec_driver_sql(MIGRATION.read_text())
    with engine.begin() as conn:
        first = _rows(conn)
        conn.exec_driver_sql(MIGRATION.read_text())
        second = _rows(conn)
    assert first == second
    by = {(r[0], r[1]): r for r in first}
    assert by[("parcel-a", "S1")][2] == "done"
    assert by[("parcel-a", "S2")][2] == "cloud_skipped"
    assert float(by[("parcel-a", "S2")][3]) == 42.5
    assert by[("parcel-a", "S2")][4] == date(2026, 11, 2)
    assert by[("parcel-b", "S1")][2] == "done"
    assert by[("parcel-c", "S3")][2] == "cloud_skipped"
    assert by[("parcel-c", "S3")][3] is None and by[("parcel-c", "S3")][4] is None
    assert by[("parcel-d", "S4")][2] == "pending" and by[("parcel-d", "S4")][4] is None
    assert by[("parcel-e", "S5")][3] is None
    assert len(first) == 6  # no row for the download without a parcel


def test_backfill_marks_unfinished_work_as_abandoned(parcel_scenes_db):
    engine, _ = parcel_scenes_db
    with engine.begin() as conn:
        _job(conn, "acme", "parcel-c", "download", "completed", {"scene_id": "S3"})
        conn.exec_driver_sql(MIGRATION.read_text())
        age = conn.exec_driver_sql(
            "SELECT now() - updated_at FROM vegetation_parcel_scenes WHERE scene_id = 'S3'"
        ).scalar()
    assert age.days >= 6


def test_record_upserts_and_respects_rules(parcel_scenes_db, monkeypatch):
    engine, make = parcel_scenes_db
    with engine.begin() as conn:
        conn.exec_driver_sql(MIGRATION.read_text())
    from app import database
    from app.services import parcel_scenes as ps
    monkeypatch.setattr(database, "SessionLocal", make)

    assert ps.record("acme", "parcel-a", "S1", ps.PENDING, new_attempt=True,
                     sensing_date=date(2026, 11, 1)) == "pending"
    assert ps.record("acme", "parcel-a", "S1", ps.DONE) == "done"
    assert ps.record("acme", "parcel-a", "S1", ps.NO_VALID_PIXELS) == "done"
    assert ps.record("acme", "parcel-a", "S1", ps.FAILED) == "done"
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT status, attempts, sensing_date FROM vegetation_parcel_scenes WHERE scene_id = 'S1'"
        ).one()
    assert tuple(row) == ("done", 1, date(2026, 11, 1))
    reader = make()
    assert ps.scenes_seen(reader, "acme", "parcel-a", ["S1", "S9"], NOW) == {"S1"}
    assert ps.scenes_seen(reader, "acme", "parcel-b", ["S1"], NOW) == set()
    assert ps.record("acme", "", "S1", ps.DONE) == ""


def test_record_never_raises_and_leaves_caller_session_usable(parcel_scenes_db, monkeypatch):
    engine, make = parcel_scenes_db
    with engine.begin() as conn:
        conn.exec_driver_sql(MIGRATION.read_text())
    from sqlalchemy import text
    from app import database
    from app.services import parcel_scenes as ps
    monkeypatch.setattr(database, "SessionLocal", make)
    caller = make()
    caller.execute(text("SELECT 1"))
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE vegetation_parcel_scenes")
    assert ps.record("acme", "parcel-a", "S1", ps.DONE) == ""
    caller.execute(text("SELECT 1"))
    caller.commit()
