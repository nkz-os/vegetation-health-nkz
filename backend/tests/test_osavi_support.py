"""OSAVI is a first-class index: every engine and registry knows it."""
import numpy as np

from app.engines.sentinel_hub import _INDEX_OUTPUT_MAP
from app.tasks.processing_tasks import _local_index_bands, compute_local_index
from app.api.scenes import default_indices_for_species


def test_sentinel_hub_maps_osavi_output():
    assert _INDEX_OUTPUT_MAP["OSAVI"] == "osavi"


def test_local_engine_bands_and_formula():
    assert _local_index_bands("OSAVI", None) == ["B04", "B08"]

    class P:
        def calculate_osavi(self):
            return np.array([1.0])
    assert compute_local_index(P(), "OSAVI", None)[0] == 1.0


def test_tree_crops_default_to_osavi():
    assert "OSAVI" in default_indices_for_species("Olea europaea")
    assert "OSAVI" not in default_indices_for_species("Triticum aestivum")
    assert default_indices_for_species(None) == ["NDVI", "EVI", "SAVI", "GNDVI", "NDRE"]


def test_migration_allows_osavi_in_the_index_cache():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "014_add_osavi_index.sql").read_text()
    assert "vegetation_indices_cache_index_type_check" in sql and "'OSAVI'" in sql
