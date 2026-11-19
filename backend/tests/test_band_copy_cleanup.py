"""One-off band copy cleanup: listing, eligibility, and deletion of eligible copies only."""
import io
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.jobs import band_copy_cleanup as bcc
from app.services.storage import generate_tenant_bucket_name

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


class FakeS3:
    """Objects per bucket; records deletions."""

    def __init__(self, objects):
        self.objects = objects  # {bucket: {key: size}}
        self.deleted = []

    def list_objects(self, prefix, bucket=None):
        return [{"Key": k, "Size": s} for k, s in sorted(self.objects.get(bucket, {}).items()) if k.startswith(prefix)]

    def delete_prefix(self, prefix, bucket=None):
        self.deleted.append((bucket, prefix))
        return 1


@pytest.fixture
def world(monkeypatch):
    """Two tenants with copies; helpers return configurable busy/pending sets."""
    state = {"busy": {}, "pending": {}, "composite": {}}
    monkeypatch.setattr(bcc, "_tenant_paths", lambda db, tenants: {
        t: p for t, p in {"acme": "vegetation-prime/", "tenant-a": "vegetation-prime"}.items()
        if not tenants or t in tenants
    })
    monkeypatch.setattr(bcc, "_tenant_context", lambda db, t: None)
    monkeypatch.setattr(bcc, "_in_progress_scenes", lambda db, t: state["busy"].get(t, set()))
    monkeypatch.setattr(bcc, "_composite_scenes", lambda db, t: state["composite"].get(t, set()))
    monkeypatch.setattr(bcc, "_fresh_pending_scenes", lambda db, t, now: state["pending"].get(t, set()))
    b_acme = generate_tenant_bucket_name("acme")
    b_a = generate_tenant_bucket_name("tenant-a")
    s3 = FakeS3({
        b_acme: {
            "vegetation-prime/scenes/S1/B04.tif": 100,
            "vegetation-prime/scenes/S1/B08.tif": 200,
            "vegetation-prime/scenes/S2/B04.tif": 50,
            "vegetation-prime/other/keep.tif": 7,
        },
        b_a: {"vegetation-prime/scenes/S1/B04.tif": 10},
    })
    return state, s3, b_acme, b_a


def test_groups_by_scene_and_sums(world):
    _, s3, b_acme, b_a = world
    entries = bcc.list_copies(None, s3, now=NOW)
    got = {(e.tenant_id, e.scene_id): (e.size_bytes, e.eligible, e.reason, e.bucket, e.prefix) for e in entries}
    assert got[("acme", "S1")] == (300, True, "calculated", b_acme, "vegetation-prime/scenes/S1/")
    assert got[("acme", "S2")][0] == 50
    assert got[("tenant-a", "S1")] == (10, True, "calculated", b_a, "vegetation-prime/scenes/S1/")
    assert len(entries) == 3  # the non-scenes object is not a copy


def test_tenant_filter(world):
    _, s3, *_ = world
    assert {e.tenant_id for e in bcc.list_copies(None, s3, ["tenant-a"], now=NOW)} == {"tenant-a"}


def test_in_progress_job_not_eligible(world):
    state, s3, *_ = world
    # _in_progress_scenes already resolves UUID -> product id in SQL (covered by the PG test); here the product id.
    state["busy"]["acme"] = {"S1"}
    got = {(e.tenant_id, e.scene_id): (e.eligible, e.reason) for e in bcc.list_copies(None, s3, now=NOW)}
    assert got[("acme", "S1")] == (False, "in_progress")
    assert got[("acme", "S2")] == (True, "calculated")
    assert got[("tenant-a", "S1")] == (True, "calculated")  # another tenant's job does not protect or block


def test_fresh_pending_parcel_not_eligible(world):
    state, s3, *_ = world
    state["pending"]["tenant-a"] = {"S1"}
    got = {(e.tenant_id, e.scene_id): (e.eligible, e.reason) for e in bcc.list_copies(None, s3, now=NOW)}
    assert got[("tenant-a", "S1")] == (False, "pending_parcel")
    assert got[("acme", "S1")] == (True, "calculated")


def test_list_never_deletes(world):
    state, s3, *_ = world
    out = io.StringIO()
    rc = bcc.main(["--tenant", "acme"], db=MagicMock(), s3=s3, out=out)
    assert rc == 0
    assert s3.deleted == []
    text = out.getvalue()
    assert "acme\tS1\t" in text and "TOTAL\tacme" in text and "TOTAL\tALL" in text
    assert "DELETED" not in text


def test_delete_only_eligible(world):
    state, s3, b_acme, b_a = world
    state["busy"]["acme"] = {"S2"}
    out = io.StringIO()
    rc = bcc.main(["--delete"], db=MagicMock(), s3=s3, out=out)
    assert rc == 0
    assert sorted(s3.deleted) == sorted([
        (b_acme, "vegetation-prime/scenes/S1/"),
        (b_a, "vegetation-prime/scenes/S1/"),
    ])
    assert "DELETED\t2 scenes" in out.getvalue()


def test_composite_job_scenes_not_eligible(world):
    state, s3, *_ = world
    state["composite"]["acme"] = {"S2"}
    got = {(e.tenant_id, e.scene_id): (e.eligible, e.reason) for e in bcc.list_copies(None, s3, now=NOW)}
    assert got[("acme", "S2")] == (False, "in_progress_composite")
    assert got[("acme", "S1")] == (True, "calculated")


def test_scene_flipped_busy_between_list_and_delete_is_not_deleted(world):
    state, s3, b_acme, b_a = world
    entries = bcc.list_copies(None, s3, now=NOW)
    assert all(e.eligible for e in entries)
    state["busy"]["acme"] = {"S1"}  # a job starts AFTER listing
    skipped = []
    scenes, _ = bcc.delete_copies(
        s3, entries,
        recheck=lambda e: bcc.busy_scenes(None, e.tenant_id, NOW).get(e.scene_id),
        skipped=skipped,
    )
    assert (b_acme, "vegetation-prime/scenes/S1/") not in s3.deleted
    assert (b_acme, "vegetation-prime/scenes/S2/") in s3.deleted
    assert scenes == 2
    assert [(e.scene_id, r) for e, r in skipped] == [("S1", "in_progress")]


def test_main_delete_rechecks_each_scene_and_reports_skip(world, monkeypatch):
    state, s3, b_acme, _ = world
    calls = {"n": 0}

    def flipping(db, t):
        calls["n"] += 1
        # the first call belongs to the listing; afterwards acme/S1 becomes busy
        return {"S1"} if t == "acme" and calls["n"] > 1 else set()

    monkeypatch.setattr(bcc, "_in_progress_scenes", flipping)
    out = io.StringIO()
    bcc.main(["--delete", "--tenant", "acme"], db=MagicMock(), s3=s3, out=out)
    assert (b_acme, "vegetation-prime/scenes/S1/") not in s3.deleted
    assert (b_acme, "vegetation-prime/scenes/S2/") in s3.deleted
    assert "SKIPPED\tacme\tS1\tin_progress" in out.getvalue()


def test_delete_copies_skips_ineligible_entries():
    s3 = FakeS3({})
    entries = [
        bcc.CopyEntry("acme", "b", "p/scenes/S1/", "S1", 5, False, "in_progress"),
        bcc.CopyEntry("acme", "b", "p/scenes/S2/", "S2", 7, True, "calculated"),
    ]
    assert bcc.delete_copies(s3, entries) == (1, 7)
    assert s3.deleted == [("b", "p/scenes/S2/")]


def test_sql_helpers_against_postgres():
    """The two eligibility queries on real Postgres (skipped without ZONING_PG_URL)."""
    import json
    import os
    import uuid
    from datetime import timedelta

    url = os.getenv("ZONING_PG_URL")
    if not url:
        pytest.skip("ZONING_PG_URL not set")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(url)
    scene_uuid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_parcel_scenes, vegetation_jobs, vegetation_scenes")
        conn.exec_driver_sql(
            "CREATE TABLE vegetation_jobs (id uuid PRIMARY KEY, tenant_id text, entity_id text, "
            "job_type text, status text, parameters jsonb, start_date date, end_date date)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE vegetation_scenes (id uuid PRIMARY KEY, tenant_id text, scene_id text, sensing_date date)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE vegetation_parcel_scenes (tenant_id text, entity_id text, scene_id text, "
            "status text, updated_at timestamptz)"
        )
        conn.exec_driver_sql("INSERT INTO vegetation_scenes VALUES (%s, 'acme', 'S2', '2026-01-01')", (scene_uuid,))
        for sid, tenant, day in [("C1", "acme", "2026-03-05"), ("C2", "acme", "2026-03-20"),
                                 ("C3", "acme", "2026-05-01"), ("C4", "tenant-a", "2026-03-10"),
                                 ("C5", "acme", "2026-06-10")]:
            conn.exec_driver_sql(
                "INSERT INTO vegetation_scenes VALUES (%s, %s, %s, %s)", (str(uuid.uuid4()), tenant, sid, day),
            )
        # composite by job columns (C1, C2) and by parameters (C5); done composite blocks nothing
        conn.exec_driver_sql(
            "INSERT INTO vegetation_jobs VALUES (%s, 'acme', 'p', 'calculate_index', 'running', '{}'::jsonb, "
            "'2026-03-01', '2026-03-31')", (str(uuid.uuid4()),))
        conn.exec_driver_sql(
            "INSERT INTO vegetation_jobs VALUES (%s, 'acme', 'p', 'calculate_index', 'pending', %s::jsonb, NULL, NULL)",
            (str(uuid.uuid4()), json.dumps({"start_date": "2026-06-01", "end_date": "2026-06-30"})))
        conn.exec_driver_sql(
            "INSERT INTO vegetation_jobs VALUES (%s, 'acme', 'p', 'calculate_index', 'completed', '{}'::jsonb, "
            "'2026-05-01', '2026-05-31')", (str(uuid.uuid4()),))
        for tenant, jtype, status, sid in [
            ("acme", "download", "running", "S1"),
            ("acme", "calculate_index", "pending", scene_uuid),
            ("acme", "download", "completed", "S3"),
            ("acme", "process", "running", "S4"),
            ("tenant-a", "download", "running", "S5"),
        ]:
            conn.exec_driver_sql(
                "INSERT INTO vegetation_jobs VALUES (%s, %s, 'p', %s, %s, %s::jsonb, NULL, NULL)",
                (str(uuid.uuid4()), tenant, jtype, status, json.dumps({"scene_id": sid})),
            )
        for tenant, sid, status, age in [
            ("acme", "P1", "pending", timedelta(hours=1)),
            ("acme", "P2", "pending", timedelta(hours=60)),
            ("acme", "P3", "done", timedelta(hours=1)),
            ("tenant-a", "P4", "pending", timedelta(hours=1)),
        ]:
            conn.exec_driver_sql(
                "INSERT INTO vegetation_parcel_scenes VALUES (%s, 'p', %s, %s, %s)",
                (tenant, sid, status, NOW - age),
            )
    session = sessionmaker(bind=engine)()
    try:
        assert bcc._in_progress_scenes(session, "acme") == {"S1", "S2"}
        assert bcc._composite_scenes(session, "acme") == {"C1", "C2", "C5"}
        assert bcc._composite_scenes(session, "tenant-a") == set()
        assert bcc.busy_scenes(session, "acme", NOW)["C1"] == "in_progress_composite"
        assert bcc._fresh_pending_scenes(session, "acme", NOW) == {"P1"}
    finally:
        session.close()
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_parcel_scenes, vegetation_jobs, vegetation_scenes")
        engine.dispose()


def test_all_tenants_include_those_without_config_row():
    """Without --tenant, every tenant with scenes or jobs is listed, not only vegetation_config rows."""
    import os

    import pytest

    url = os.getenv("ZONING_PG_URL")
    if not url:
        pytest.skip("ZONING_PG_URL not set")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_config, vegetation_jobs, vegetation_scenes")
        conn.exec_driver_sql("CREATE TABLE vegetation_config (tenant_id text, storage_path text)")
        conn.exec_driver_sql("CREATE TABLE vegetation_jobs (id uuid PRIMARY KEY, tenant_id text)")
        conn.exec_driver_sql("CREATE TABLE vegetation_scenes (id uuid PRIMARY KEY, tenant_id text)")
        conn.exec_driver_sql("INSERT INTO vegetation_config VALUES ('tenant-a', 'custom/')")
        conn.exec_driver_sql("INSERT INTO vegetation_scenes VALUES (gen_random_uuid(), 'acme')")
        conn.exec_driver_sql("INSERT INTO vegetation_jobs VALUES (gen_random_uuid(), 'demo-coop')")
    db = sessionmaker(bind=engine)()
    try:
        paths = bcc._tenant_paths(db, None)
    finally:
        db.close()
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_config, vegetation_jobs, vegetation_scenes")
        engine.dispose()
    assert paths == {"tenant-a": "custom/", "acme": bcc.DEFAULT_STORAGE_PATH, "demo-coop": bcc.DEFAULT_STORAGE_PATH}
