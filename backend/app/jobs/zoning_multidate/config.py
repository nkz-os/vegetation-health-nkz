"""Thresholds of the multi-date zoning proposal (overridable by environment)."""
from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    return float(os.getenv(f"ZONING_{name}", default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(f"ZONING_{name}", default))


MIN_DATES = _i("MIN_DATES", 3)
MIN_VALID_FRACTION = _f("MIN_VALID_FRACTION", 0.7)
# Parcel median below which a date shows no crop (bare soil, stubble).
VEGETATED_FLOOR: dict[str, float] = {
    "NDVI": _f("FLOOR_NDVI", 0.35),  # owner, 2026-11-16: keeps freshly sown / ripening dates out
    "GNDVI": _f("FLOOR_GNDVI", 0.30),
    "SAVI": _f("FLOOR_SAVI", 0.20),
    "OSAVI": _f("FLOOR_OSAVI", 0.20),
    "EVI": _f("FLOOR_EVI", 0.20),
    "NDRE": _f("FLOOR_NDRE", 0.15),
}
# p90 - p10 of the per-pixel relative vigour below which the parcel is homogeneous.
HOMOGENEOUS_SPREAD = _f("HOMOGENEOUS_SPREAD", 0.10)
HIGH_MIN = _f("HIGH_MIN", 1.10)
LOW_MAX = _f("LOW_MAX", 0.90)
CRITICAL_MAX = _f("CRITICAL_MAX", 0.75)
CRITICAL_PERSISTENCE = _f("CRITICAL_PERSISTENCE", 0.8)
MIN_ZONE_HA = _f("MIN_ZONE_HA", 0.25)
# A pixel needs a value on at least this share of the dates to get a level.
MIN_PIXEL_DATE_SHARE = _f("MIN_PIXEL_DATE_SHARE", 0.5)
YEARS_BACK = _i("YEARS_BACK", 2)
RES_M = _f("RES_M", 10.0)
# Above this share of unavailable rasters the proposal is not trusted (fail-safe).
MAX_FAILED_SHARE = _f("MAX_FAILED_SHARE", 0.2)

ZONABLE_INDICES = frozenset(VEGETATED_FLOOR)
