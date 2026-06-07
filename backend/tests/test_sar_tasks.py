"""Tests for Sentinel-1 SAR tasks — integrated subscription pipeline."""
import sys
from unittest.mock import patch, MagicMock
import pytest
import uuid


class TestSARImports:
    """SAR tasks are properly registered and importable."""

    def test_import_from_tasks_init(self):
        """download_sentinel1_scene is exportable from app.tasks."""
        from app.tasks import download_sentinel1_scene
        assert download_sentinel1_scene.name == "vegetation.download_sentinel1_scene"

    def test_sar_not_in_celery_beat(self):
        """Standalone sar_crawl is removed (now integrated into scheduler)."""
        from app.celery_app import celery_app
        schedule = celery_app.conf.beat_schedule
        assert "vegetation.sar_crawl" not in schedule
        assert "vegetation.process_subscriptions" in schedule


class TestS1DownloadTask:
    """download_sentinel1_scene — one task: download + zonal stats + EOProduct."""

    @patch("app.tasks.sar_tasks.get_db_session")
    @patch("app.tasks.sar_tasks.get_copernicus_credentials_with_fallback")
    @patch("app.tasks.sar_tasks.CopernicusDataSpaceClient")
    def test_creates_calculate_index_jobs_for_vv_and_vh(
        self, mock_client_cls, mock_creds, mock_db
    ):
        """download_sentinel1_scene creates 2 VegetationJob records (SAR-VV, SAR-VH)."""
        from app.tasks.sar_tasks import download_sentinel1_scene

        mock_creds.return_value = {"client_id": "id", "client_secret": "secret"}

        mock_client = MagicMock()
        mock_client.download_s1_bands.return_value = {
            "vv": "/tmp/vv.tif",
            "vh": "/tmp/vh.tif",
        }
        mock_client_cls.return_value = mock_client

        import numpy as np
        mock_dataset = MagicMock()
        mock_dataset.read.return_value = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        mock_dataset.nodata = None
        mock_dataset.height = 2
        mock_dataset.width = 2
        mock_dataset.transform = MagicMock()
        mock_rasterio = MagicMock()
        mock_rasterio.open.return_value.__enter__.return_value = mock_dataset
        mock_rasterio.features = MagicMock()
        mock_rasterio.features.rasterize.return_value = np.array([[1, 1], [1, 1]], dtype=np.uint8)

        download_job = MagicMock()
        download_job.id = uuid.uuid4()

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            download_job,   # find download job
            None,           # no existing scene record
        ]
        mock_db.return_value = iter([mock_session])

        with patch.dict(sys.modules, {"rasterio": mock_rasterio, "rasterio.features": mock_rasterio.features}):
            with patch("os.path.isfile", return_value=True):
                with patch.object(download_sentinel1_scene, "update_state"):
                    with patch("app.tasks.sar_tasks.upsert_eo_product") as mock_eo:
                        mock_eo.return_value = "urn:ngsi-ld:EOProduct:test:parcel-1:GRD:2026-06-01"

                        download_sentinel1_scene(
                            job_id=str(uuid.uuid4()),
                            tenant_id="test-tenant",
                            parameters={
                                "scene_id": "S1A_TEST",
                                "entity_id": "urn:ngsi-ld:AgriParcel:parcel-1",
                                "sensing_date": "2026-06-01",
                                "bounds": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                            },
                        )

        # Verify EOProduct was published
        mock_eo.assert_called_once()

        # Verify VegetationJob add calls: 2 calculate_index jobs + 1 scene + download job updates
        add_calls = [
            c for c in mock_session.add.call_args_list
        ]
        # At least 2 add calls for SAR-VV and SAR-VH calculate_index jobs
        assert len(add_calls) >= 2

    @patch("app.tasks.sar_tasks.get_db_session")
    @patch("app.tasks.sar_tasks.get_copernicus_credentials_with_fallback")
    def test_handles_missing_vh_band(self, mock_creds, mock_db):
        """download_sentinel1_scene handles missing VH gracefully."""
        from app.tasks.sar_tasks import download_sentinel1_scene

        mock_creds.return_value = {"client_id": "id", "client_secret": "secret"}

        with patch("app.tasks.sar_tasks.CopernicusDataSpaceClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.download_s1_bands.return_value = {"vv": "/tmp/vv.tif"}  # no vh
            mock_client_cls.return_value = mock_client

            import numpy as np
            mock_dataset = MagicMock()
            mock_dataset.read.return_value = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
            mock_dataset.nodata = None
            mock_dataset.height = 2
            mock_dataset.width = 2
            mock_dataset.transform = MagicMock()
            mock_rasterio = MagicMock()
            mock_rasterio.open.return_value.__enter__.return_value = mock_dataset
            mock_rasterio.features = MagicMock()
            mock_rasterio.features.rasterize.return_value = np.array([[1, 1], [1, 1]], dtype=np.uint8)

            download_job = MagicMock()
            download_job.id = uuid.uuid4()

            mock_session = MagicMock()
            mock_session.query.return_value.filter.return_value.first.side_effect = [
                download_job,
                None,
            ]
            mock_db.return_value = iter([mock_session])

            with patch.dict(sys.modules, {"rasterio": mock_rasterio, "rasterio.features": mock_rasterio.features}):
                with patch("os.path.isfile", side_effect=lambda p: "vv" in p):
                    with patch.object(download_sentinel1_scene, "update_state"):
                        with patch("app.tasks.sar_tasks.upsert_eo_product") as mock_eo:
                            download_sentinel1_scene(
                                job_id=str(uuid.uuid4()),
                                tenant_id="test-tenant",
                                parameters={
                                    "scene_id": "S1A_TEST",
                                    "entity_id": "urn:ngsi-ld:AgriParcel:parcel-1",
                                    "sensing_date": "2026-06-01",
                                    "bounds": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                                },
                            )

        # EOProduct NOT published (need both VV and VH)
        mock_eo.assert_not_called()


class TestCopernicusS1Methods:
    """CopernicusDataSpaceClient S1 methods work."""

    def test_search_s1_uses_correct_collection(self):
        """search_s1_scenes queries sentinel-1-grd."""
        from app.services.copernicus_client import CopernicusDataSpaceClient
        from datetime import date

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

        body = mock_post.call_args[1]["json"]
        assert body["collections"] == ["sentinel-1-grd"]


class TestEOProductUpsert:
    """EOProduct upsert for crop-health integration."""

    @patch("app.services.fiware_integration.requests.post")
    def test_eo_product_has_correct_type(self, mock_post):
        """EOProduct entity payload has type EOProduct and productType GRD."""
        from app.services.fiware_integration import upsert_eo_product
        from datetime import datetime, timezone

        mock_post.return_value.status_code = 201

        upsert_eo_product(
            tenant_id="test",
            parcel_id="urn:ngsi-ld:AgriParcel:parcel-4",
            vv_mean=-12.3,
            vh_mean=-18.7,
            acquisition_date=datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc),
        )

        call_json = mock_post.call_args[1]["json"]
        assert call_json["type"] == "EOProduct"
        assert call_json["productType"]["value"] == "GRD"
