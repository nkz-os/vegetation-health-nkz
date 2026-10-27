"""Per-zone EOProduct id scheme."""
from app.services.zone_products import zone_eo_product_id


def test_zone_optical_id():
    assert zone_eo_product_id("acme", "p1", "z1", "2026-06-01") == \
        "urn:ngsi-ld:EOProduct:acme:zone:p1:z1:2026-06-01"


def test_zone_grd_id():
    assert zone_eo_product_id("acme", "p1", "z1", "2026-06-01", grd=True) == \
        "urn:ngsi-ld:EOProduct:acme:zone:p1:z1:GRD:2026-06-01"


def test_zone_id_accepts_date_object():
    from datetime import date
    assert zone_eo_product_id("acme", "p1", "z1", date(2026, 6, 1)).endswith(":z1:2026-06-01")


def test_zone_id_never_matches_parcel_prefix():
    parcel_prefix = "urn:ngsi-ld:EOProduct:acme:p1:"
    assert not zone_eo_product_id("acme", "p1", "z1", "2026-06-01").startswith(parcel_prefix)
    assert not zone_eo_product_id("acme", "p1", "z1", "2026-06-01", grd=True).startswith(parcel_prefix)
