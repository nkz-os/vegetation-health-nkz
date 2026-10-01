"""The job tile endpoint must render with the job's own index type.

A default of ``index=NDVI`` used to override the type stored on the job, so a
SAR tile requested without ``?index=`` was rendered with the NDVI ramp
(-0.2..0.9) and its backscatter DN values saturated into one flat colour.
"""
import uuid
from unittest.mock import MagicMock, patch

from fastapi import Response

with patch("app.database.init_db"):
    from app.api import tiles


def _render_index(index_param, stored="SAR-VV"):
    """Request the tile through FastAPI so the query-parameter default applies."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    job = MagicMock()
    job.tenant_id = "t1"
    job.result = {"index_type": stored, "raster_path": "t1/entities/p/sar/x-VV.tif"}
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = job

    app = FastAPI()
    app.include_router(tiles.router)
    app.dependency_overrides[tiles.get_db_session] = lambda: db
    query = "token=tok" + (f"&index={index_param}" if index_param else "")
    with patch.object(tiles, "validate_tile_token", return_value=True), \
         patch.object(tiles, "_render_tile", return_value=Response(content=b"png")) as render:
        resp = TestClient(app).get(f"/api/vegetation/tiles/{uuid.uuid4()}/15/1/1.png?{query}")
    assert resp.status_code == 200
    return render.call_args.args[4]


def test_without_index_uses_the_job_index_type():
    assert _render_index(None) == "SAR-VV"


def test_explicit_index_still_wins():
    assert _render_index("SAR-VH") == "SAR-VH"


def test_job_without_index_type_falls_back_to_ndvi():
    assert _render_index(None, stored=None) == "NDVI"
