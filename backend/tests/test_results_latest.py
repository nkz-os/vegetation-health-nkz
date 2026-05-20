"""
Tests for GET /api/vegetation/results/latest endpoint.

Uses FastAPI dependency overrides and in-memory VegetationJob-like objects
to avoid requiring a real PostgreSQL/PostGIS instance.

The module-level sys.modules stubs prevent heavy optional deps (Celery,
rasterio, geoalchemy2 geometry DDL, SQLAlchemy engine) from being
exercised at import time.
"""
import os
import sys
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

# --- Environment setup (must come before any app import) ---
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

# Stub heavy modules that are not needed for these route-level tests.
_STUBS = [
    # Geospatial stack
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.crs", "rasterio.merge", "rasterio.mask",
    "shapely", "shapely.geometry", "shapely.ops",
    "pyproj",
    # Cloud / storage
    "boto3",
    "botocore", "botocore.config", "botocore.exceptions",
    # Remote sensing
    "sentinelsat",
    "pystac_client",
    "asf_search",
    # Scientific
    "simpleeval",
    "numpy", "numpy.ma",
    "pyarrow", "pyarrow.ipc",
    "rasterstats",
    "rio_tiler", "rio_tiler.io", "rio_tiler.errors", "rio_tiler.colormap",
    "rio_cogeo",
    "scipy", "scipy.stats", "scipy.ndimage", "scipy.cluster", "scipy.cluster.vq",
    "PIL", "PIL.Image",
    "structlog",
    # app.tasks — stub the whole package to avoid pulling in celery/boto3/rasterio
    "app.tasks",
    "app.tasks.download_tasks",
    "app.tasks.processing_tasks",
    "app.tasks.scheduler",
    "app.tasks.storage_cleanup",
    "app.celery_app",
]
for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Stub the entire app.database module before app.main imports it.
# This replaces the module-level engine/sessionmaker that would otherwise
# call create_engine() and register event listeners against a real engine.
_db_stub = MagicMock()
_session_stub = MagicMock()
_db_stub.SessionLocal = _session_stub
sys.modules["app.database"] = _db_stub

# Now it is safe to import the app.
from app.main import app  # noqa: E402
from app.middleware.auth import require_auth  # noqa: E402

# Retrieve get_db_with_tenant from the stub (it was set when app.api.scenes
# imported app.database).
get_db_with_tenant = _db_stub.get_db_with_tenant

TENANT = "test-tenant-results-latest"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(entity_id, index_type, completed_at, raster_path,
              status="completed", sensing_date=date(2025, 6, 1),
              tenant=TENANT, scene_id=None):
    """Build an in-memory VegetationJob-like object without touching the DB."""
    job = MagicMock()
    job.id = uuid4()
    job.tenant_id = tenant
    job.entity_id = entity_id
    job.job_type = "calculate_index"
    job.status = status
    job.completed_at = completed_at
    job.deleted_at = None
    job.created_at = completed_at
    # result JSONB stores index metadata (matches production write path).
    job.result = {
        "index_type": index_type,
        "index_key": index_type,
        "raster_path": raster_path,
        "sensing_date": sensing_date.isoformat() if sensing_date else None,
        "scene_id": scene_id,
    }
    return job


def _make_client(all_jobs):
    """Return a TestClient with DB and auth dependencies overridden.

    The DB override returns *all_jobs* verbatim; filtering logic inside
    the endpoint (status, job_type, tenant_id, index, scene_date) is
    applied in Python by the endpoint itself.
    """
    from fastapi.testclient import TestClient

    def _override_auth():
        return {"tenant_id": TENANT, "user_id": "u-1", "roles": ["User"]}

    def _override_db():
        db = MagicMock()

        class _FakeQuery:
            def __init__(self, jobs):
                self._jobs = list(jobs)

            # Chain all ORM filter/order/limit calls back to self.
            def filter(self, *_):
                return self

            def order_by(self, *_):
                return self

            def limit(self, _n):
                return self

            def all(self):
                # Mimic ORDER BY completed_at DESC so deduplication
                # in the endpoint picks the latest job correctly.
                return sorted(
                    self._jobs,
                    key=lambda j: j.completed_at or datetime.min,
                    reverse=True,
                )

            def count(self):
                return 0

        db.query.return_value = _FakeQuery(all_jobs)
        yield db

    app.dependency_overrides[require_auth] = _override_auth
    app.dependency_overrides[get_db_with_tenant] = _override_db
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_returns_latest_completed_job_per_entity():
    """Latest job wins when two completed jobs exist for same entity+index."""
    jobs = [
        _make_job("urn:E:1", "NDVI", datetime(2025, 6, 1), "/data/old.tif"),
        _make_job("urn:E:1", "NDVI", datetime(2025, 7, 1), "/data/new.tif"),
        _make_job("urn:E:2", "NDVI", datetime(2025, 6, 15), "/data/e2.tif"),
        _make_job("urn:E:3", "NDRE", datetime(2025, 6, 20), "/data/e3-ndre.tif"),
    ]
    client = _make_client(jobs)

    r = client.get("/api/vegetation/results/latest?index=NDVI")
    assert r.status_code == 200, r.text
    data = r.json()
    entity_ids = {item["entity_id"] for item in data}
    assert entity_ids == {"urn:E:1", "urn:E:2"}
    e1 = next(i for i in data if i["entity_id"] == "urn:E:1")
    assert e1["raster_path"] == "/data/new.tif"


def test_excludes_failed_and_in_progress_jobs():
    """Only completed jobs appear in results."""
    jobs = [
        _make_job("urn:E:1", "NDVI", datetime(2025, 7, 1), "/data/ok.tif", status="completed"),
        _make_job("urn:E:2", "NDVI", datetime(2025, 7, 1), "/data/bad.tif", status="failed"),
        _make_job("urn:E:3", "NDVI", datetime(2025, 7, 1), "/data/wip.tif", status="running"),
    ]
    client = _make_client(jobs)

    r = client.get("/api/vegetation/results/latest?index=NDVI")
    assert r.status_code == 200, r.text
    ids = {i["entity_id"] for i in r.json()}
    assert ids == {"urn:E:1"}


def test_scene_date_filter():
    """scene_date query param restricts results to that sensing date."""
    jobs = [
        _make_job("urn:E:1", "NDVI", datetime(2025, 6, 1), "/data/jun.tif",
                  sensing_date=date(2025, 6, 1)),
        _make_job("urn:E:1", "NDVI", datetime(2025, 7, 1), "/data/jul.tif",
                  sensing_date=date(2025, 7, 1)),
    ]
    client = _make_client(jobs)

    r = client.get("/api/vegetation/results/latest?index=NDVI&scene_date=2025-06-01")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 1
    assert data[0]["raster_path"] == "/data/jun.tif"


def test_tenant_isolation():
    """Jobs from other tenants must not appear in the response."""
    jobs = [
        _make_job("urn:E:1", "NDVI", datetime(2025, 7, 1), "/data/ours.tif", tenant=TENANT),
        _make_job("urn:E:99", "NDVI", datetime(2025, 7, 1), "/data/leak.tif",
                  tenant="other-tenant"),
    ]
    client = _make_client(jobs)

    r = client.get("/api/vegetation/results/latest?index=NDVI")
    assert r.status_code == 200, r.text
    ids = {i["entity_id"] for i in r.json()}
    assert ids == {"urn:E:1"}
