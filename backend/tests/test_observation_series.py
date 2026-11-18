"""The per-parcel observation series: EOProduct entities -> points with a quality mark."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.services import observation_series as obs

TENANT = "acme"
PARCEL_UUID = "0123456789abcdef"
PARCEL = f"urn:ngsi-ld:AgriParcel:{PARCEL_UUID}"
OTHER = "urn:ngsi-ld:AgriParcel:0123456789zzzzzz"  # same 10-character prefix


def product(day, parcel=PARCEL, index="ndvi", fraction=0.9, mask="core", median=0.5, prefix="", rel="hasAgriParcel",
            cloud=None, pixels=120, parcel_short=None):
    short = parcel_short or parcel.split(":")[-1][:10]
    sub = {}
    if fraction is not None:
        sub[prefix + "interiorValidFraction"] = {"type": "Property", "value": fraction}
        sub[prefix + "interiorPixelCount"] = {"type": "Property", "value": pixels}
        sub[prefix + "interiorMask"] = {"type": "Property", "value": mask}
        if median is not None:
            sub[prefix + "interiorMedian"] = {"type": "Property", "value": median}
            sub[prefix + "interiorMean"] = {"type": "Property", "value": median + 0.01}
            sub[prefix + "interiorP10"] = {"type": "Property", "value": median - 0.1}
            sub[prefix + "interiorP90"] = {"type": "Property", "value": median + 0.1}
    entity = {
        "id": f"urn:ngsi-ld:EOProduct:{TENANT}:{short}:{day}",
        "type": "EOProduct",
        rel: {"type": "Relationship", "object": parcel},
        "sensingDate": {"type": "Property", "value": day},
        prefix + index: {"type": "Property", "value": 0.4, **sub},
    }
    if cloud is not None:
        entity["parcelCloudFraction"] = {"type": "Property", "value": cloud}
    return entity


def test_point_full_interior():
    p = obs.point_from_product(product("2026-05-01", cloud=0.05), "NDVI")
    assert p == {
        "date": "2026-05-01", "median": 0.5, "mean": 0.51, "p10": 0.4, "p90": 0.6,
        "valid_fraction": 0.9, "pixel_count": 120, "cloud_fraction": 0.05, "mask": "core", "quality": "ok",
    }


def test_point_is_none_without_the_index():
    assert obs.point_from_product(product("2026-05-01", index="lst"), "NDVI") is None


@pytest.mark.parametrize("fraction,mask,expected", [
    (0.9, "core", "ok"),
    (0.7, "core", "ok"),
    (0.5, "core", "partly_covered"),
    (0.95, "parcel", "too_few_pixels"),
    (None, None, "unmeasured"),
])
def test_quality_rules(fraction, mask, expected):
    p = obs.point_from_product(product("2026-05-01", fraction=fraction, mask=mask), "NDVI")
    assert p["quality"] == expected
    assert obs.quality_of(p) == expected


def test_unmeasured_has_null_interior_fields():
    p = obs.point_from_product(product("2026-05-01", fraction=None, cloud=0.1), "NDVI")
    assert p["quality"] == "unmeasured"
    for key in ("median", "mean", "p10", "p90", "valid_fraction", "pixel_count", "mask"):
        assert p[key] is None
    assert p["date"] == "2026-05-01" and p["cloud_fraction"] == 0.1


def test_zero_valid_keeps_date_without_values():
    p = obs.point_from_product(product("2026-05-01", fraction=0.0, median=None, pixels=0), "NDVI")
    assert p["valid_fraction"] == 0.0 and p["median"] is None and p["mean"] is None
    assert p["quality"] == "partly_covered"


@pytest.mark.parametrize("prefix", ["https://nkz-os.org/ns/", "https://uri.etsi.org/ngsi-ld/default-context/"])
def test_iri_attribute_names(prefix):
    e = product("2026-05-01", prefix=prefix)
    e["sensingDate"] = {"type": "Property", "value": "2026-05-01"}
    p = obs.point_from_product(e, "NDVI")
    assert p["median"] == 0.5 and p["quality"] == "ok"


def test_prefix_collision_filtered():
    mine = product("2026-05-01")
    theirs = product("2026-05-02", parcel=OTHER)  # same id prefix, other parcel
    assert [p["date"] for p in obs.build_series([mine, theirs], PARCEL, "NDVI", None, None)] == ["2026-05-01"]


def test_legacy_relationship_name_is_accepted():
    e = product("2026-05-01", rel="refAgriParcel")
    assert len(obs.build_series([e], PARCEL, "NDVI", None, None)) == 1


def test_product_without_the_index_is_absent():
    sar_only = product("2026-05-03", index="lst")
    assert obs.build_series([product("2026-05-01"), sar_only], PARCEL, "NDVI", None, None)[0]["date"] == "2026-05-01"
    assert len(obs.build_series([product("2026-05-01"), sar_only], PARCEL, "NDVI", None, None)) == 1


def test_one_point_per_date_best_fraction():
    a = product("2026-05-01", fraction=0.4, median=0.1)
    b = product("2026-05-01", fraction=0.8, median=0.7)
    c = product("2026-05-01", fraction=None)
    points = obs.build_series([a, c, b], PARCEL, "NDVI", None, None)
    assert len(points) == 1 and points[0]["median"] == 0.7


def test_unmeasured_loses_against_any_fraction():
    a = product("2026-05-01", fraction=None)
    b = product("2026-05-01", fraction=0.0, median=None)
    (p,) = obs.build_series([a, b], PARCEL, "NDVI", None, None)
    assert p["valid_fraction"] == 0.0


def test_dates_filter_and_order():
    entities = [product(d) for d in ("2026-05-10", "2026-05-01", "2026-06-01", "2026-04-01")]
    pts = obs.build_series(entities, PARCEL, "NDVI", date(2026, 5, 1), date(2026, 5, 31))
    assert [p["date"] for p in pts] == ["2026-05-01", "2026-05-10"]


def test_woody_adds_canopy_basis():
    with patch.object(obs, "fetch_parcel_products", return_value=[product("2026-05-01")]):
        woody = obs.observation_series(TENANT, PARCEL, "NDVI", None, None, woody=True)
        plain = obs.observation_series(TENANT, PARCEL, "ndvi", None, None, woody=False)
    assert woody["canopy_basis"] == "parcel_interior"
    assert "canopy_basis" not in plain
    assert plain == {
        "entity_id": PARCEL, "index": "NDVI",
        "rules": {"min_valid_fraction": 0.7, "mask": "interior"},
        "points": plain["points"],
    }
    assert len(plain["points"]) == 1


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.body


def _client(pages):
    """SDK client double: real header/url helpers shape, the session answers pages in order."""
    client = MagicMock()
    client._url.side_effect = lambda path: "http://orion" + path
    client._headers.return_value = {"NGSILD-Tenant": TENANT, "Fiware-Service": TENANT, "Link": "<ctx>"}
    client.timeout = 5
    client._session.get.side_effect = [p if isinstance(p, Exception) else FakeResponse(p) for p in pages]
    return client


def test_fetch_uses_id_pattern_with_sdk_headers_and_keeps_only_the_parcel_prefix():
    mine = product("2026-05-01")
    grd = dict(mine, id=f"urn:ngsi-ld:EOProduct:{TENANT}:{PARCEL_UUID[:10]}:GRD:2026-05-01")
    other_tenant = dict(mine, id=f"urn:ngsi-ld:EOProduct:tenant-a:{PARCEL_UUID[:10]}:2026-05-02")
    client = _client([[mine] * 1000, [grd, other_tenant, product("2026-05-03")], []])
    with patch.object(obs, "SyncOrionClient", return_value=client) as ctor:
        out = obs.fetch_parcel_products(TENANT, PARCEL)
    ctor.assert_called_once_with(TENANT)
    assert len(out) == 1001
    calls = client._session.get.call_args_list
    assert len(calls) == 3
    first = calls[0]
    assert first.args[0] == "http://orion/ngsi-ld/v1/entities"
    assert first.kwargs["headers"]["NGSILD-Tenant"] == TENANT and "Link" in first.kwargs["headers"]
    params = first.kwargs["params"]
    assert params["type"] == "EOProduct" and params["limit"] == 1000 and params["offset"] == 0
    assert params["idPattern"] == r"^urn:ngsi-ld:EOProduct:acme:0123456789:"
    assert "attrs" not in params
    assert calls[1].kwargs["params"]["offset"] == 1000
    assert calls[2].kwargs["params"]["offset"] == 1000 + 3


def test_paging_advances_by_what_was_returned_and_stops_only_on_empty():
    client = _client([[product("2026-05-01")] * 100, [product("2026-05-02")] * 7, []])
    with patch.object(obs, "SyncOrionClient", return_value=client):
        out = obs.fetch_parcel_products(TENANT, PARCEL)
    assert len(out) == 107
    assert [c.kwargs["params"]["offset"] for c in client._session.get.call_args_list] == [0, 100, 107]


def test_page_cap_raises_instead_of_a_partial_series():
    client = _client([[product("2026-05-01")]] * 60)
    with patch.object(obs, "SyncOrionClient", return_value=client):
        with pytest.raises(obs.BrokerUnavailable, match="series truncated"):
            obs.fetch_parcel_products(TENANT, PARCEL)
    assert client._session.get.call_count == 50


def test_fetch_raises_broker_unavailable_on_error():
    for failure in (RuntimeError("connection refused"), FakeResponse([], status=503)):
        client = _client([failure])
        with patch.object(obs, "SyncOrionClient", return_value=client):
            with pytest.raises(obs.BrokerUnavailable):
                obs.fetch_parcel_products(TENANT, PARCEL)


def test_relationship_key_as_full_iri_is_matched():
    e = product("2026-05-01", rel="https://nkz-os.org/ns/hasAgriParcel")
    assert len(obs.build_series([e], PARCEL, "NDVI", None, None)) == 1


def test_invalid_sensing_date_skips_the_product(caplog):
    bad = product("2026-13-45")
    good = product("2026-05-01")
    with caplog.at_level("WARNING"):
        pts = obs.build_series([bad, good], PARCEL, "NDVI", None, None)
    assert [p["date"] for p in pts] == ["2026-05-01"]
    assert any("2026-13-45" in r.getMessage() for r in caplog.records)


def test_non_numeric_interior_value_becomes_null(caplog):
    e = product("2026-05-01")
    e["ndvi"]["interiorMedian"] = {"type": "Property", "value": "n/a"}
    e["ndvi"]["interiorPixelCount"] = {"type": "Property", "value": "many"}
    with caplog.at_level("WARNING"):
        (p,) = obs.build_series([e], PARCEL, "NDVI", None, None)
    assert p["median"] is None and p["pixel_count"] is None and p["valid_fraction"] == 0.9
    assert caplog.records
