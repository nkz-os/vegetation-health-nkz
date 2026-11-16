"""Multi-date zoning: spatial regularisation of the level map."""
import numpy as np

from app.jobs.zoning_multidate.regions import regularize

INSIDE = np.ones((30, 30), dtype=bool)


def test_small_islands_merge_into_neighbour():
    levels = np.full((30, 30), 2, dtype="int8")
    levels[:, 15:] = 1
    levels[5:7, 5:7] = 3  # 4 px = 0.04 ha at 10 m
    out = regularize(levels, INSIDE, 10.0, 0.25)
    assert (out[5:7, 5:7] == 2).all()
    assert set(np.unique(out)) == {1, 2}


def test_large_regions_survive():
    levels = np.full((30, 30), 2, dtype="int8")
    levels[:, 20:] = 4  # 300 px = 3 ha
    out = regularize(levels, INSIDE, 10.0, 0.25)
    assert (out[:, 21:] == 4).all()


def test_regularize_is_deterministic():
    rng = np.random.default_rng(7)
    levels = rng.integers(1, 5, size=(30, 30)).astype("int8")
    a = regularize(levels, INSIDE, 10.0, 0.25)
    b = regularize(levels.copy(), INSIDE, 10.0, 0.25)
    assert np.array_equal(a, b)


def test_gaps_inside_are_filled_and_outside_stays_zero():
    inside = INSIDE.copy()
    inside[:, :3] = False
    levels = np.full((30, 30), 2, dtype="int8")
    levels[:, :3] = 0
    levels[10:12, 10:12] = 0  # never-valid pixels inside
    out = regularize(levels, inside, 10.0, 0.25)
    assert (out[10:12, 10:12] == 2).all()
    assert (out[:, :3] == 0).all()


def test_noise_is_smoothed():
    rng = np.random.default_rng(3)
    levels = np.full((30, 30), 2, dtype="int8")
    levels[:, 15:] = 1
    noise = rng.random((30, 30)) < 0.05
    levels[noise] = 3
    out = regularize(levels, INSIDE, 10.0, 0.25)
    assert set(np.unique(out)) == {1, 2}
