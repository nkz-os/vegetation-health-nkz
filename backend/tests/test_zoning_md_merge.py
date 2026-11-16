"""Merging two levels of a zoning into one (pure function)."""
from datetime import datetime, timezone

import pytest
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from tests.zoning_fakes import PARCEL, zone

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
OLD, NEW = "a" * 32, "b" * 32
_to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32630", always_xy=True).transform


def _strip(x0, x1):
    return {"type": "Polygon", "coordinates": [[[x0, 42.8000], [x1, 42.8000], [x1, 42.8020], [x0, 42.8020],
                                                [x0, 42.8000]]]}


def _ha(geom):
    return transform(_to_utm, shape(geom)).area / 10_000


def _zones():
    """high | medium | low strips, the medium one twice as wide; areas from the geometry."""
    g1, g2, g3 = _strip(-1.6510, -1.6505), _strip(-1.6505, -1.6495), _strip(-1.6495, -1.6490)
    return [
        zone(OLD, 1, "high", g1, area=round(_ha(g1), 2), rel=114, mean=0.70, confirming=18, total=22),
        zone(OLD, 2, "medium", g2, area=round(_ha(g2), 2), rel=99, mean=0.60, confirming=20, total=22),
        {**zone(OLD, 3, "low", g3, area=round(_ha(g3), 2), rel=86, mean=0.50, confirming=15, total=21),
         "technicianCause": {"type": "Property", "value": "stones"}},
    ]


def _by_rank(entities):
    return {e["zoneRank"]["value"]: e for e in entities}


def test_merge_unions_and_reweights():
    from app.jobs.zoning_multidate.merge import merge_levels
    src = _zones()
    out = merge_levels(src, (2, 3), 2, NEW, OLD, NOW)
    ranks = _by_rank(out)
    assert sorted(ranks) == [1, 2]
    merged = ranks[2]
    assert merged["id"] == "urn:ngsi-ld:AgriManagementZone:p1:bbbbbbbb:L2"
    assert merged["zoneLevel"]["value"] == merged["zoneName"]["value"] == "medium"
    assert merged["zoneId"]["value"] == 2
    a2, a3 = src[1]["areaHa"]["value"], src[2]["areaHa"]["value"]
    assert merged["meanValue"]["value"] == pytest.approx((0.60 * a2 + 0.50 * a3) / (a2 + a3), abs=1e-4)
    assert merged["relativeVigorPct"]["value"] == round((99 * a2 + 86 * a3) / (a2 + a3))
    assert (merged["confirmingDates"]["value"], merged["totalDates"]["value"]) == (15, 21)
    assert shape(merged["location"]["value"]).area == pytest.approx(
        shape(src[1]["location"]["value"]).union(shape(src[2]["location"]["value"])).area)
    for e in out:
        assert e["zoningRunId"]["value"] == NEW and e["derivedFromRunId"]["value"] == OLD
        assert e["acceptedAt"]["value"] == {"@type": "DateTime", "@value": "2026-11-16T12:00:00Z"}
        assert e["hasAgriParcel"]["object"] == PARCEL and e["zoningMethod"]["value"] == "vegetationIndexMultiDate"
        assert ":bbbbbbbb:" in e["id"]
    # The untouched level keeps its values; the low zone's cause travels with the merged zone.
    assert ranks[1]["relativeVigorPct"]["value"] == 114 and ranks[1]["areaHa"]["value"] == src[0]["areaHa"]["value"]
    assert merged["technicianCause"]["value"] == "stones"
    assert "technicianCause" not in src[0] and src[1]["id"].endswith(":aaaaaaaa:L2")  # inputs untouched


def test_merge_into_the_lower_level():
    from app.jobs.zoning_multidate.merge import merge_levels
    out = _by_rank(merge_levels(_zones(), (1, 2), 2, NEW, OLD, NOW))
    assert sorted(out) == [2, 3] and out[2]["zoneLevel"]["value"] == "medium"


def test_merge_target_must_be_one_of_ranks():
    from app.jobs.zoning_multidate.merge import merge_levels
    with pytest.raises(ValueError):
        merge_levels(_zones(), (2, 3), 1, NEW, OLD, NOW)
    with pytest.raises(ValueError):
        merge_levels(_zones(), (2, 2), 2, NEW, OLD, NOW)
    with pytest.raises(ValueError):
        merge_levels(_zones(), (2, 4), 2, NEW, OLD, NOW)  # no critical zone in this zoning


def test_merge_keeps_area_sum():
    from app.jobs.zoning_multidate.merge import merge_levels
    src = _zones()
    out = merge_levels(src, (2, 3), 2, NEW, OLD, NOW)
    before = sum(e["areaHa"]["value"] for e in src)
    after = sum(e["areaHa"]["value"] for e in out)
    assert after == pytest.approx(before, abs=0.02)
