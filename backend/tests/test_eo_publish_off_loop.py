"""Publishing an EOProduct from async code must not nest asyncio.run().

`upsert_eo_index` bridges to the async SDK with `asyncio.run()`, which is there
for the sync callers (Celery tasks). scenes.py is an async FastAPI handler, so
calling it directly raised

    RuntimeError: asyncio.run() cannot be called from a running event loop

on every index of every Copernicus run — the broker stayed empty and the failure
was only visible in the pod logs, because publishing is best-effort.
"""
import asyncio
from unittest.mock import patch

from app.api.scenes import _publish_eo_index


def test_publishing_works_inside_a_running_loop():
    seen = {}

    def _fake_upsert(**kwargs):
        # Must run where no loop is active, or asyncio.run() would blow up again.
        try:
            asyncio.get_running_loop()
            seen["loop"] = True
        except RuntimeError:
            seen["loop"] = False
        seen["kwargs"] = kwargs
        return "urn:ngsi-ld:EOProduct:x"

    async def _drive():
        with patch("app.api.scenes.upsert_eo_index", _fake_upsert):
            await _publish_eo_index(tenant_id="montiko", parcel_id="p", index_type="NDVI")

    asyncio.run(_drive())
    assert seen["loop"] is False, "the sync bridge must not run on the event loop"
    assert seen["kwargs"]["index_type"] == "NDVI"


def test_a_publish_failure_is_swallowed():
    async def _drive():
        with patch("app.api.scenes.upsert_eo_index", side_effect=RuntimeError("broker down")):
            await _publish_eo_index(tenant_id="montiko", parcel_id="p", index_type="NDVI")

    asyncio.run(_drive())  # must not raise
