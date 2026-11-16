"""The parcel UI learns the outcome of a multi-date zoning job."""
from datetime import datetime
from types import SimpleNamespace


def _job(result, parameters):
    return SimpleNamespace(
        id="j1", job_type="calculate_index", status="completed", parameters=parameters, result=result,
        error_message=None, created_at=datetime(2026, 11, 16), completed_at=datetime(2026, 11, 16),
        created_by="u1", crop_season_id=None,
    )


def test_card_reports_no_zones_reason_and_chosen_index():
    from app.api.parcels import _job_card
    card = _job_card(_job(
        {"index_type": "VRA_ZONES", "status": "no_zones", "reason": "homogeneous", "zones_created": 0,
         "zoning_source": "OSAVI"},
        {"index_type": "VRA_ZONES", "source_index": None},
    ))
    assert card["is_vra"] is True
    assert card["zoning_source"] == "OSAVI"
    assert (card["zoning_status"], card["zoning_reason"]) == ("no_zones", "homogeneous")
    assert card["zones_created"] == 0


def test_card_for_non_zoning_job_has_no_zoning_fields():
    from app.api.parcels import _job_card
    card = _job_card(_job({"index_type": "NDVI"}, {"index_type": "NDVI"}))
    assert card["zoning_status"] is None and card["zoning_reason"] is None
