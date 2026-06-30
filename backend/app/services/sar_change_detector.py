"""SAR Change Detector — classify agronomic operations from backscatter deltas.

Compares consecutive Sentinel-1 VV backscatter values for the same parcel
and classifies significant changes as tillage, harvest, or sowing using
heuristic thresholds on ΔVV and NDVI stability.

Future: upgrade to ML classifier once user confirmations accumulate.
"""

from __future__ import annotations


# Detection thresholds (dB)
MIN_DELTA_DB = 3.0       # |ΔVV| below this is noise
HARVEST_DELTA_DB = 5.0   # VV drop magnitude for harvest detection
SOWING_DELTA_DB = 3.0    # VV increase for sowing detection
MAX_LOOKBACK_DAYS = 20    # discard comparison if previous scene is older

# NDVI stability thresholds
NDVI_CHANGE_THRESHOLD = 0.15  # larger NDVI change = vegetation, not mechanical


def detect_change(
    vv_current_db: float,
    vv_previous_db: float | None,
    ndvi_current: float | None = None,
    ndvi_previous: float | None = None,
    days_since_previous: int | None = None,
) -> dict | None:
    """Classify agronomic operation from Sentinel-1 VV backscatter change.

    Args:
        vv_current_db: Current scene VV mean backscatter (dB).
        vv_previous_db: Previous scene VV mean, or None if first acquisition.
        ndvi_current: Latest NDVI value (0–1), optional.
        ndvi_previous: NDVI from previous scene date, optional.
        days_since_previous: Days between current and previous SAR acquisition.

    Returns:
        Dict with ``change_flag``, ``confidence``, ``delta_vv``, ``reason``,
        or None if no previous scene or outside lookback window.
    """
    if vv_previous_db is None:
        return None

    if days_since_previous is not None and days_since_previous > MAX_LOOKBACK_DAYS:
        return None

    delta_vv = round(vv_current_db - vv_previous_db, 2)

    if abs(delta_vv) < MIN_DELTA_DB:
        return {
            "change_flag": "none",
            "confidence": 0.0,
            "delta_vv": delta_vv,
            "reason": f"|ΔVV| < {MIN_DELTA_DB} dB",
        }

    # ── NDVI stability check ───────────────────────────────────────────
    delta_ndvi: float | None = None
    ndvi_declining = False
    if ndvi_current is not None and ndvi_previous is not None:
        delta_ndvi = round(abs(ndvi_current - ndvi_previous), 4)
        ndvi_declining = ndvi_current < ndvi_previous - 0.10

    # ── Classify ──────────────────────────────────────────────────────────

    # Harvest: strong VV drop + NDVI declining (crop removed, expected)
    if delta_vv < -HARVEST_DELTA_DB and ndvi_declining:
        return {
            "change_flag": "harvest",
            "confidence": 0.85,
            "delta_vv": delta_vv,
            "reason": f"VV drop {delta_vv} dB + NDVI decline",
        }

    # Vegetation change (not mechanical): NDVI changed but VV is stable or
    # the NDVI change dominates the signal.
    if delta_ndvi is not None and delta_ndvi > NDVI_CHANGE_THRESHOLD:
        return {
            "change_flag": "none",
            "confidence": 0.0,
            "delta_vv": delta_vv,
            "reason": f"vegetation change (|ΔNDVI| = {delta_ndvi})",
        }

    # Sowing: VV increase + NDVI stable (bare soil → rough surface)
    # Requires NDVI data — without it, default to tillage.
    if delta_vv > SOWING_DELTA_DB and delta_ndvi is not None and delta_ndvi < 0.10:
        return {
            "change_flag": "sowing",
            "confidence": 0.80,
            "delta_vv": delta_vv,
            "reason": f"VV increase {delta_vv} dB, NDVI stable",
        }

    # Default significant change → tillage
    return {
        "change_flag": "tillage",
        "confidence": 0.75,
        "delta_vv": delta_vv,
        "reason": f"|ΔVV| = {abs(delta_vv)} dB, NDVI stable",
    }
