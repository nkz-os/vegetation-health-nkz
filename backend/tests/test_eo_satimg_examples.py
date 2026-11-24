"""Conformance examples of EOSatelliteImagery (``sdm-examples/`` at the repository root).

Every example is built ONLY by ``build_entity``, from the same take constructors the writers use, and is
compared byte for byte with its file. To rewrite the files after a deliberate builder change, from ``backend/``:

    NKZ_REWRITE_SDM_EXAMPLES=1 python -m tests.test_eo_satimg_examples

All data is synthetic (including the land surface temperature takes: no real LST data exists yet).
"""
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

if __name__ == "__main__":  # pytest loads conftest itself; the rewrite entry needs its env defaults
    import tests.conftest  # noqa: F401

from nkz_platform_sdk import parse_eo_entities

from app.services import eo_satimg_optical as opt
from app.services import eo_satimg_radar_lst as rad
from app.services.eo_satellite_imagery import (
    Family,
    RasterMeta,
    Target,
    build_entity,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "sdm-examples"
REWRITE_ENV = "NKZ_REWRITE_SDM_EXAMPLES"

TENANT = "tenant-a"
UID = "11111111-2222-3333-4444-555555555555"
PARCEL = f"urn:ngsi-ld:AgriParcel:{TENANT}:{UID}"
ZONE = f"urn:ngsi-ld:AgriManagementZone:{UID}:0edc1602:L1"
GEOM = {"type": "Polygon", "coordinates": [[[-1.6, 42.8], [-1.59, 42.8], [-1.59, 42.81], [-1.6, 42.81], [-1.6, 42.8]]]}
SCENE_GEOM = {"type": "Polygon", "coordinates": [[[-1.7, 42.7], [-1.5, 42.7], [-1.5, 42.9], [-1.7, 42.9], [-1.7, 42.7]]]}
DAY = date(2026, 11, 12)
RASTER = f"s3://example-bucket/{TENANT}/{UID}/scene"

S2_ID = "S2A_MSIL2A_20261112T105621_N0511_R094_T30TXN_20261112T150000"
S1_ID = "S1C_IW_GRDH_1SDV_20261112T060102_20261112T060127_005432_00ABCD_1234_COG"
LANDSAT_ID = "LC09_L2SP_199030_20261112_02_T1"
CLMS_ID = "c_gls_LST_202611120700_GLOBE_GEO_V3.0.1_cog"

SENSING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}Z)?$")


def _targets():
    parcel = rad.parcel_target(PARCEL, GEOM)
    zone = rad.zone_target(PARCEL, ZONE, GEOM)
    return parcel, zone


def _optical(name, target, take, analytics, indices=()):
    layers = [(n, opt._EXPLANATION[n]) for n in opt.INDEX_NAMES if n in indices]
    raster = RasterMeta(take.raster_folder, layers) if take.raster_folder else None
    return name, build_entity(
        tenant=TENANT, target=target, family=take.family, sensing=take.sensing, analytics=analytics,
        scene=take.scene, raster=raster, result_description=take.description,
    )


def _radar(name, target, family, take, analytics, description):
    return name, build_entity(
        tenant=TENANT, target=target, family=family, sensing=take.sensing, analytics=analytics,
        scene=take.scene, raster=None, result_description=description,
    )


def _lst(name, target, take, analytics):
    return name, build_entity(
        tenant=TENANT, target=target, family=take.family, sensing=take.sensing, analytics=analytics,
        scene=take.scene, raster=None, result_description=take.description,
    )


def build_examples() -> dict:
    """File name -> entity, for every example, from the writers' own take constructors."""
    parcel, zone = _targets()

    scene = SimpleNamespace(
        scene_id=S2_ID, sensing_date=DAY, acquisition_datetime="2026-11-12T10:56:21Z", footprint=SCENE_GEOM,
    )
    s2_take = opt.scene_take(scene, raster_folder=RASTER)
    s2_zone_take = opt.scene_take(scene)
    composite = opt.composite_take(date(2026, 11, 1), DAY, raster_folder=RASTER)

    s2_stats = {"mean": 0.62, "min": 0.2, "max": 0.8, "std": 0.05}
    interior = {"valid_fraction": 0.98, "valid_pixels": 1200, "mask": "core", "mean": 0.63}
    s2_parcel = {
        **opt.index_analytics("NDVI", s2_stats, interior=interior),
        **opt.index_analytics("EVI", {"mean": 0.41}),
        "pixelCount": 1532, "validPixelFraction": 0.97, "cloudCoverage": 12.5, "parcelCloudFraction": 0.02,
    }
    s2_zone = {**opt.index_analytics("NDVI", {"mean": 0.55, "min": 0.2}),
               "pixelCount": 88, "validPixelFraction": 0.9, "cloudCoverage": 12.5}
    comp = {**opt.index_analytics("NDVI", {"mean": 0.6}), **opt.index_analytics("EVI", {"mean": 0.4}),
            "pixelCount": 1500}

    s1_params = {
        "sensing_date": "2026-11-12", "scene_datetime": "2026-11-12T06:01:02Z", "scene_platform": "sentinel-1c",
        "scene_product_type": "IW_GRDH_1S", "scene_relative_orbit": 30, "instrument_mode": "IW",
        "scene_footprint": SCENE_GEOM,
    }
    s1 = rad.radar_take(S1_ID, s1_params)
    bs = rad._backscatter_analytics({
        "VV": {"mean": -11.2, "pixel_count": 900, "valid_fraction": 0.98},
        "VH": {"mean": -17.5, "pixel_count": 880, "valid_fraction": 0.97},
    })
    bs_zone = rad._backscatter_analytics({  # a zone is written only with both polarisations
        "VV": {"mean": -10.8, "pixel_count": 60, "valid_fraction": 0.95},
        "VH": {"mean": -17.1, "pixel_count": 60, "valid_fraction": 0.95},
    })
    sm = {"soilMoistureIndex": 0.29, "soilMoistureIndex.min": 0.1, "soilMoistureIndex.max": 0.45,
          "soilMoistureIndex.std": 0.06, "pixelCount": 900}
    change = {"changeFlag": "harvest", "changeConfidence": 0.8, "deltaVV": -2.1}

    landsat = rad.landsat_take({
        "id": LANDSAT_ID, "geometry": SCENE_GEOM,
        "properties": {"datetime": "2026-11-12T10:56:21Z", "platform": "landsat-9"},
    })
    clms = rad.clms_take({
        "id": CLMS_ID, "geometry": SCENE_GEOM, "properties": {"datetime": "2026-11-12T07:00:00.000000Z", "product:type": "land_surface_temperature"},
    })
    lst = {"lst": 21.4, "lst.min": 19.0, "lst.max": 23.5, "lst.std": 1.2, "pixelCount": 40}

    pairs = [
        _optical("s2-indices.parcel", parcel, s2_take, s2_parcel, ("ndvi", "evi")),
        _optical("s2-indices.zone", zone, s2_zone_take, s2_zone),
        _optical("s2-composite.parcel", parcel, composite, comp, ("ndvi", "evi")),
        _radar("s1-backscatter.parcel", parcel, Family.S1_BACKSCATTER, s1, bs, rad._DESC_BACKSCATTER),
        _radar("s1-backscatter.zone", zone, Family.S1_BACKSCATTER, s1, bs_zone, rad._DESC_BACKSCATTER),
        _radar("s1-soil-moisture.parcel", parcel, Family.S1_SOIL_MOISTURE, s1, sm, rad._DESC_SOIL_MOISTURE),
        _radar("s1-change.parcel", parcel, Family.S1_CHANGE, s1, change, rad._DESC_CHANGE),
        _lst("landsat-lst.parcel", parcel, landsat, lst),
        _lst("clms-lst.parcel", parcel, clms, {**lst, "pixelCount": 2}),
    ]
    out = {}
    for name, entity in pairs:
        assert entity is not None, name
        out[f"EOSatelliteImagery.{name}.json"] = entity
    return out


def render(entity: dict) -> str:
    return json.dumps(entity, indent=2, ensure_ascii=False) + "\n"


def rewrite() -> None:
    EXAMPLES_DIR.mkdir(exist_ok=True)
    expected = build_examples()
    for stale in EXAMPLES_DIR.glob("*.json"):
        if stale.name not in expected:
            stale.unlink()
    for name, entity in expected.items():
        (EXAMPLES_DIR / name).write_text(render(entity), encoding="utf-8")


EXPECTED_FAMILY = {n: n.split(".")[1] for n in build_examples()}


def test_examples_match_builders():
    expected = build_examples()
    assert EXAMPLES_DIR.is_dir(), f"missing {EXAMPLES_DIR}; rewrite with {REWRITE_ENV}=1"
    assert sorted(p.name for p in EXAMPLES_DIR.iterdir()) == sorted(expected)
    for name, entity in expected.items():
        assert (EXAMPLES_DIR / name).read_text(encoding="utf-8") == render(entity), name


@pytest.mark.parametrize("name", sorted(EXPECTED_FAMILY))
def test_each_example_is_one_observation(name, caplog):
    entity = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
    assert "@context" not in entity
    with caplog.at_level(logging.DEBUG):
        found = parse_eo_entities([entity])
    assert len(found) == 1, name
    assert found[0].family == EXPECTED_FAMILY[name]
    assert found[0].sensing_day == DAY
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("name", sorted(EXPECTED_FAMILY))
def test_sensing_date_form(name):
    entity = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
    value = entity["sensingDate"]["value"]
    assert SENSING_DATE.match(value), value
    assert value.startswith(DAY.isoformat())
    # a composite has no acquisition time: day only
    if "composite" in name:
        assert "T" not in value


if __name__ == "__main__":
    if os.environ.get(REWRITE_ENV) != "1":
        sys.exit(f"set {REWRITE_ENV}=1 to rewrite {EXAMPLES_DIR}")
    rewrite()
    print(f"rewrote {len(build_examples())} examples in {EXAMPLES_DIR}")
