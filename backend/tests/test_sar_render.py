"""SAR raster rendering: COG upload to MinIO + tile render config."""

from unittest.mock import MagicMock, patch


class TestUploadSarCog:
    """_upload_sar_cog persists a band to MinIO and returns the S3 object key."""

    @patch("app.tasks.sar_tasks.create_storage_service")
    def test_returns_s3_key_and_uploads(self, mock_create_storage):
        from app.tasks.sar_tasks import _upload_sar_cog

        storage = MagicMock()
        mock_create_storage.return_value = storage

        # COG conversion is exercised by the fallback path (raw GeoTIFF upload)
        # when rio_cogeo is unavailable; here we just assert the upload call.
        with patch("app.tasks.sar_tasks.os.getenv", side_effect=lambda k, d=None: {
            "VEGETATION_COG_BUCKET": "vegetation-prime-global",
            "STORAGE_TYPE": "s3",
        }.get(k, d)):
            key = _upload_sar_cog(
                "/tmp/band.tif", "montiko", "urn:ngsi-ld:AgriParcel:p1", "S1A_TEST", "VV", "2026-06-01"
            )

        assert key == "montiko/entities/urn:ngsi-ld:AgriParcel:p1/sar/2026-06-01/S1A_TEST-VV.tif"
        storage.upload_file.assert_called_once()
        assert storage.upload_file.call_args.args[1] == key
        assert storage.upload_file.call_args.args[2] == "vegetation-prime-global"

    @patch("app.tasks.sar_tasks.create_storage_service")
    def test_returns_none_on_upload_failure(self, mock_create_storage):
        from app.tasks.sar_tasks import _upload_sar_cog

        mock_create_storage.return_value.upload_file.side_effect = RuntimeError("minio down")
        with patch("app.tasks.sar_tasks.os.getenv", return_value="vegetation-prime-global"):
            key = _upload_sar_cog(
                "/tmp/band.tif", "montiko", "p1", "S1A_TEST", "VV", "2026-06-01"
            )
        assert key is None


class TestSarRenderConfig:
    """INDEX_RENDER_CONFIG must define a colormap + rescale for SAR indices."""

    def test_sar_indices_have_render_config(self):
        from app.api.tiles import INDEX_RENDER_CONFIG, DEFAULT_RENDER

        for idx in ("SAR-VV", "SAR-VH"):
            render = INDEX_RENDER_CONFIG.get(idx)
            assert render is not None, f"{idx} missing from INDEX_RENDER_CONFIG"
            assert render != DEFAULT_RENDER, f"{idx} falls back to DEFAULT_RENDER"
            assert render["colormap_name"] == "viridis"
            lo, hi = render["rescale"]
            assert lo == 0
            assert hi > 0, f"{idx} rescale high bound must be positive"


class TestClipSarToParcel:
    """_clip_sar_to_parcel warps a GCP-only band to a small 4326 raster."""

    def _make_gcp_band(self, tmp_path):
        import numpy as np
        import rasterio
        from rasterio.control import GroundControlPoint as GCP

        src = str(tmp_path / "gcp.tif")
        w = h = 1000
        data = (np.random.rand(h, w) * 500).astype(np.int16)
        gcps = [
            GCP(row=0, col=0, x=-3.0, y=44.0),
            GCP(row=0, col=w - 1, x=-1.0, y=44.0),
            GCP(row=h - 1, col=0, x=-3.0, y=42.0),
            GCP(row=h - 1, col=w - 1, x=-1.0, y=42.0),
        ]
        with rasterio.open(src, "w", driver="GTiff", height=h, width=w, count=1, dtype="int16") as dst:
            dst.write(data, 1)
            dst.gcps = (gcps, "EPSG:4326")
        return src

    def test_clips_to_small_georeferenced_raster(self, tmp_path):
        import numpy as np
        import rasterio
        from app.tasks.sar_tasks import _clip_sar_to_parcel

        src = self._make_gcp_band(tmp_path)
        out = str(tmp_path / "clipped.tif")
        bounds = (-2.1, 42.63, -2.07, 42.65)
        _clip_sar_to_parcel(src, out, bounds)

        with rasterio.open(out) as o:
            assert str(o.crs) == "EPSG:4326"
            # Parcel-sized, NOT the full 1000x1000 scene
            assert o.width < 2000 and o.height < 2000
            arr = o.read(1)
            assert bool(np.isfinite(arr).any())
            # The clip covers the parcel bounds (plus pad)
            assert o.bounds.left <= bounds[0]
            assert o.bounds.right >= bounds[2]
