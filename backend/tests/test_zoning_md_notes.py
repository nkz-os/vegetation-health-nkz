"""A technician's note follows the place where it was written, not the zone id."""
from datetime import datetime, timezone
from types import SimpleNamespace

# Two halves of a small square near (-1.65, 42.80): west and east.
WEST = {"type": "Polygon", "coordinates": [[[-1.6510, 42.8000], [-1.6495, 42.8000], [-1.6495, 42.8020],
                                            [-1.6510, 42.8020], [-1.6510, 42.8000]]]}
EAST = {"type": "Polygon", "coordinates": [[[-1.6495, 42.8000], [-1.6480, 42.8000], [-1.6480, 42.8020],
                                            [-1.6495, 42.8020], [-1.6495, 42.8000]]]}


def _zone(eid, geom):
    return {"id": eid, "type": "AgriManagementZone", "location": {"type": "GeoProperty", "value": geom}}


def _note(geom, cause, minute):
    return SimpleNamespace(geometry=geom, cause=cause,
                           created_at=datetime(2026, 11, 16, 10, minute, tzinfo=timezone.utc))


def _shifted(geom, dlon):
    ring = [[x + dlon, y] for x, y in geom["coordinates"][0]]
    return {"type": "Polygon", "coordinates": [ring]}


def test_notes_follow_location_by_overlap():
    from app.jobs.zoning_multidate.notes import assign_notes
    # The note was written on a zone a bit to the west of today's east zone: it overlaps it most.
    note = _note(_shifted(EAST, -0.0003), "waterlogging", 0)
    got = assign_notes([note], [_zone("z:L1", WEST), _zone("z:L3", EAST)])
    assert got == {"z:L3": "waterlogging"}


def test_note_below_30pct_overlap_is_not_assigned():
    from app.jobs.zoning_multidate.notes import assign_notes
    # Shifted 80 % of the width east: only 20 % of the note stays on EAST, none on WEST.
    note = _note(_shifted(EAST, 0.0012), "stones", 0)
    assert assign_notes([note], [_zone("z:L1", WEST), _zone("z:L3", EAST)]) == {}


def test_latest_note_wins():
    from app.jobs.zoning_multidate.notes import assign_notes
    older, newer = _note(EAST, "old cause", 0), _note(EAST, "new cause", 5)
    zones = [_zone("z:L1", WEST), _zone("z:L3", EAST)]
    assert assign_notes([newer, older], zones) == {"z:L3": "new cause"}


def test_with_causes_sets_the_attribute_only_on_matched_zones():
    from app.jobs.zoning_multidate.notes import with_causes
    zones = [_zone("z:L1", WEST), _zone("z:L3", EAST)]
    out = with_causes(zones, {"z:L3": "waterlogging"})
    assert out[1]["technicianCause"] == {"type": "Property", "value": "waterlogging"}
    assert "technicianCause" not in out[0]
    assert "technicianCause" not in zones[1]  # inputs untouched


def test_notes_on_zone_picks_overlapping_notes():
    from app.jobs.zoning_multidate.notes import assign_notes, notes_on_zone
    old = _note(_shifted(WEST, -0.0002), "old run cause", 0)  # written on an older, shifted zone
    new = _note(WEST, "current cause", 1)
    east = _note(EAST, "stones", 2)
    zones = [_zone("z:L1", WEST), _zone("z:L3", EAST)]
    assert notes_on_zone([old, new, east], zones, "z:L1") == [old, new]
    assert notes_on_zone([old, new, east], zones, "z:L3") == [east]
    assert assign_notes([old, new, east], zones) == {"z:L1": "current cause", "z:L3": "stones"}
