"""Useful dates and the relative vigour level of each pixel.

Each date is divided by its own parcel mean, so a pixel's value says how it
compares with the rest of the parcel that day; the level comes from the median
over dates. ``critical`` also needs persistence: being very low on most dates.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from app.jobs.zoning_multidate import config as cfg

RANK_HIGH, RANK_MEDIUM, RANK_LOW, RANK_CRITICAL = 1, 2, 3, 4
_REFERENCE_ITERATIONS = 20


def usable_dates(stack: np.ndarray, inside: np.ndarray, index: str) -> tuple[list[int], dict[str, int]]:
    """Dates with enough clear pixels and a crop on the ground."""
    floor = cfg.VEGETATED_FLOOR.get(index)
    keep: list[int] = []
    dropped = {"cloudy": 0, "bare": 0}
    n_inside = int(inside.sum())
    for i, day in enumerate(stack):
        values = day[inside]
        valid = values[~np.isnan(values)]
        if n_inside == 0 or valid.size / n_inside < cfg.MIN_VALID_FRACTION:
            dropped["cloudy"] += 1
            continue
        if floor is not None and float(np.median(valid)) < floor:
            dropped["bare"] += 1
            continue
        keep.append(i)
    return keep, dropped


def _nanmedian(rel: np.ndarray) -> np.ndarray:
    # All-NaN pixels (outside, or never clear) are expected: silence numpy's warning.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(rel, axis=0).astype("float32")


def _relative(stack: np.ndarray, inside: np.ndarray, expected: np.ndarray | None) -> np.ndarray:
    """Each date divided by its parcel reference (plain mean, or mean corrected by ``expected``)."""
    rel = np.full(stack.shape, np.nan, dtype="float32")
    for i, day in enumerate(stack):
        clear = inside & np.isfinite(day)
        if expected is not None:
            clear &= np.isfinite(expected)
        if not clear.any():
            continue
        ref = float(np.mean(day[clear]))
        if expected is not None:
            ref /= float(np.mean(expected[clear]))
        if np.isfinite(ref) and ref > 0:
            rel[i] = np.where(inside, day / ref, np.nan)
    return rel


@dataclass
class LevelResult:
    levels: np.ndarray      # int8: 0 outside / never valid, 1..4 rank
    median_rel: np.ndarray  # float32, NaN outside
    rel: np.ndarray         # (dates, h, w) relative vigour, NaN where no data
    homogeneous: bool
    spread: float


def classify(stack: np.ndarray, inside: np.ndarray) -> LevelResult:
    """Per-pixel level from the relative vigour of every date."""
    rel = _relative(stack, inside, None)
    # A date's reference is the mean of its clear pixels corrected by how those pixels
    # usually compare with the parcel; iterate to a fixed point. Clouds sitting on one
    # vigour class would otherwise move the reference and shift every other pixel.
    for _ in range(_REFERENCE_ITERATIONS):
        expected = _nanmedian(rel)
        known = inside & np.isfinite(expected)
        if known.any():
            # Relative vigour is defined up to a scale: anchor it so the parcel's mean is 1.
            expected = expected / float(np.mean(expected[known]))
        updated = _relative(stack, inside, expected)
        converged = np.allclose(updated, rel, atol=1e-5, equal_nan=True)
        rel = updated
        if converged:
            break
    median_rel = _nanmedian(rel)
    valid_count = np.sum(~np.isnan(rel), axis=0)
    seen_enough = valid_count >= max(1, int(np.ceil(cfg.MIN_PIXEL_DATE_SHARE * stack.shape[0])))
    critical_share = np.sum(rel < cfg.CRITICAL_MAX, axis=0) / np.maximum(valid_count, 1)

    known = inside & np.isfinite(median_rel) & seen_enough
    values = median_rel[known]
    spread = float(np.percentile(values, 90) - np.percentile(values, 10)) if values.size else 0.0

    levels = np.zeros(stack.shape[1:], dtype="int8")
    levels[known] = RANK_MEDIUM
    levels[known & (median_rel >= cfg.HIGH_MIN)] = RANK_HIGH
    levels[known & (median_rel < cfg.LOW_MAX)] = RANK_LOW
    levels[known & (median_rel < cfg.CRITICAL_MAX) & (critical_share >= cfg.CRITICAL_PERSISTENCE)] = RANK_CRITICAL
    return LevelResult(levels=levels, median_rel=np.where(inside, median_rel, np.nan),
                       rel=rel, homogeneous=spread < cfg.HOMOGENEOUS_SPREAD, spread=spread)
