"""Shared test configuration.

App modules fail fast at import when required env vars are missing
(production mandate). Provide test values BEFORE any app import so the
suite can collect without a live environment.
"""

import os
import sys
from pathlib import Path

# Ensure this project's backend is FIRST on sys.path, overriding
# any editable installs (e.g. DaTaK/backend via _datak_gateway.pth)
# that would shadow our app package on import.
_backend_root = str(Path(__file__).resolve().parent.parent)
if _backend_root in sys.path:
    sys.path.remove(_backend_root)
sys.path.insert(0, _backend_root)

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("CONTEXT_URL", "http://context/ngsi-ld-context.json")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")


import pytest  # noqa: E402


@pytest.fixture
def zoning_db():
    """Session factory on a real Postgres with the zoning tables (migration 016) freshly created.

    Needs ZONING_PG_URL (CI sets it); tests using it are skipped without it.
    """
    url = os.getenv("ZONING_PG_URL")
    if not url:
        pytest.skip("ZONING_PG_URL not set")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    migration = Path(__file__).resolve().parents[1] / "migrations" / "016_zoning_versions.sql"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS zoning_versions, zoning_notes")
        conn.exec_driver_sql(migration.read_text())
    factory = sessionmaker(bind=engine)
    opened = []

    def make():
        s = factory()
        opened.append(s)
        return s

    yield make
    for s in opened:  # an open read transaction would block the next test's DROP
        s.close()
    engine.dispose()
