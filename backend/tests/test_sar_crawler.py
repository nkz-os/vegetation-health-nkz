"""Tests for SAR crawler — Sentinel-1 ingestion job."""
import sys
from unittest.mock import patch, MagicMock
from datetime import date
import pytest
from app.jobs.sar_crawler import (
    compute_parcel_backscatter,
    _should_skip_parcel,
    sar_crawl_task,
)


class TestComputeParcelBackscatter:
    """compute_parcel_backscatter() — zonal stats from S1 GeoTIFF."""

    @patch("os.path.isfile", return_value=True)
    def test_compute_mean_from_raster(self, mock_isfile):
        """compute_parcel_backscatter returns mean VV and VH for a parcel polygon."""
        # compute_parcel_backscatter uses internal imports — mock sys.modules
        import sys
        import numpy as np
        from shapely.geometry import Polygon
        mock_rasterio = MagicMock()
        mock_dataset = MagicMock()
        mock_dataset.read.return_value = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        mock_dataset.nodata = None
        mock_dataset.height = 2
        mock_dataset.width = 2
        mock_dataset.transform = MagicMock()
        mock_rasterio.open.return_value.__enter__.return_value = mock_dataset
        mock_rasterio.features = MagicMock()
        mock_rasterio.features.rasterize.return_value = np.array([[1, 1], [1, 1]], dtype=np.uint8)

        with patch.dict(sys.modules, {"rasterio": mock_rasterio, "rasterio.features": mock_rasterio.features}):
            result = compute_parcel_backscatter(
                vv_path="/tmp/scene_vv.tif",
                vh_path="/tmp/scene_vh.tif",
                parcel_geometry={
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
            )

        assert result is not None
        vv, vh = result
        # With data [[1,2],[3,4]] mean = 2.5
        assert vv == 2.5
        assert vh == 2.5

    @patch("os.path.isfile")
    def test_compute_returns_none_when_vv_missing(self, mock_isfile):
        """compute_parcel_backscatter returns None if VV file doesn't exist."""
        mock_isfile.side_effect = lambda p: "vh" in p

        result = compute_parcel_backscatter(
            vv_path="/nonexistent/vv.tif",
            vh_path="/tmp/scene_vh.tif",
            parcel_geometry={
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )
        assert result is None


class TestShouldSkipParcel:
    """_should_skip_parcel() — avoid re-processing already-ingested scenes."""

    @patch("app.jobs.sar_crawler.requests.get")
    def test_skip_when_eo_product_exists(self, mock_get):
        """_should_skip_parcel returns True when EOProduct already exists."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": "urn:ngsi-ld:EOProduct:test:parcel-4:GRD:2026-06-01"}
        ]
        mock_get.return_value = mock_resp

        result = _should_skip_parcel(
            parcel_id="urn:ngsi-ld:AgriParcel:parcel-4",
            tenant_id="test",
            sensing_date="2026-06-01",
            orion_url="http://orion:1026",
        )

        assert result is True

    @patch("app.jobs.sar_crawler.requests.get")
    def test_process_when_no_eo_product(self, mock_get):
        """_should_skip_parcel returns False when no EOProduct exists."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_get.return_value = mock_resp

        result = _should_skip_parcel(
            parcel_id="urn:ngsi-ld:AgriParcel:parcel-4",
            tenant_id="test",
            sensing_date="2026-06-01",
            orion_url="http://orion:1026",
        )

        assert result is False


class TestSarCrawlTask:
    """sar_crawl_task — the Celery task entry point."""

    @patch("app.services.platform_credentials.get_copernicus_credentials_with_fallback")
    @patch("app.jobs.sar_crawler.requests.get")
    @patch("app.jobs.sar_crawler.CopernicusDataSpaceClient")
    def test_task_fetches_parcels_and_searches(
        self, mock_client_cls, mock_get, mock_creds
    ):
        """sar_crawl_task fetches parcels, searches S1, handles no scenes."""
        mock_creds.return_value = {"client_id": "id", "client_secret": "secret"}

        mock_parcel_resp = MagicMock()
        mock_parcel_resp.status_code = 200
        mock_parcel_resp.json.return_value = [
            {
                "id": "urn:ngsi-ld:AgriParcel:parcel-1",
                "type": "AgriParcel",
                "location": {
                    "type": "GeoProperty",
                    "value": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                    },
                },
            }
        ]
        mock_get.return_value = mock_parcel_resp

        mock_client = MagicMock()
        mock_client.search_s1_scenes.return_value = []
        mock_client_cls.return_value = mock_client

        result = sar_crawl_task()

        assert result is not None
        assert "parcels_processed" in result
        assert mock_get.called
        assert mock_client.search_s1_scenes.called
