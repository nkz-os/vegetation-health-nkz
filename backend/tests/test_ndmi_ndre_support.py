"""NDMI is a first-class index, and every engine's band list matches its formula."""
import numpy as np
import pytest

from app.engines.routing import COPERNICUS_ELIGIBLE, route_index
from app.services.processor import VegetationIndexProcessor
from app.tasks import historical_baseline
from app.tasks.processing_tasks import _LOCAL_BANDS, _LOCAL_CALCULATORS, _local_index_bands, compute_local_index


def _recording_processor():
    """Processor whose load_bands records the bands and serves flat reflectance."""
    p = VegetationIndexProcessor.__new__(VegetationIndexProcessor)
    p.band_paths = {}
    p.band_data = {}
    p.band_meta = None
    p.bbox = None
    p.radiometry = None
    p.loaded = []

    def load(bands):
        for b in bands:
            p.loaded.append(b)
            p.band_data[b] = np.full((4, 4), 0.2, dtype=np.float32)

    p.load_bands = load
    return p


@pytest.mark.parametrize("table,calculators", [
    (_LOCAL_BANDS, _LOCAL_CALCULATORS),
    (historical_baseline.BAND_MAP, historical_baseline._INDEX_CALCULATORS),
])
def test_downloaded_bands_are_the_bands_each_formula_loads(table, calculators):
    for index, method in calculators.items():
        p = _recording_processor()
        getattr(p, method)(apply_cloud_mask=False)
        assert set(p.loaded) == set(table[index]), index


def test_ndre_bands_are_red_edge_and_narrow_nir():
    assert set(_local_index_bands("NDRE", None)) == {"B05", "B8A"}
    assert set(historical_baseline.index_bands("NDRE")) == {"B05", "B8A", "SCL"}


def test_local_and_historical_engines_compute_ndmi():
    assert set(_local_index_bands("NDMI", None)) == {"B8A", "B11"}
    assert set(historical_baseline.index_bands("NDMI")) == {"B8A", "B11", "SCL"}

    class P:
        def calculate_ndmi(self):
            return np.array([0.3])
    assert compute_local_index(P(), "NDMI", None)[0] == 0.3


def test_copernicus_serves_ndmi():
    assert "NDMI" in COPERNICUS_ELIGIBLE
    assert route_index("NDMI", has_custom_formula=False) == "copernicus"


# ---- Copernicus engine ----

from datetime import date  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from app.engines.sentinel_hub import SentinelHubEngine  # noqa: E402
from app.services import evalscripts  # noqa: E402

_GEOM = {"type": "Polygon", "coordinates": [[[-1.6, 42.8], [-1.5, 42.8], [-1.5, 42.9], [-1.6, 42.8]]]}


def _interval(**means):
    stats = lambda m: {"bands": {"B0": {"stats": {  # noqa: E731
        "mean": m, "stDev": 0.05, "min": m - 0.1, "max": m + 0.1,
        "percentiles": {"10": m - 0.05, "90": m + 0.05}, "sampleCount": 40, "noDataCount": 0}}}}
    return {"data": [{"interval": {"from": "2026-07-15T00:00:00Z", "to": "2026-07-20T00:00:00Z"},
                      "outputs": {k: stats(v) for k, v in means.items()}}]}


def _run(index_types, responses):
    engine = SentinelHubEngine(client_id="id", client_secret="secret")
    calls = []

    async def statistical(**kwargs):
        calls.append(kwargs["evalscript"])
        return responses[kwargs["evalscript"]]

    with patch.object(engine, "_client") as client:
        client.statistical = AsyncMock(side_effect=statistical)
        import asyncio
        results = asyncio.run(engine.compute_indices(
            tenant_id="tenant-one", parcel_id="urn:ngsi-ld:AgriParcel:p1", parcel_geometry=_GEOM,
            date_range=(date(2026, 7, 15), date(2026, 7, 20)), index_types=index_types))
    return results, calls


def test_ndmi_alone_requests_only_the_moisture_script():
    results, calls = _run(["NDMI"], {evalscripts.MOISTURE_INDEX: _interval(ndmi=0.21)})
    assert calls == [evalscripts.MOISTURE_INDEX]
    assert [(r.index_type, r.mean) for r in results] == [("NDMI", 0.21)]


def test_ndmi_with_optical_indices_merges_both_scripts():
    results, calls = _run(["NDVI", "NDMI"], {
        evalscripts.MULTI_INDEX: _interval(ndvi=0.8, evi=0.5, savi=0.6, osavi=0.7, gndvi=0.6, ndre=0.4),
        evalscripts.MOISTURE_INDEX: _interval(ndmi=0.25),
    })
    assert sorted(calls, key=len) == sorted([evalscripts.MULTI_INDEX, evalscripts.MOISTURE_INDEX], key=len)
    assert {(r.index_type, r.mean) for r in results} == {("NDVI", 0.8), ("NDMI", 0.25)}


def test_optical_indices_never_pay_for_b11():
    _, calls = _run(["NDVI", "OSAVI"], {evalscripts.MULTI_INDEX: _interval(ndvi=0.8, osavi=0.7)})
    assert calls == [evalscripts.MULTI_INDEX]
    assert "B11" not in evalscripts.MULTI_INDEX


def test_moisture_script_formula_and_bands():
    js = evalscripts.MOISTURE_INDEX
    assert js.startswith("//VERSION=3")
    assert '"B8A", "B11", "SCL", "dataMask"' in js
    assert 'id: "ndmi"' in js and "(b8a - b11) / (b8a + b11)" in js
    assert '"DN"' in js and "SIMPLE" in js


def _input_bands(js):
    import json
    import re
    setup = js[js.index("function setup()"):js.index("function isClear")]
    bands = json.loads(re.search(r"bands:\s*(\[[^\]]*\])", setup).group(1))
    units = json.loads(re.search(r"units:\s*(\[[^\]]*\])", setup).group(1))
    assert len(bands) == len(units)
    return bands, units


def test_float_raster_requests_only_the_index_bands():
    ndmi = evalscripts.build_index_float("NDMI")
    assert 'case "NDMI"' in ndmi
    assert _input_bands(ndmi) == (["B8A", "B11", "SCL", "dataMask"],
                                  ["reflectance", "reflectance", "DN", "DN"])
    assert _input_bands(evalscripts.build_index_float("NDVI"))[0] == ["B04", "B08", "SCL", "dataMask"]
    assert _input_bands(evalscripts.build_index_float("NDRE"))[0] == ["B05", "B8A", "SCL", "dataMask"]


def test_skipped_scene_releases_every_local_index(monkeypatch):
    from app.tasks import processing_tasks as pt
    released = []
    monkeypatch.setattr(pt, "_release_idempotency", lambda t, p, i, d: released.append(i))
    pt.release_scene_idempotency("tenant-one", "urn:ngsi-ld:AgriParcel:p1", "2026-06-01")
    assert set(released) == set(pt._LOCAL_CALCULATORS)
    assert {"OSAVI", "NDMI"} <= set(released)


def test_migration_allows_ndmi_in_the_index_cache():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "015_add_ndmi_index.sql").read_text()
    assert "vegetation_indices_cache_index_type_check" in sql and "'NDMI'" in sql and "'OSAVI'" in sql


def test_history_and_datahub_accept_ndmi():
    from app.api.history import BuildHistoryRequest
    from app.api.internal import ATTRIBUTE_TO_INDEX
    assert BuildHistoryRequest(index="NDMI").index == "NDMI"
    assert ATTRIBUTE_TO_INDEX["ndmiMean"] == "NDMI"


def test_overview_offers_the_crop_defaults(monkeypatch):
    import asyncio
    from app.api import parcels, scenes

    async def species(tenant_id, entity_id):
        return {"p-olive": "Olea europaea", "p-wheat": "Triticum aestivum"}.get(entity_id)

    monkeypatch.setattr(scenes, "_get_crop_species_from_orion", species)
    olive = asyncio.run(parcels.default_indices_for_parcel("tenant-one", "p-olive"))
    wheat = asyncio.run(parcels.default_indices_for_parcel("tenant-one", "p-wheat"))
    none = asyncio.run(parcels.default_indices_for_parcel("tenant-one", "p-none"))
    assert "OSAVI" in olive and "NDMI" in olive
    assert "OSAVI" not in wheat
    assert none == scenes.default_indices_for_species(None)


def test_local_engine_windows_the_cloud_mask():
    from app.tasks.processing_tasks import _window_band_set
    scene = {"B04": "b4", "B08": "b8", "SCL": "scl"}
    assert "SCL" in _window_band_set(["B04", "B08"], scene, sen2res_enabled=False)
    assert "SCL" in _window_band_set(["B05", "B8A"], {**scene, "B05": "b5", "B8A": "b8a"}, sen2res_enabled=True)


def test_scene_index_is_brought_to_the_reference_grid():
    from app.tasks.processing_tasks import _to_reference_grid
    coarse = np.arange(4, dtype=np.float32).reshape(2, 2)
    out = _to_reference_grid(coarse, (4, 4))
    assert out.shape == (4, 4) and out[0, 0] == 0 and out[3, 3] == 3
    same = np.ones((4, 4), dtype=np.float32)
    assert _to_reference_grid(same, (4, 4)) is same


def test_ndmi_loads_only_its_bands():
    p = _recording_processor()
    p.band_paths = {"B04": "x", "B08": "x", "B8A": "x", "B11": "x"}
    p.calculate_ndmi(apply_cloud_mask=False)
    assert set(p.loaded) == {"B8A", "B11"}
