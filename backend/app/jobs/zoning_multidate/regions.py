"""Spatial regularisation: from a per-pixel level map to contiguous, workable zones.

Deterministic: same input, same output (no random seeds, fixed tie rules).
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

RANKS = (1, 2, 3, 4)
_EIGHT = np.ones((3, 3), dtype=bool)
_MAX_ROUNDS = 50


def _fill_gaps(levels: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """Inside pixels without a level take the level of the nearest one that has it."""
    known = inside & (levels > 0)
    if not known.any():
        return levels
    _, (ri, ci) = ndimage.distance_transform_edt(~known, return_indices=True)
    out = levels.copy()
    gaps = inside & ~known
    out[gaps] = levels[ri[gaps], ci[gaps]]
    return out


def _mode_pass(levels: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """3x3 majority over inside pixels; a tie keeps the centre's level."""
    counts = np.stack([
        ndimage.convolve((inside & (levels == r)).astype(np.int16), _EIGHT.astype(np.int16),
                         mode="constant", cval=0)
        for r in RANKS
    ])
    best = counts.max(axis=0)
    winner = np.array(RANKS, dtype="int8")[counts.argmax(axis=0)]  # lowest rank on ties
    centre_count = np.zeros_like(best)
    for i, r in enumerate(RANKS):
        centre_count = np.where(levels == r, counts[i], centre_count)
    out = np.where(centre_count == best, levels, winner).astype("int8")
    return np.where(inside, out, levels)


def _merge_small(levels: np.ndarray, inside: np.ndarray, min_pixels: int) -> bool:
    """Merge the smallest undersized region into its main neighbour. True if one merged."""
    candidates: list[tuple[int, int, int, np.ndarray]] = []
    for r in RANKS:
        labelled, n = ndimage.label(inside & (levels == r), structure=_EIGHT)
        if n == 0:
            continue
        sizes = ndimage.sum(np.ones_like(labelled), labelled, index=np.arange(1, n + 1))
        for lab, size in enumerate(sizes, start=1):
            if size < min_pixels:
                candidates.append((int(size), r, lab, labelled))
    for size, r, lab, labelled in sorted(candidates, key=lambda c: (c[0], c[1], c[2])):
        region = labelled == lab
        ring = ndimage.binary_dilation(region, structure=_EIGHT) & inside & ~region
        neighbours = levels[ring]
        neighbours = neighbours[(neighbours > 0) & (neighbours != r)]
        if neighbours.size == 0:
            continue
        counts = np.bincount(neighbours, minlength=5)
        target = int(np.argmax(counts))  # lowest rank wins a tie
        levels[region] = target
        return True
    return False


def regularize(levels: np.ndarray, inside: np.ndarray, res_m: float, min_zone_ha: float) -> np.ndarray:
    """Fill gaps, smooth noise, then merge regions smaller than ``min_zone_ha``."""
    out = _fill_gaps(levels.astype("int8"), inside)
    for _ in range(2):
        out = _mode_pass(out, inside)
    min_pixels = max(1, int(np.ceil(min_zone_ha * 10_000.0 / (res_m * res_m))))
    for _ in range(_MAX_ROUNDS * out.size):
        if not _merge_small(out, inside, min_pixels):
            break
    out[~inside] = 0
    return out
