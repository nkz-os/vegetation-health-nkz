"""Unit tests for Sentinel-1 GRD methods on CopernicusDataSpaceClient."""
from datetime import date
from unittest.mock import patch, MagicMock
import pytest
from app.services.copernicus_client import CopernicusDataSpaceClient


class TestSearchS1Scenes:
    """search_s1_scenes() — STAC search for Sentinel-1 GRD."""

    def test_search_s1_scenes_returns_list(self):
        """search_s1_scenes returns a list of scene dicts."""
        client = CopernicusDataSpaceClient("id", "secret")
        with patch.object(client, "_get_optional_auth_headers", return_value={}):
            with patch("app.services.copernicus_client.requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "features": [
                        {
                            "id": "S1A_IW_GRDH_1SDV_20260601T180000",
                            "properties": {
                                "datetime": "2026-06-01T18:00:00Z",
                                "sar:instrument_mode": "IW",
                                "sar:polarizations": ["VV", "VH"],
                            },
                            "geometry": {"type": "Polygon", "coordinates": []},
                            "assets": {"vv": {"href": "s3://eodata/.../vv.tiff"}, "vh": {"href": "s3://eodata/.../vh.tiff"}},
                            "links": [],
                        }
                    ]
                }
                mock_post.return_value = mock_resp

                result = client.search_s1_scenes(
                    bbox=[-5.0, 40.0, -4.0, 41.0],
                    start_date=date(2026, 5, 1),
                    end_date=date(2026, 6, 7),
                )

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "S1A_IW_GRDH_1SDV_20260601T180000"
        assert result[0]["sensing_date"] == "2026-06-01"
        assert result[0]["polarizations"] == ["VV", "VH"]

    def test_search_s1_scenes_uses_correct_collection(self):
        """search_s1_scenes queries the sentinel-1-grd collection."""
        client = CopernicusDataSpaceClient("id", "secret")
        with patch.object(client, "_get_optional_auth_headers", return_value={}):
            with patch("app.services.copernicus_client.requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"features": []}
                mock_post.return_value = mock_resp

                client.search_s1_scenes(
                    bbox=[-5.0, 40.0, -4.0, 41.0],
                    start_date=date(2026, 5, 1),
                    end_date=date(2026, 6, 7),
                )

        call_args = mock_post.call_args
        body = call_args[1]["json"]
        assert "sentinel-1-grd" in body["collections"]
        assert body["collections"] == ["sentinel-1-grd"]

    def test_search_s1_scenes_filters_orbit_direction(self):
        """search_s1_scenes passes orbit_direction filter when given."""
        client = CopernicusDataSpaceClient("id", "secret")
        with patch.object(client, "_get_optional_auth_headers", return_value={}):
            with patch("app.services.copernicus_client.requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"features": []}
                mock_post.return_value = mock_resp

                client.search_s1_scenes(
                    bbox=[-5.0, 40.0, -4.0, 41.0],
                    start_date=date(2026, 5, 1),
                    end_date=date(2026, 6, 7),
                    orbit_direction="ASCENDING",
                )

        body = mock_post.call_args[1]["json"]
        assert body["query"]["sat:orbit_state"] == "ascending"

    def test_search_s1_scenes_empty_result(self):
        """search_s1_scenes returns empty list when no scenes found."""
        client = CopernicusDataSpaceClient("id", "secret")
        with patch.object(client, "_get_optional_auth_headers", return_value={}):
            with patch("app.services.copernicus_client.requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"features": []}
                mock_post.return_value = mock_resp

                result = client.search_s1_scenes(
                    bbox=[-5.0, 40.0, -4.0, 41.0],
                    start_date=date(2026, 5, 1),
                    end_date=date(2026, 6, 7),
                )

        assert result == []


class TestDownloadS1Bands:
    """download_s1_bands() — S3 download of VV and VH bands for S1 GRD."""

    def test_download_s1_bands_returns_paths(self):
        """download_s1_bands downloads both polarizations and returns path dict."""
        client = CopernicusDataSpaceClient("id", "secret")
        mock_s3 = MagicMock()
        client._s3_client = mock_s3

        with patch.object(client, "get_scene_item") as mock_item:
            mock_item.return_value = {
                "id": "S1A_IW_GRDH_1SDV_20260601T180000",
                "sensing_date": "2026-06-01",
                "cloud_cover": 0,
                "geometry": None,
                "assets": {
                    "vv": {"href": "s3://eodata/Sentinel-1/SAR/GRD/2026/06/01/vv.tiff"},
                    "vh": {"href": "s3://eodata/Sentinel-1/SAR/GRD/2026/06/01/vh.tiff"},
                },
                "links": [],
            }

            with patch("pathlib.Path.mkdir"):
                result = client.download_s1_bands(
                    "S1A_IW_GRDH_1SDV_20260601T180000",
                    polarizations=["vv", "vh"],
                    output_dir="/tmp/test_s1",
                )

        assert isinstance(result, dict)
        assert "vv" in result
        assert "vh" in result
        assert mock_s3.download_file.call_count == 2

    def test_download_s1_bands_handles_missing_polarization(self):
        """download_s1_bands skips polarizations not present in assets."""
        client = CopernicusDataSpaceClient("id", "secret")
        mock_s3 = MagicMock()
        client._s3_client = mock_s3

        with patch.object(client, "get_scene_item") as mock_item:
            mock_item.return_value = {
                "id": "S1A_IW_GRDH_1SDV_20260601T180000",
                "sensing_date": "2026-06-01",
                "cloud_cover": 0,
                "geometry": None,
                "assets": {
                    "vv": {"href": "s3://eodata/Sentinel-1/.../vv.tiff"},
                },
                "links": [],
            }

            with patch("pathlib.Path.mkdir"):
                result = client.download_s1_bands(
                    "S1A_IW_GRDH_1SDV_20260601T180000",
                    polarizations=["vv", "vh"],
                    output_dir="/tmp/test_s1",
                )

        # vh missing from assets -> only vv downloaded
        assert "vv" in result
        assert "vh" not in result
        assert mock_s3.download_file.call_count == 1
