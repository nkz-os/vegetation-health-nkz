"""Migration 018 against a real Postgres (skipped without ZONING_PG_URL)."""
import os
from pathlib import Path

import pytest

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "018_monitoring_state.sql"


@pytest.fixture
def monitoring_db():
    """Engine with a minimal vegetation_subscriptions (as of migration 004) and no 018 objects."""
    url = os.getenv("ZONING_PG_URL")
    if not url:
        pytest.skip("ZONING_PG_URL not set")
    from sqlalchemy import create_engine
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_continuous_requests, vegetation_subscriptions")
        conn.exec_driver_sql(
            "CREATE TABLE vegetation_subscriptions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "tenant_id text NOT NULL, entity_id text NOT NULL, is_active boolean DEFAULT true)"
        )
    yield engine
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_continuous_requests, vegetation_subscriptions")
    engine.dispose()


def _reasons(conn):
    return dict(conn.exec_driver_sql(
        "SELECT entity_id, paused_reason FROM vegetation_subscriptions ORDER BY entity_id"
    ).fetchall())


def test_migration_is_idempotent_and_marks_existing_pauses_as_user(monitoring_db):
    engine = monitoring_db
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO vegetation_subscriptions (tenant_id, entity_id, is_active) VALUES "
            "('acme', 'active', true), ('acme', 'paused', false)"
        )
        conn.exec_driver_sql(MIGRATION.read_text())
    with engine.begin() as conn:
        first = _reasons(conn)
        conn.exec_driver_sql(
            "UPDATE vegetation_subscriptions SET paused_reason = 'harvest', is_active = false "
            "WHERE entity_id = 'active'"
        )
        conn.exec_driver_sql(MIGRATION.read_text())
        second = _reasons(conn)
    assert first == {"active": None, "paused": "user"}
    assert second == {"active": "harvest", "paused": "user"}  # a rerun keeps what was set since


def test_check_rejects_another_reason(monitoring_db):
    from sqlalchemy.exc import IntegrityError
    engine = monitoring_db
    with engine.begin() as conn:
        conn.exec_driver_sql(MIGRATION.read_text())
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO vegetation_subscriptions (tenant_id, entity_id, is_active, paused_reason) "
                "VALUES ('acme', 'x', false, 'weather')"
            )


def test_continuous_requests_table_is_unique_and_tenant_isolated(monitoring_db):
    from sqlalchemy.exc import IntegrityError
    engine = monitoring_db
    with engine.begin() as conn:
        conn.exec_driver_sql(MIGRATION.read_text())
        conn.exec_driver_sql(MIGRATION.read_text())
        conn.exec_driver_sql(
            "INSERT INTO vegetation_continuous_requests (tenant_id, entity_id, module_id) "
            "VALUES ('acme', 'p1', 'carbon')"
        )
        assert conn.exec_driver_sql(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'vegetation_continuous_requests'"
        ).scalar() is True
        assert conn.exec_driver_sql(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'vegetation_continuous_requests'"
        ).scalar() == 1
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO vegetation_continuous_requests (tenant_id, entity_id, module_id) "
                "VALUES ('acme', 'p1', 'carbon')"
            )
