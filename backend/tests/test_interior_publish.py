"""EOProduct publication of the interior statistics and the existing-product reader."""
import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.mask", "shapely", "shapely.geometry",
    "shapely.ops", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import pytest

import app.services.fiware_integration as fi
from tests.fake_orion import FakeAsyncOrion

PARCEL = "urn:ngsi-ld:AgriParcel:parcel-1"
DAY = date(2026, 6, 21)
STATS = {"mean": 0.61, "min": 0.1, "max": 0.9, "std": 0.05, "pixel_count": 42}
INTERIOR = {"median": 0.6123456789, "mean": 0.61, "p10": 0.4, "p90": 0.8,
            "valid_pixels": 30, "total_pixels": 40, "valid_fraction": 0.75, "mask": "core"}


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAsyncOrion.last_instance = None
    yield
    FakeAsyncOrion.last_instance = None


def _publish(**kwargs):
    with patch.object(fi, "OrionClient", FakeAsyncOrion):
        eid = fi.upsert_eo_index("acme", PARCEL, "NDVI", STATS, DAY,
                                 raster_url="s3://b/x.tif", preview_url="s3://b/x.png",
                                 cloud_cover=3.0, **kwargs)
    assert eid
    return FakeAsyncOrion.last_instance.entities[0]


def test_index_attribute_carries_interior_subproperties():
    body = _publish(interior=INTERIOR, parcel_cloud_fraction=0.12345)
    ndvi = body["ndvi"]
    assert ndvi["interiorMedian"] == {"type": "Property", "value": 0.612346}
    assert ndvi["interiorMean"]["value"] == 0.61
    assert ndvi["interiorP10"]["value"] == 0.4 and ndvi["interiorP90"]["value"] == 0.8
    assert ndvi["interiorValidFraction"]["value"] == 0.75
    assert ndvi["interiorPixelCount"]["value"] == 30
    assert ndvi["interiorMask"]["value"] == "core"
    assert body["parcelCloudFraction"] == {"type": "Property", "value": 0.1235}


def test_interior_without_valid_pixels_publishes_fraction_only():
    interior = {"median": None, "mean": None, "p10": None, "p90": None, "valid_pixels": 0,
                "total_pixels": 40, "valid_fraction": 0.0, "mask": "parcel"}
    ndvi = _publish(interior=interior)["ndvi"]
    for absent in ("interiorMedian", "interiorMean", "interiorP10", "interiorP90"):
        assert absent not in ndvi
    assert ndvi["interiorValidFraction"]["value"] == 0.0
    assert ndvi["interiorPixelCount"]["value"] == 0
    assert ndvi["interiorMask"]["value"] == "parcel"


def test_existing_attributes_unchanged():
    plain = _publish()
    FakeAsyncOrion.last_instance = None
    rich = _publish(interior=INTERIOR, parcel_cloud_fraction=0.125)
    assert "parcelCloudFraction" not in plain
    assert not [k for k in plain["ndvi"] if k.startswith("interior")]
    stripped = {k: v for k, v in rich.items() if k != "parcelCloudFraction"}
    stripped["ndvi"] = {k: v for k, v in rich["ndvi"].items() if not k.startswith("interior")}
    assert stripped == plain
    assert plain["ndvi"]["value"] == 0.61 and plain["ndvi"]["min"]["value"] == 0.1
    assert plain["ndvi"]["max"]["value"] == 0.9 and plain["ndvi"]["std"]["value"] == 0.05
    assert plain["pixelCount"]["value"] == 42
    assert plain["cloudCoverPercentage"]["value"] == 3.0
    assert plain["ndvi"]["rasterUrl"]["value"] == "s3://b/x.tif"
    assert plain["ndvi"]["previewUrl"]["value"] == "s3://b/x.png"


class _Orion:
    def __init__(self, entity=None, exc=None):
        self.entity, self.exc, self.asked = entity, exc, []

    def get_entity(self, entity_id, options=None):
        self.asked.append((entity_id, options))
        if self.exc:
            raise self.exc
        return self.entity


def _read(orion):
    with patch.object(fi, "SyncOrionClient", lambda tenant: orion):
        return fi.existing_interior_observation("acme", PARCEL, DAY, "NDVI")


def test_existing_reader_short_names():
    orion = _Orion({"ndvi": {"type": "Property", "value": 0.5,
                             "interiorValidFraction": {"type": "Property", "value": 0.8},
                             "rasterUrl": {"type": "Property", "value": "s3://b/scenes/u1/NDVI.tif"}}})
    assert _read(orion) == (0.8, "s3://b/scenes/u1/NDVI.tif")
    assert orion.asked == [(fi._entity_id_for_acquisition("acme", PARCEL, "2026-06-21"), None)]


def test_existing_reader_accepts_full_iri():
    for base in ("https://nkz-os.org/ns/", "https://uri.etsi.org/ngsi-ld/default-context/"):
        orion = _Orion({base + "ndvi": {"type": "Property", "value": 0.5,
                        base + "interiorValidFraction": {"type": "Property", "value": 0.8},
                        base + "rasterUrl": {"type": "Property", "value": "s3://b/scenes/u1/NDVI.tif"}}})
        assert _read(orion) == (0.8, "s3://b/scenes/u1/NDVI.tif")


def test_existing_reader_product_before_interior_has_no_fraction():
    orion = _Orion({"ndvi": {"type": "Property", "value": 0.5,
                             "rasterUrl": {"type": "Property", "value": "s3://b/scenes/u1/NDVI.tif"}}})
    assert _read(orion) == (None, "s3://b/scenes/u1/NDVI.tif")
    assert _read(_Orion({"ndre": {"type": "Property", "value": 0.5}})) == (None, None)


class _HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"{status} error")
        self.response = MagicMock(status_code=status)


def test_existing_reader_missing_entity_is_none_other_errors_raise():
    assert _read(_Orion(exc=_HTTPError(404))) is None
    with pytest.raises(_HTTPError):
        _read(_Orion(exc=_HTTPError(503)))
