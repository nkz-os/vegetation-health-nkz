"""Pure builders for the EOSatelliteImagery entity (contract sections 1-5)."""
import logging
import math
from datetime import date, datetime, timezone

import numpy as np
import pytest
from nkz_platform_sdk import EO_FAMILIES, EO_VALUE_NAMES, parse_eo_entities

from app.services import eo_satellite_imagery as eo
from app.services.eo_satellite_imagery import (
    Family,
    RasterMeta,
    SceneMeta,
    Sensing,
    Target,
    build_entity,
    entity_id,
    format_value,
)

PARCEL = "urn:ngsi-ld:AgriParcel:tenant-a:p1"
ZONE = "urn:ngsi-ld:AgriManagementZone:tenant-a:z1"
GEOM = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}
DAY = date(2026, 11, 12)
TIME = datetime(2026, 11, 12, 10, 56, 21, tzinfo=timezone.utc)
AN = {"ndvi": 0.62, "evi": 0.41, "pixelCount": 1532}


def parcel_target(geometry=GEOM):
    return Target("parcel", PARCEL, None, None, geometry)


def zone_target(geometry=GEOM, relation="hasAgriManagementZone"):
    return Target("zone", PARCEL, ZONE, relation, geometry)


def scene():
    return SceneMeta(
        product_id="S2A_X",
        product_url="https://catalog.example.org/items/S2A_X",
        product_format="SAFE",
        product_type="S2MSI2A",
        platform_name="S2A",
        orbit_number=37,
        operational_mode="MSI",
        footprint=GEOM,
    )


def build(**kw):
    args = dict(
        tenant="tenant-a",
        target=parcel_target(),
        family=Family.S2_INDICES,
        sensing=Sensing(DAY, TIME),
        analytics=dict(AN),
        scene=scene(),
        raster=None,
        result_description="Sentinel-2 vegetation indices.",
    )
    args.update(kw)
    return build_entity(**args)


def analytics_of(entity):
    return {i["name"]: i["value"] for i in entity["analytics"]["value"]}


def test_id_parcel_full_segment_not_truncated():
    seg = "123e4567-e89b-12d3-a456-426614174000"
    assert len(seg) == 36
    got = entity_id("tenant-a", "parcel", f"urn:ngsi-ld:AgriParcel:tenant-a:{seg}", Family.S2_INDICES, DAY)
    assert got == f"urn:ngsi-ld:EOSatelliteImagery:tenant-a:parcel:tenant-a:{seg}:s2-indices:2026-11-12"


def test_id_zone_uses_full_zone_urn():
    z = "urn:ngsi-ld:AgriManagementZone:p-uuid:0edc1602:L1"
    got = entity_id("tenant-a", "zone", z, Family.S2_INDICES, DAY)
    assert got == "urn:ngsi-ld:EOSatelliteImagery:tenant-a:zone:p-uuid:0edc1602:L1:s2-indices:2026-11-12"


def test_id_zone_same_level_different_parcels_differ():
    a = entity_id("tenant-a", "zone", "urn:ngsi-ld:AgriManagementZone:p1:run1:L1", Family.S2_INDICES, DAY)
    b = entity_id("tenant-a", "zone", "urn:ngsi-ld:AgriManagementZone:p2:run1:L1", Family.S2_INDICES, DAY)
    assert a != b


def test_id_parcel_urn_with_two_segments():
    got = entity_id("tenant-a", "parcel", PARCEL, Family.S2_INDICES, DAY)
    assert got == "urn:ngsi-ld:EOSatelliteImagery:tenant-a:parcel:tenant-a:p1:s2-indices:2026-11-12"


@pytest.mark.parametrize("bad", ["p1", "urn:other:AgriParcel:x", "urn:ngsi-ld:", "urn:ngsi-ld:AgriParcel:", "urn:ngsi-ld::x"])
def test_id_rejects_malformed_urn(bad):
    with pytest.raises(ValueError):
        entity_id("tenant-a", "parcel", bad, Family.S2_INDICES, DAY)


def test_format_value_fixed_decimals():
    assert format_value("ndvi", 0.1234567) == "0.123457"
    assert format_value("backscatterVV", -12.345678) == "-12.3457"
    assert format_value("lst", 21.456) == "21.46"
    assert format_value("pixelCount", 1532.0) == "1532"
    assert format_value("ndvi", 1e-7) == "0.000000"
    assert "e" not in format_value("ndvi", 1e-7).lower()
    assert format_value("ndvi", -1e-9) == "0.000000"
    assert format_value("cloudCoverage", 12.3456) == "12.35"
    assert format_value("incidenceAngle", 39.1234) == "39.12"
    assert format_value("deltaVV", 1.234567) == "1.2346"
    assert format_value("soilMoistureIndex", 0.123456) == "0.1235"
    assert format_value("validPixelFraction", 0.98765) == "0.9877"
    assert format_value("changeFlag", "increase") == "increase"
    # statistics follow their value
    assert format_value("ndvi.min", 0.1234567) == "0.123457"
    assert format_value("ndvi.interiorMean", 0.1234567) == "0.123457"
    assert format_value("backscatterVV.std", 1.234567) == "1.2346"
    assert format_value("lst.max", 21.456) == "21.46"
    assert format_value("ndvi.interiorPixelCount", 12.0) == "12"
    assert format_value("ndvi.interiorValidFraction", 0.98765) == "0.9877"
    assert format_value("ndvi.interiorMask", "core") == "core"


def test_nan_and_none_are_omitted():
    e = build(analytics={"ndvi": 0.5, "evi": 0.4, "savi": float("nan"), "ndre": None, "ndmi": math.inf})
    assert analytics_of(e) == {"ndvi": "0.500000", "evi": "0.400000"}


def test_less_than_two_analytics_returns_none(caplog):
    with caplog.at_level(logging.WARNING):
        assert build(analytics={"ndvi": 0.5, "evi": float("nan")}) is None
    text = caplog.text
    assert "tenant-a" in text and PARCEL in text and "s2-indices" in text and "2026-11-12" in text


def test_zone_entity_has_parcel_and_zone_relation():
    e = build(target=zone_target())
    assert e["hasAgriParcel"] == {"type": "Relationship", "object": PARCEL}
    assert e["hasAgriManagementZone"] == {"type": "Relationship", "object": ZONE}
    assert "hasAgriParcelZone" not in e
    assert e["id"] == "urn:ngsi-ld:EOSatelliteImagery:tenant-a:zone:tenant-a:z1:s2-indices:2026-11-12"
    with pytest.raises(ValueError):
        build(target=zone_target(relation="hasAgriParcelZone"))


def test_parcel_entity_has_no_zone_relation():
    e = build()
    assert "hasAgriManagementZone" not in e
    assert e["hasAgriParcel"]["object"] == PARCEL


def test_target_kind_consistent():
    with pytest.raises(ValueError):
        build(target=Target("parcel", PARCEL, ZONE, "hasAgriManagementZone", GEOM))
    with pytest.raises(ValueError):
        build(target=Target("zone", PARCEL, None, None, GEOM))
    with pytest.raises(ValueError):
        build(target=Target("zone", PARCEL, ZONE, None, GEOM))


def test_composite_sensing_is_date_only():
    with pytest.raises(ValueError):
        build(family=Family.S2_COMPOSITE, sensing=Sensing(DAY, TIME))
    e = build(family=Family.S2_COMPOSITE, sensing=Sensing(DAY, None), scene=None)
    assert e["sensingDate"]["value"] == "2026-11-12"
    assert e["id"].endswith(":s2-composite:2026-11-12")
    assert e["analysisType"]["value"] == "vegetation-indices-composite"


def test_time_must_belong_to_the_day():
    with pytest.raises(ValueError):
        build(sensing=Sensing(date(2026, 11, 13), TIME))


def test_unknown_analytic_name_raises():
    with pytest.raises(ValueError):
        build(analytics={"ndvi": 0.5, "evi": 0.4, "sarMoisture": 0.3})
    with pytest.raises(ValueError):
        build(analytics={"ndvi": 0.5, "evi": 0.4, "ndvi.bogus": 0.3})
    with pytest.raises(ValueError):
        format_value("nope", 1.0)


def test_family_enum_matches_sdk():
    assert tuple(f.value for f in Family) == EO_FAMILIES
    assert all(f.analysis_type for f in Family)
    assert all(n in EO_VALUE_NAMES for n in ("ndvi", "lst"))


def test_no_geometry_omits_processed_bbox():
    e = build(target=parcel_target(geometry=None))
    assert "processedBbox" not in e
    e = build()
    assert e["processedBbox"] == {"type": "GeoProperty", "value": GEOM}


def test_raster_layers_are_analytics_names():
    r = RasterMeta("/rasters/p1/2026-11-12", [("ndvi", "NDVI, dimensionless"), ("evi", "EVI, dimensionless")])
    e = build(raster=r)
    assert e["localServerPath"]["value"] == "/rasters/p1/2026-11-12"
    assert e["storageFormat"]["value"] == "GeoTIFF"
    assert e["contentInformation"]["value"] == [
        {"layer_name": "ndvi", "layer_categorization": "numerical", "values_explanation": ["NDVI, dimensionless"]},
        {"layer_name": "evi", "layer_categorization": "numerical", "values_explanation": ["EVI, dimensionless"]},
    ]
    with pytest.raises(ValueError):
        build(raster=RasterMeta("/r", [("ndvi", "x"), ("savi", "y")]))


def test_relationships_have_no_type_id_context():
    e = build(target=zone_target())
    assert "@context" not in e
    for key in ("hasAgriParcel", "hasAgriManagementZone"):
        assert set(e[key]) == {"type", "object"}
    assert "@type" not in repr(e)


def test_no_scene_keeps_only_product_type_and_date_only_sensing():
    e = build(scene=None, sensing=Sensing(DAY, None))
    assert e["sensingDate"]["value"] == "2026-11-12"
    assert e["productType"]["value"] == "S2MSI2A"
    for k in ("productID", "productURL", "productFormat", "platformName", "orbitNumber", "operationalMode", "productBbox"):
        assert k not in e


def test_scene_attributes():
    e = build(scene=scene())
    assert e["productID"]["value"] == "S2A_X"
    assert e["productURL"]["value"].startswith("https://")
    assert e["productFormat"]["value"] == "SAFE"
    assert e["productType"]["value"] == "S2MSI2A"
    assert e["platformName"]["value"] == "S2A"
    assert e["orbitNumber"]["value"] == 37
    assert e["operationalMode"]["value"] == "MSI"
    assert e["productBbox"] == {"type": "GeoProperty", "value": GEOM}


def test_content_information_only_with_two_layers():
    one = build(raster=RasterMeta("/r", [("ndvi", "x")]))
    assert "contentInformation" not in one and one["localServerPath"]["value"] == "/r"
    none = build(raster=RasterMeta("/r", []))
    assert "contentInformation" not in none
    assert "contentInformation" not in build(raster=None)


def test_forbidden_attributes_absent():
    e = build(target=zone_target(), raster=RasterMeta("/r", [("ndvi", "x"), ("evi", "y")]))
    for k in ("refAgriParcel", "campaign", "source", "rasterUrl", "previewUrl", "processingLevel",
              "acquisitionDate", "sarSceneId", "lstSourceScene", "ndvi", "evi", "pixelCount"):
        assert k not in e
    assert e["type"] == "EOSatelliteImagery"
    assert e["provider"]["value"] == "Nekazari vegetation module"
    assert e["resultDescription"]["value"] == "Sentinel-2 vegetation indices."


def test_sensing_date_with_real_time_iso_z():
    assert build()["sensingDate"]["value"] == "2026-11-12T10:56:21Z"


def test_analytics_are_strings_never_exponent():
    e = build(analytics={"ndvi": 1e-7, "evi": -0.0, "pixelCount": 3})
    for item in e["analytics"]["value"]:
        assert isinstance(item["value"], str)
        assert "e" not in item["value"].lower()
        assert item["value"] != "-0.000000"
    assert analytics_of(e)["evi"] == "0.000000"


def test_sdk_parses_built_entities(caplog):
    p = build(
        analytics={"ndvi": 0.62, "evi": 0.41, "pixelCount": 1532, "ndvi.interiorMask": "core",
                   "ndvi.interiorMean": 0.6, "ndvi.min": 0.1},
        raster=RasterMeta("/rasters/p1/2026-11-12", [("ndvi", "x"), ("evi", "y")]),
    )
    z = build(
        target=zone_target(),
        family=Family.S1_BACKSCATTER,
        sensing=Sensing(date(2026, 11, 13), None),
        analytics={"backscatterVV": -12.345678, "backscatterVH": -18.5, "pixelCount": 40},
        scene=None,
        result_description="Sentinel-1 backscatter.",
    )
    with caplog.at_level(logging.WARNING, logger="nkz_platform_sdk.eo"):
        obs = {o.family: o for o in parse_eo_entities([p, z])}
    assert [r for r in caplog.records if r.name.startswith("nkz_platform_sdk.eo")] == []
    assert set(obs) == {"s2-indices", "s1-backscatter"}
    a = obs["s2-indices"]
    assert (a.target, a.parcel_id, a.zone_id) == ("parcel", PARCEL, None)
    assert a.sensing_day == DAY and a.sensing_time == TIME
    assert set(a.values) == {"ndvi", "evi", "pixelCount", "ndvi.interiorMask", "ndvi.interiorMean", "ndvi.min"}
    assert a.values["ndvi"] == pytest.approx(0.62) and a.values["pixelCount"] == 1532
    assert a.values["ndvi.interiorMask"] == "core"
    assert a.raster_path == "/rasters/p1/2026-11-12"
    assert a.raster_url("ndvi") == f"{a.raster_path}/ndvi.tif"
    b = obs["s1-backscatter"]
    assert (b.target, b.parcel_id, b.zone_id) == ("zone", PARCEL, ZONE)
    assert b.sensing_day == date(2026, 11, 13) and b.sensing_time is None
    assert set(b.values) == {"backscatterVV", "backscatterVH", "pixelCount"}
    assert b.values["backscatterVV"] == pytest.approx(-12.3457)
    assert b.product_type == "GRD"


# ── fix round 1 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [1532.4, 2.5, -3, 0, True])
def test_pixel_count_must_be_whole_and_positive(bad):
    with pytest.raises(ValueError):
        format_value("pixelCount", bad)
    with pytest.raises(ValueError):
        build(analytics={"ndvi": 0.5, "evi": 0.4, "pixelCount": bad})


@pytest.mark.parametrize("bad", [2.5, -1])
def test_interior_pixel_count_whole_and_non_negative(bad):
    with pytest.raises(ValueError):
        format_value("ndvi.interiorPixelCount", bad)


def test_interior_pixel_count_zero_is_valid():
    assert format_value("ndvi.interiorPixelCount", 0) == "0"
    assert format_value("pixelCount", 1) == "1"


def test_analytics_sorted_by_name():
    e = build(analytics={"pixelCount": 5, "evi": 0.4, "ndvi": 0.5, "ndvi.min": 0.1})
    names = [i["name"] for i in e["analytics"]["value"]]
    assert names == sorted(names) == ["evi", "ndvi", "ndvi.min", "pixelCount"]


def test_every_sdk_value_name_has_a_class():
    for name in EO_VALUE_NAMES:
        eo._classify(name)  # raises when the writer has no class for it


def test_name_without_class_raises(monkeypatch):
    monkeypatch.setattr(eo, "EO_VALUE_NAMES", tuple(EO_VALUE_NAMES) + ("brandNew",))
    with pytest.raises(ValueError):
        eo._classify("brandNew")


def test_interior_only_on_indices_of_s2_indices():
    ok = {"ndvi": 0.5, "evi": 0.4, "ndvi.interiorMean": 0.5}
    assert build(analytics=ok) is not None
    with pytest.raises(ValueError):
        build(family=Family.S2_COMPOSITE, sensing=Sensing(DAY, None), scene=None, analytics=ok)
    with pytest.raises(ValueError):
        build(family=Family.LANDSAT_LST, analytics={"lst": 20.0, "lst.interiorMean": 20.0})
    with pytest.raises(ValueError):
        build(family=Family.S1_BACKSCATTER, analytics={"backscatterVV": -10.0, "backscatterVH": -15.0,
                                                       "backscatterVV.interiorMean": -10.0})


def test_interior_mask_values():
    assert format_value("ndvi.interiorMask", "parcel") == "parcel"
    with pytest.raises(ValueError):
        format_value("ndvi.interiorMask", "other")


def test_no_pixel_count_in_s1_change():
    with pytest.raises(ValueError):
        build(family=Family.S1_CHANGE, analytics={"changeFlag": "increase", "changeConfidence": 0.5, "pixelCount": 9})
    e = build(family=Family.S1_CHANGE, analytics={"changeFlag": "increase", "changeConfidence": 0.5})
    assert e is not None


@pytest.mark.parametrize(
    "family,analytics",
    [
        (Family.S2_INDICES, {"validPixelFraction": 0.9, "pixelCount": 10}),
        (Family.S1_BACKSCATTER, {"pixelCount": 10, "incidenceAngle": 39.0}),
        (Family.S1_SOIL_MOISTURE, {"pixelCount": 10, "validPixelFraction": 0.9}),
        (Family.S1_CHANGE, {"changeConfidence": 0.5, "deltaVV": 1.0}),
        (Family.LANDSAT_LST, {"pixelCount": 10, "lst.min": 1.0}),
        (Family.CLMS_LST, {"pixelCount": 10, "validPixelFraction": 0.9}),
    ],
)
def test_family_without_primary_value_returns_none(family, analytics, caplog):
    with caplog.at_level(logging.WARNING):
        assert build(family=family, analytics=analytics) is None
    assert family.value in caplog.text


def test_numpy_scalars_accepted_and_numpy_nan_omitted():
    e = build(analytics={"ndvi": np.float32(0.5), "evi": np.float64(0.25), "savi": np.float64("nan"),
                         "pixelCount": np.int64(7)})
    assert analytics_of(e) == {"ndvi": "0.500000", "evi": "0.250000", "pixelCount": "7"}


def test_entity_id_guards():
    with pytest.raises(ValueError):
        entity_id("a:b", "parcel", PARCEL, Family.S2_INDICES, DAY)
    with pytest.raises(ValueError):
        entity_id("", "parcel", PARCEL, Family.S2_INDICES, DAY)
    with pytest.raises(ValueError):
        entity_id("tenant-a", "parcel", PARCEL, Family.S2_INDICES, datetime(2026, 11, 12, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        entity_id("tenant-a", "parcel", "", Family.S2_INDICES, DAY)
    with pytest.raises(ValueError):
        build(target=Target("parcel", "", None, None, GEOM))
    with pytest.raises(ValueError):
        build(target=Target("zone", PARCEL, "", ZONE_REL, GEOM))


ZONE_REL = "hasAgriManagementZone"


def test_empty_scene_strings_omitted_and_orbit_must_be_integer():
    s = SceneMeta("", "", "", "S2MSI2A", "", None, "", None)
    e = build(scene=s)
    for k in ("productID", "productURL", "productFormat", "platformName", "operationalMode"):
        assert k not in e
    assert e["productType"]["value"] == "S2MSI2A"
    for bad in (37.5, "37", True):
        with pytest.raises(ValueError):
            build(scene=SceneMeta("i", "u", "SAFE", "S2MSI2A", "S2A", bad, None, None))


@pytest.mark.parametrize("layer", ["pixelCount", "ndvi.min", "validPixelFraction"])
def test_raster_layers_must_be_raster_names(layer):
    with pytest.raises(ValueError):
        build(analytics={"ndvi": 0.5, "evi": 0.4, "pixelCount": 3, "ndvi.min": 0.1, "validPixelFraction": 0.9},
              raster=RasterMeta("/r", [("ndvi", "x"), (layer, "y")]))


def test_clms_lst_default_product_type_and_no_platform_attributes():
    lst = {"lst": 21.4, "validPixelFraction": 0.9}
    e = build(family=Family.CLMS_LST, scene=None, sensing=Sensing(DAY, None), analytics=lst)
    assert e["productType"]["value"] == "land_surface_temperature"
    carried = SceneMeta("id", "https://catalog.example.org/x", "GeoTIFF", "land_surface_temperature",
                        "CLMS", 5, "IW", GEOM)
    e = build(family=Family.CLMS_LST, scene=carried, sensing=Sensing(DAY, None), analytics=lst)
    for k in ("platformName", "orbitNumber", "operationalMode"):
        assert k not in e
    assert e["productID"]["value"] == "id"
    assert build(family=Family.LANDSAT_LST, scene=None, analytics=lst)["productType"]["value"] == "L2SP"
    assert build(family=Family.S1_BACKSCATTER, scene=None, analytics={"backscatterVV": -9.0, "backscatterVH": -15.0})[
        "productType"]["value"] == "GRD"


def test_sdk_parses_full_target_ids(caplog):
    z1 = "urn:ngsi-ld:AgriManagementZone:p-uuid:0edc1602:L1"
    z2 = "urn:ngsi-ld:AgriManagementZone:p-other:11aa22bb:L1"
    parcel_a, parcel_b = "urn:ngsi-ld:AgriParcel:p-uuid", "urn:ngsi-ld:AgriParcel:p-other"
    an = {"ndvi": 0.5, "evi": 0.4}
    ents = [
        build(target=Target("zone", parcel_a, z1, ZONE_REL, GEOM), analytics=an),
        build(target=Target("zone", parcel_b, z2, ZONE_REL, GEOM), analytics=an, sensing=Sensing(date(2026, 11, 14), None)),
        build(analytics=an),
    ]
    assert len({e["id"] for e in ents}) == 3
    with caplog.at_level(logging.WARNING, logger="nkz_platform_sdk.eo"):
        obs = parse_eo_entities(ents)
    assert [r for r in caplog.records if r.name.startswith("nkz_platform_sdk.eo")] == []
    assert len(obs) == 3
    by_id = {o.entity_id: o for o in obs}
    o1, o2, o3 = (by_id[e["id"]] for e in ents)
    assert (o1.target, o1.zone_id, o1.parcel_id, o1.sensing_day) == ("zone", z1, parcel_a, DAY)
    assert (o2.target, o2.zone_id, o2.parcel_id, o2.sensing_day) == ("zone", z2, parcel_b, date(2026, 11, 14))
    assert (o3.target, o3.zone_id, o3.parcel_id, o3.sensing_day) == ("parcel", None, PARCEL, DAY)
    assert {o.family for o in obs} == {"s2-indices"}
