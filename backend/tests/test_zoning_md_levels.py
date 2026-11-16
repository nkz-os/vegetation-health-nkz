"""Multi-date zoning: useful dates and the relative vigour level of each pixel."""
import numpy as np

from app.jobs.zoning_multidate.levels import classify, usable_dates

H, W = 20, 20
INSIDE = np.ones((H, W), dtype=bool)


def _stack(*dates):
    return np.stack([np.asarray(d, dtype="float32") for d in dates])


def test_cloudy_and_bare_dates_are_dropped():
    good = np.full((H, W), 0.6)
    cloudy = good.copy()
    cloudy[: H // 2, :] = np.nan  # 50 % valid
    bare = np.full((H, W), 0.15)
    keep, dropped = usable_dates(_stack(good, cloudy, bare, good), INSIDE, "NDVI")
    assert keep == [0, 3]
    assert dropped == {"cloudy": 1, "bare": 1}


def test_uniform_field_is_homogeneous():
    r = classify(_stack(*[np.full((H, W), v) for v in (0.5, 0.6, 0.7)]), INSIDE)
    assert r.homogeneous
    assert r.spread < 0.10


def test_two_halves_give_high_and_low():
    d = np.full((H, W), 1.2)
    d[:, W // 2:] = 0.8
    r = classify(_stack(*[d * s for s in (0.5, 0.6, 0.7, 0.65)]), INSIDE)
    assert not r.homogeneous
    assert set(np.unique(r.levels[:, : W // 2])) == {1}
    assert set(np.unique(r.levels[:, W // 2:])) == {3}


def _with_patch(factors):
    dates = []
    for f in factors:
        d = np.full((H, W), 0.6)
        d[:5, :5] = 0.6 * f
        dates.append(d)
    return _stack(*dates)


def test_critical_needs_persistence():
    persistent = classify(_with_patch([0.6, 0.6, 0.6, 0.6, 1.0]), INSIDE)
    assert set(np.unique(persistent.levels[:5, :5])) == {4}
    occasional = classify(_with_patch([0.6, 0.6, 0.85, 0.85, 1.0]), INSIDE)
    assert set(np.unique(occasional.levels[:5, :5])) == {3}


def test_nan_holes_do_not_bias_date_mean():
    d = np.full((H, W), 0.6)
    d[:5, :5] = 0.3
    holed = d.copy()
    holed[10:, 10:] = np.nan
    r = classify(_stack(d, holed, d), INSIDE)
    assert np.allclose(r.rel[1][~np.isnan(r.rel[1])], (holed / np.nanmean(holed))[~np.isnan(holed)])
    assert set(np.unique(r.levels[:5, :5])) == {4}


def test_outside_and_never_valid_pixels_are_zero():
    inside = INSIDE.copy()
    inside[0, :] = False
    d = np.full((H, W), 0.6)
    d[5, 5] = np.nan
    r = classify(_stack(d, d, d), inside)
    assert (r.levels[0, :] == 0).all()
    assert r.levels[5, 5] == 0
