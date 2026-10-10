"""Historical baseline: per-window zonal stats over real UTM rasters.

Sentinel-2 bands come in UTM metres while the parcel geometry from Orion is in
EPSG:4326 degrees. These tests build small UTM GeoTIFFs around a 4326 parcel so
a missing reprojection (no overlap) or a wrong formula shows up as a failure.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

UTM_CRS = "EPSG:32630"
CENTER_LON, CENTER_LAT = -1.6, 42.8
HALF_DEG = 0.0005  # ~40 m half-side parcel


def _parcel_4326() -> dict:
    lo, la, d = CENTER_LON, CENTER_LAT, HALF_DEG
    return {
        "type": "Polygon",
        "coordinates": [[
            [lo - d, la - d], [lo + d, la - d], [lo + d, la + d],
            [lo - d, la + d], [lo - d, la - d],
        ]],
    }


def _write_band(path, value: float, pixel_m: float) -> str:
    """Write a constant UTM band of 1 km around the parcel centre."""
    x, y = Transformer.from_crs("EPSG:4326", UTM_CRS, always_xy=True).transform(
        CENTER_LON, CENTER_LAT
    )
    size = int(1000 / pixel_m)
    transform = from_origin(x - 500, y + 500, pixel_m, pixel_m)
    with rasterio.open(
        path, "w", driver="GTiff", height=size, width=size, count=1,
        dtype="float32", crs=UTM_CRS, transform=transform,
    ) as dst:
        dst.write(np.full((size, size), value, dtype=np.float32), 1)
    return str(path)


def _copernicus(band_paths: dict) -> MagicMock:
    client = MagicMock()
    client.search_scenes.return_value = [
        {"id": "S2A_TEST", "cloud_cover": 5, "sensing_date": "2026-06-01"}
    ]
    client.download_scene_bands.return_value = band_paths
    return client


def _run_window(index: str, band_paths: dict) -> MagicMock:
    from app.tasks import historical_baseline as hb

    parcel = _parcel_4326()
    with patch.object(hb, "upsert_eo_index") as upsert:
        created = hb._process_window(
            tenant_id="t1",
            entity_id="urn:ngsi-ld:AgriParcel:p1",
            intersects=parcel,
            bbox=[CENTER_LON - HALF_DEG, CENTER_LAT - HALF_DEG,
                  CENTER_LON + HALF_DEG, CENTER_LAT + HALF_DEG],
            copernicus_client=_copernicus(band_paths),
            window_start=date(2026, 6, 1),
            window_end=date(2026, 6, 20),
            index=index,
            cloud_threshold=30.0,
            required_bands=hb.index_bands(index),
        )
    assert created is True
    upsert.assert_called_once()
    return upsert


def test_ndvi_over_utm_raster_with_4326_parcel(tmp_path):
    bands = {
        "B04": _write_band(tmp_path / "B04.tif", 0.1, 10),
        "B08": _write_band(tmp_path / "B08.tif", 0.5, 10),
    }
    upsert = _run_window("NDVI", bands)
    stats = upsert.call_args.kwargs["statistics"]
    assert upsert.call_args.kwargs["index_type"] == "NDVI"
    assert stats["pixel_count"] > 0
    assert stats["mean"] == pytest.approx((0.5 - 0.1) / (0.5 + 0.1), abs=1e-4)


def test_gndvi_uses_green_band_not_ndvi(tmp_path):
    bands = {
        "B03": _write_band(tmp_path / "B03.tif", 0.2, 10),
        "B08": _write_band(tmp_path / "B08.tif", 0.6, 10),
    }
    upsert = _run_window("GNDVI", bands)
    stats = upsert.call_args.kwargs["statistics"]
    assert upsert.call_args.kwargs["index_type"] == "GNDVI"
    assert stats["mean"] == pytest.approx((0.6 - 0.2) / (0.6 + 0.2), abs=1e-4)


def test_ndre_mixes_20m_and_10m_bands(tmp_path):
    bands = {
        "B8A": _write_band(tmp_path / "B8A.tif", 0.3, 20),
        "B08": _write_band(tmp_path / "B08.tif", 0.5, 10),
    }
    upsert = _run_window("NDRE", bands)
    stats = upsert.call_args.kwargs["statistics"]
    assert stats["pixel_count"] > 0
    assert stats["mean"] == pytest.approx((0.5 - 0.3) / (0.5 + 0.3), abs=1e-3)


def test_osavi_uses_its_soil_adjusted_formula(tmp_path):
    bands = {
        "B04": _write_band(tmp_path / "B04.tif", 0.1, 10),
        "B08": _write_band(tmp_path / "B08.tif", 0.5, 10),
    }
    upsert = _run_window("OSAVI", bands)
    assert upsert.call_args.kwargs["index_type"] == "OSAVI"
    assert upsert.call_args.kwargs["statistics"]["mean"] == pytest.approx(0.4 / 0.76, abs=1e-4)


def test_unsupported_index_is_rejected():
    from app.tasks import historical_baseline as hb

    with pytest.raises(ValueError, match="Unsupported index"):
        hb.index_bands("NOPE")


def test_task_fails_when_every_window_fails():
    """A systematic processing error must not be reported as a successful empty run."""
    from app.tasks import historical_baseline as hb

    overlap_error = ValueError("Input shapes do not overlap raster")
    with patch.object(hb, "_get_parcel_geometry", return_value=(_parcel_4326(), [0, 0, 1, 1])), \
         patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback",
               return_value=None), \
         patch("app.services.copernicus_client.CopernicusDataSpaceClient"), \
         patch.object(hb, "_process_window", side_effect=overlap_error), \
         patch.object(hb.build_historical_baseline, "update_state"), \
         pytest.raises(RuntimeError, match="every processed window failed"):
        hb.build_historical_baseline.run(
            tenant_id="t1", entity_id="p1", years=1, index="NDVI", window_days=400,
        )
