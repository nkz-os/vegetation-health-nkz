"""
Temporal grouping utilities for vegetation index composites.

Groups scenes into dekadal (10-day) windows — the standard approach in
remote sensing for creating cloud-free temporal composites.
"""

from datetime import date, timedelta
from typing import List, Dict, Any


def dekad_key(d: date) -> tuple:
    """Return (year, month, dekad) for a given date.

    Dekads: 1-10 → 0, 11-20 → 1, 21-end → 2.
    """
    return (d.year, d.month, (d.day - 1) // 10)


def dekad_range(d: date) -> tuple:
    """Return (start, end) dates for the dekad containing *d*."""
    dek = (d.day - 1) // 10
    start_day = dek * 10 + 1
    if dek < 2:
        end_day = start_day + 9
    else:
        # Last dekad extends to end of month
        import calendar
        end_day = calendar.monthrange(d.year, d.month)[1]
    return (
        date(d.year, d.month, start_day),
        date(d.year, d.month, end_day),
    )


def group_scenes_into_windows(
    scenes: List[Dict[str, Any]],
    date_key: str = "sensing_date",
) -> List[Dict[str, Any]]:
    """Group a list of scene dicts into dekadal (10-day) windows.

    Each scene dict must have a *date_key* field that is either a
    ``date`` object or an ISO-format string.

    Returns a list sorted by window start date::

        [
            {
                "window_start": date(2025, 1, 1),
                "window_end":   date(2025, 1, 10),
                "scenes": [scene_dict, ...],
            },
            ...
        ]
    """
    from collections import defaultdict

    buckets: Dict[tuple, List[Dict]] = defaultdict(list)

    for scene in scenes:
        raw = scene[date_key]
        if isinstance(raw, str):
            d = date.fromisoformat(raw)
        else:
            d = raw
        key = dekad_key(d)
        buckets[key].append(scene)

    windows = []
    for key in sorted(buckets.keys()):
        group = buckets[key]
        sample = group[0][date_key]
        if isinstance(sample, str):
            sample = date.fromisoformat(sample)
        start, end = dekad_range(sample)
        windows.append({
            "window_start": start,
            "window_end": end,
            "scenes": sorted(
                group,
                key=lambda s: s[date_key] if isinstance(s[date_key], str) else s[date_key].isoformat(),
            ),
        })

    return windows
