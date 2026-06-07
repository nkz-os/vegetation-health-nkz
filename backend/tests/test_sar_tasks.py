"""Tests for Sentinel-1 SAR tasks integrated into the subscription pipeline."""
import sys
from unittest.mock import patch, MagicMock
import pytest
import uuid


class TestSARImports:
    """SAR tasks are properly registered and importable."""

    def test_imports_from_tasks_init(self):
        """SAR tasks are exportable from app.tasks."""
        from app.tasks import download_sentinel1_scene, calculate_sar_backscatter
        assert download_sentinel1_scene.name == "vegetation.download_sentinel1_scene"
        assert calculate_sar_backscatter.name == "vegetation.calculate_sar_backscatter"

    def test_sar_not_in_celery_beat(self):
        """Standalone sar_crawl is removed from beat schedule (now integrated)."""
        from app.celery_app import celery_app
        schedule = celery_app.conf.beat_schedule
        assert "vegetation.sar_crawl" not in schedule
        assert "vegetation.process_subscriptions" in schedule  # existing still there


class TestS1CalculateTask:
    """calculate_sar_backscatter — computes zonal stats for VV or VH."""

    @patch("app.tasks.sar_tasks.get_db_session")
    def test_calculate_stores_result_with_index_type(self, mock_db):
        """calculate_sar_backscatter stores result with correct index_type."""
        from app.tasks.sar_tasks import calculate_sar_backscatter

        job_id = uuid.uuid4()
        scene_id = uuid.uuid4()

        mock_job = MagicMock()
        mock_job.id = job_id
        mock_job.parameters = {
            "raster_path": "/tmp/scene_vv.tif",
            "entity_id": "urn:ngsi-ld:AgriParcel:parcel-1",
            "sensing_date": "2026-06-01",
            "bounds": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        }

        mock_scene = MagicMock()
        mock_scene.id = scene_id
        mock_scene.scene_id = "S1A_TEST"

        mock_session = MagicMock()
        # First query returns job, second query returns scene
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            mock_job,
            mock_scene,
        ]
        mock_db.return_value = iter([mock_session])

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

        with patch.dict(sys.modules, {"rasterio": mock_rasterio, "rasterio.features": mock_rasterio.features}):
            with patch("os.path.isfile", return_value=True):
                # Celery task bind=True → self.update_state fails when called directly
                with patch.object(calculate_sar_backscatter, "update_state"):
                    calculate_sar_backscatter(
                        job_id=str(job_id),
                        tenant_id="test-tenant",
                        scene_id=str(scene_id),
                        index_type="SAR-VV",
                    )

        # Verify mark_completed was called with correct result
        mock_job.mark_completed.assert_called_once()
        result = mock_job.mark_completed.call_args[0][0]
        assert result["index_type"] == "SAR-VV"
        assert result["index_key"] == "SAR-VV"
        assert "statistics" in result
        assert "mean" in result["statistics"]
        # Same data as Task 3 test: [[1,2],[3,4]] → mean=2.5
        assert result["statistics"]["mean"] == 2.5


class TestCopernicusS1Methods:
    """CopernicusDataSpaceClient S1 methods work (from test_copernicus_s1.py)."""

    def test_search_s1_scenes_uses_correct_collection(self):
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
    """EOProduct upsert works (from test_eo_product_upsert.py)."""

    @patch("app.services.fiware_integration.requests.post")
    def test_eo_product_has_correct_type(self, mock_post):
        """EOProduct entity has type EOProduct."""
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
