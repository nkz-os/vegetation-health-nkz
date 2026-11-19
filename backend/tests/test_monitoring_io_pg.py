"""Continuity requests against a real Postgres (skipped without ZONING_PG_URL)."""
import os

import pytest

from app.models import VegetationContinuousRequest
from app.services.monitoring_io import add_continuous, continuous_modules, remove_continuous

PARCEL = "urn:ngsi-ld:AgriParcel:p1"


@pytest.fixture
def session():
    url = os.getenv("ZONING_PG_URL")
    if not url:
        pytest.skip("ZONING_PG_URL not set")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(url)
    table = VegetationContinuousRequest.__table__
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_continuous_requests")
    table.create(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS vegetation_continuous_requests")
    engine.dispose()


def test_add_is_idempotent_and_scoped_by_tenant_and_parcel(session):
    add_continuous(session, "tenant-a", PARCEL, "carbon")
    add_continuous(session, "tenant-a", PARCEL, "carbon")
    add_continuous(session, "tenant-a", PARCEL, "alpha")
    add_continuous(session, "tenant-b", PARCEL, "other")
    add_continuous(session, "tenant-a", "urn:ngsi-ld:AgriParcel:p2", "other")
    assert continuous_modules(session, "tenant-a", PARCEL) == ["alpha", "carbon"]
    assert continuous_modules(session, "tenant-b", PARCEL) == ["other"]


def test_remove_is_idempotent_and_leaves_the_rest(session):
    add_continuous(session, "tenant-a", PARCEL, "carbon")
    add_continuous(session, "tenant-a", PARCEL, "alpha")
    remove_continuous(session, "tenant-a", PARCEL, "carbon")
    remove_continuous(session, "tenant-a", PARCEL, "carbon")
    assert continuous_modules(session, "tenant-a", PARCEL) == ["alpha"]
