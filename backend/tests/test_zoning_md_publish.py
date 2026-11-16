"""Multi-date zoning: AgriManagementZone contract and retirement of old automatic zonings."""
import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest
from shapely.geometry import box

from app.jobs.zoning_multidate.polygons import ZoneSummary
from app.jobs.zoning_multidate.publish import build_entities, retire_automatic_zonings

PARCEL = "urn:ngsi-ld:AgriParcel:acme-p1"
RUN = "0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)


def _zones():
    # UTM 30N squares near 42.8 N.
    return [
        ZoneSummary(1, "high", box(597000, 4739000, 597100, 4739100), 1.0, 0.71234, 14, 18, 22),
        ZoneSummary(3, "low", box(597100, 4739000, 597150, 4739100), 0.5, 0.55, -14, 22, 22),
    ]


def _attr(e, k):
    return e[k]["value"] if e[k]["type"] == "Property" else e[k]


def test_entities_match_contract():
    ents = build_entities(PARCEL, RUN, "NDVI", [date(2025, 5, 9), date(2026, 6, 13)], _zones(),
                          "EPSG:32630", NOW)
    assert [e["id"] for e in ents] == [
        "urn:ngsi-ld:AgriManagementZone:acme-p1:01234567:L1",
        "urn:ngsi-ld:AgriManagementZone:acme-p1:01234567:L3",
    ]
    e = ents[0]
    assert e["type"] == "AgriManagementZone"
    assert e["hasAgriParcel"] == {"type": "Relationship", "object": PARCEL}
    assert e["location"]["type"] == "GeoProperty" and e["location"]["value"]["type"] == "Polygon"
    lon, lat = e["location"]["value"]["coordinates"][0][0]
    assert -4 < lon < 0 and 42 < lat < 43
    assert _attr(e, "zoningMethod") == "vegetationIndexMultiDate"
    assert _attr(e, "zoningRunId") == RUN
    assert _attr(e, "zoneId") == _attr(e, "zoneRank") == 1
    assert _attr(e, "zoneName") == _attr(e, "zoneLevel") == "high"
    assert _attr(e, "relativeVigorPct") == 14
    assert (_attr(e, "confirmingDates"), _attr(e, "totalDates")) == (18, 22)
    assert _attr(e, "sourceDates") == ["2025-05-09", "2026-06-13"]
    assert _attr(e, "meanValue") == 0.7123
    assert _attr(e, "areaHa") == 1.0
    assert _attr(e, "variableAttribute") == "NDVI"
    assert e["dateZoned"]["value"] == {"@type": "DateTime", "@value": "2026-11-16T09:00:00Z"}


def test_entities_carry_no_spanish_text():
    blob = json.dumps(build_entities(PARCEL, RUN, "NDVI", [date(2026, 6, 13)], _zones(), "EPSG:32630", NOW))
    for word in ("Alto", "Bajo", "Medio", "Crítico", "Zona"):
        assert word not in blob


def _fiware(pages):
    fw = MagicMock()
    responses = []
    for page in pages:
        r = MagicMock()
        r.json.return_value = page
        r.raise_for_status.return_value = None
        responses.append(r)
    fw.session.get.side_effect = responses
    fw.context_broker_url = "http://broker"
    fw.delete_entity.return_value = True
    return fw


def test_no_zones_retires_automatic_keeps_manual():
    fw = _fiware([[
        {"id": "z-old", "zoningMethod": "vegetationIndex", "zoningRunId": "r0", "hasAgriParcel": PARCEL},
        {"id": "z-iri", "https://nkz-os.org/ns/zoningMethod": "vegetationIndexMultiDate", "zoningRunId": "r1",
         "hasAgriParcel": PARCEL},
        {"id": "z-manual", "zoningMethod": "manual", "zoningRunId": "r2", "hasAgriParcel": PARCEL},
        {"id": "z-legacy", "hasAgriParcel": PARCEL},
    ]])
    assert retire_automatic_zonings(fw, PARCEL, keep_run_id=None) == 2
    deleted = sorted(c.args[0] for c in fw.delete_entity.call_args_list)
    assert deleted == ["z-iri", "z-old"]
    params = fw.session.get.call_args.kwargs["params"]
    assert params["type"] == "AgriManagementZone" and PARCEL in params["q"]


def test_zones_published_keep_their_own_run():
    fw = _fiware([[
        {"id": "z-new", "zoningMethod": "vegetationIndexMultiDate", "zoningRunId": RUN, "hasAgriParcel": PARCEL},
        {"id": "z-old", "zoningMethod": "vegetationIndex", "zoningRunId": "r0", "hasAgriParcel": PARCEL},
    ]])
    assert retire_automatic_zonings(fw, PARCEL, keep_run_id=RUN) == 1
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == ["z-old"]


def test_retire_follows_pagination():
    page1 = [{"id": f"z{i}", "zoningMethod": "vegetationIndex", "zoningRunId": "r", "hasAgriParcel": PARCEL}
             for i in range(100)]
    fw = _fiware([page1, [{"id": "z100", "zoningMethod": "vegetationIndex", "zoningRunId": "r",
                           "hasAgriParcel": PARCEL}]])
    assert retire_automatic_zonings(fw, PARCEL, keep_run_id=None) == 101


def test_publish_rolls_back_on_failure():
    from app.jobs.zoning_multidate.publish import publish
    fw = MagicMock()
    fw.create_entity.side_effect = [True, False]
    fw.delete_entity.return_value = True
    ents = build_entities(PARCEL, RUN, "NDVI", [date(2026, 6, 13)], _zones(), "EPSG:32630", NOW)
    with pytest.raises(RuntimeError):
        publish(fw, ents)
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == [ents[0]["id"]]


def test_retire_skips_unknown_methods_other_parcels_and_the_new_run():
    fw = _fiware([[
        {"id": "z-unknown-method", "zoningRunId": "r9", "hasAgriParcel": PARCEL},
        {"id": "z-other-parcel", "zoningMethod": "vegetationIndex", "zoningRunId": "r1",
         "hasAgriParcel": "urn:ngsi-ld:AgriParcel:acme-p2"},
        {"id": "urn:ngsi-ld:AgriManagementZone:acme-p1:01234567:L2", "zoningMethod": "vegetationIndexMultiDate",
         "hasAgriParcel": PARCEL},
        {"id": "z-old", "zoningMethod": "vegetationIndex", "zoningRunId": "r0",
         "https://nkz-os.org/ns/hasAgriParcel": {"object": PARCEL}},
    ]])
    assert retire_automatic_zonings(fw, PARCEL, keep_run_id=RUN) == 1
    assert [c.args[0] for c in fw.delete_entity.call_args_list] == ["z-old"]
    assert "hasAgriParcel" in fw.session.get.call_args.kwargs["params"]["attrs"]
