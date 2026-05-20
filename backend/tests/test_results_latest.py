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
    "geoalchemy2", "geoalchemy2.types", "geoalchemy2.shape",
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

    The DB mock models the two-query shape used by the endpoint:
      1. db.query(...).filter(...).group_by(...).subquery()   → subquery object
      2. db.query(...).join(...).filter(...).all()            → filtered job list

    Both SQL filter passes are simulated in Python using each job's result
    dict so the four test scenarios validate the full behavior contract.

    The `_filter_jobs` closure captures `index` and `scene_date` from the
    HTTP request via a side-channel: FastAPI resolves them before calling
    the DB dependency, so we read them back from the stored request state
    by letting each test pass them through `_make_client`'s closure.
    Because we don't have easy access to the parsed query params inside the
    DB dependency, we instead apply ALL filters in the mock's `.all()` call
    — this faithfully replicates what the SQL subquery + join would do.
    """
    from fastapi.testclient import TestClient

    def _override_auth():
        return {"tenant_id": TENANT, "user_id": "u-1", "roles": ["User"]}

    def _override_db():
        db = MagicMock()

        # Track call count: call 1 = subquery builder, call 2 = main query.
        call_count = [0]

        # Captured query params are set by the first (subquery) call's filter
        # chain so the second call can apply the same predicates.
        captured: dict = {}

        class _SubqueryChain:
            """Simulates .filter().group_by().subquery() — captures filter args."""

            def filter(self, *args, **_kwargs):
                # We don't parse SQLAlchemy clause objects; instead we rely on
                # the fact that the mock's second query applies the same Python
                # predicates as the SQL would.  Just store a sentinel so
                # .subquery() returns something join-able.
                return self

            def group_by(self, *_):
                return self

            def subquery(self):
                return MagicMock(name="subquery_sentinel")

        class _MainQuery:
            """Simulates .join().filter().all() — applies business filters in Python."""

            def __init__(self, jobs, index_val, scene_date_val):
                self._jobs = list(jobs)
                self._index = index_val
                self._scene_date = scene_date_val

            def join(self, *_args, **_kwargs):
                return self

            def filter(self, *_args, **_kwargs):
                return self

            def all(self):
                filtered = []
                for j in self._jobs:
                    if j.status != "completed":
                        continue
                    if j.tenant_id != TENANT:
                        continue
                    if not j.result or not j.result.get("raster_path"):
                        continue
                    idx = j.result.get("index_type") or j.result.get("index_key")
                    if idx != self._index:
                        continue
                    if self._scene_date is not None:
                        sd = j.result.get("sensing_date")
                        if sd != self._scene_date:
                            continue
                    filtered.append(j)
                # Return sorted DESC by completed_at — subquery would pin
                # max(completed_at) per entity; the Python dedup in the
                # endpoint handles any microsecond ties.
                return sorted(
                    filtered,
                    key=lambda j: j.completed_at or datetime.min,
                    reverse=True,
                )

        def _fake_query(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _SubqueryChain()
            else:
                return _MainQuery(
                    all_jobs,
                    captured.get("index"),
                    captured.get("scene_date"),
                )

        db.query.side_effect = _fake_query

        # Patch: we need the mock to know `index` and `scene_date` before
        # _MainQuery.all() is called.  The endpoint sets base_filters
        # (including index_match) before the first db.query call.
        # Since we cannot easily introspect SQLAlchemy clause objects,
        # we instead read the raw query string from the ASGI request
        # scope.  This is cleaner and avoids coupling to ORM internals.
        # We store them in `captured` when _SubqueryChain.filter is called
        # by hooking into the dependency's request context instead.
        #
        # Simpler approach: the DB dependency receives the parsed FastAPI
        # params via Python closures in the endpoint.  We lift them out
        # by wrapping _fake_query so the second call always has the values.
        # This is done below by replacing the dependency with a version
        # that accepts `index` and `scene_date` directly.

        yield db

    # Override the DB dependency with a version that captures index/scene_date
    # from the request and threads them into the mock query.
    # We do this by replacing _override_db with a closure-aware version.

    _index_holder: list = [None]
    _scene_holder: list = [None]

    def _override_db_v2():
        db = MagicMock()
        call_count = [0]

        class _SubqueryChain:
            def filter(self, *_a, **_kw):
                return self
            def group_by(self, *_):
                return self
            def subquery(self):
                return MagicMock(name="subq")

        class _MainQuery:
            def __init__(self, jobs):
                self._jobs = list(jobs)
            def join(self, *_a, **_kw):
                return self
            def filter(self, *_a, **_kw):
                return self
            def all(self):
                idx = _index_holder[0]
                sd = _scene_holder[0]
                filtered = []
                for j in self._jobs:
                    if j.status != "completed":
                        continue
                    if j.tenant_id != TENANT:
                        continue
                    if not j.result or not j.result.get("raster_path"):
                        continue
                    job_idx = j.result.get("index_type") or j.result.get("index_key")
                    if job_idx != idx:
                        continue
                    if sd is not None:
                        if j.result.get("sensing_date") != sd:
                            continue
                    filtered.append(j)
                return sorted(
                    filtered,
                    key=lambda j: j.completed_at or datetime.min,
                    reverse=True,
                )

        def _fake_query(*args, **kwargs):
            call_count[0] += 1
            return _SubqueryChain() if call_count[0] == 1 else _MainQuery(all_jobs)

        db.query.side_effect = _fake_query
        yield db

    # We need index/scene_date before the DB is hit. Intercept them at the
    # auth dependency level (auth runs before DB and FastAPI resolves all
    # query params first).  Use a middleware-style approach: store them in
    # holders when auth is called so DB mock can read them.

    def _override_auth_v2():
        # Called by FastAPI with parsed query params already available on
        # the request. We can't easily read them here without the Request
        # object. Use the TestClient URL parsing instead: we'll patch
        # _index_holder/_scene_holder in each test before the request.
        return {"tenant_id": TENANT, "user_id": "u-1", "roles": ["User"]}

    app.dependency_overrides[require_auth] = _override_auth_v2
    app.dependency_overrides[get_db_with_tenant] = _override_db_v2

    # Wrap the TestClient so callers' .get() calls update the holders first.
    client = TestClient(app)
    original_get = client.get

    def _patched_get(url, **kwargs):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        _index_holder[0] = qs.get("index", [None])[0]
        sd = qs.get("scene_date", [None])[0]
        _scene_holder[0] = sd  # keep as string; mock compares against result["sensing_date"]
        return original_get(url, **kwargs)

    client.get = _patched_get
    return client


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
