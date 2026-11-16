"""Multi-date zoning: level polygons clipped to the parcel and their summaries."""
import numpy as np
import pytest
from shapely.geometry import MultiPolygon, Polygon, mapping

from app.jobs.zoning_multidate.grid import parcel_grid
from app.jobs.zoning_multidate.polygons import level_polygons, summarize

OUTER = Polygon(
    [(-1.6510, 42.8000), (-1.6486, 42.8000), (-1.6486, 42.8014), (-1.6510, 42.8014)],
    holes=[[(-1.6502, 42.8004), (-1.6497, 42.8004), (-1.6497, 42.8008), (-1.6502, 42.8008)]],
)
ISLAND = Polygon([(-1.6480, 42.8000), (-1.6474, 42.8000), (-1.6474, 42.8005), (-1.6480, 42.8005)])
PARCEL = MultiPolygon([OUTER, ISLAND])


def _levels(grid):
    h, w = grid.shape
    lv = np.zeros((h, w), dtype="int8")
    lv[grid.inside] = 2
    left = np.zeros_like(grid.inside)
    left[:, : w // 3] = True
    lv[grid.inside & left] = 1
    lv[grid.inside & ~left & (np.arange(h)[:, None] > h // 2)] = 3
    return lv


def test_polygons_sum_to_parcel_area():
    g = parcel_grid(mapping(PARCEL), 10.0)
    polys = level_polygons(_levels(g), g)
    total = sum(p.area for p in polys.values())
    assert total == pytest.approx(g.parcel_utm.area, rel=0.005)


def test_polygons_valid_disjoint_and_within_parcel():
    g = parcel_grid(mapping(PARCEL), 10.0)
    polys = level_polygons(_levels(g), g)
    assert set(polys) == {1, 2, 3}
    for p in polys.values():
        assert p.is_valid and p.geom_type in ("Polygon", "MultiPolygon")
        assert p.within(g.parcel_utm.buffer(0.01))
    ranks = sorted(polys)
    for i, a in enumerate(ranks):
        for b in ranks[i + 1:]:
            assert polys[a].intersection(polys[b]).area < 1.0


def test_polygons_are_deterministic():
    g = parcel_grid(mapping(PARCEL), 10.0)
    a = level_polygons(_levels(g), g)
    b = level_polygons(_levels(g), g)
    assert all(a[r].equals(b[r]) for r in a)


def test_summary_confirming_counts():
    g = parcel_grid(mapping(PARCEL), 10.0)
    lv = _levels(g)
    polys = level_polygons(lv, g)
    stack = np.stack([np.where(lv == 1, 0.8, 0.5).astype("float32") for _ in range(4)])
    stack[3][lv == 1] = 0.4  # one date where the high level was below the mean
    rel = np.stack([d / np.nanmean(np.where(g.inside, d, np.nan)) for d in stack])
    zones = summarize(lv, stack, rel, polys)
    high = next(z for z in zones if z.rank == 1)
    assert (high.level, high.total, high.confirming) == ("high", 4, 3)
    assert high.relative_pct > 0
    assert high.mean_value == pytest.approx(0.7, abs=1e-6)
    assert high.area_ha == pytest.approx(polys[1].area / 10_000, rel=1e-9)
    assert [z.rank for z in zones] == sorted(z.rank for z in zones)


def test_stair_edges_become_diagonals():
    """A 1:2 diagonal boundary between two levels is drawn as a few straight segments."""
    g = parcel_grid(mapping(OUTER), 10.0)
    h, w = g.shape
    rows, cols = np.indices((h, w))
    lv = np.where(g.inside, np.where(cols * 2 < rows + w // 2, 1, 2), 0).astype("int8")
    polys = level_polygons(lv, g)
    border = polys[1].intersection(polys[2].buffer(0.5))
    vertices = sum(len(p.exterior.coords) for p in getattr(polys[1], "geoms", [polys[1]]))
    assert vertices < 12
    assert border.length > 0


def test_ndvi_bare_floor_is_035():
    from app.jobs.zoning_multidate import config
    assert config.VEGETATED_FLOOR["NDVI"] == 0.35


def test_low_border_ring_is_not_a_zone():
    """Mixed pixels along the boundary (crop + track) must not become a 'low' strip."""
    from app.jobs.zoning_multidate.levels import classify
    from app.jobs.zoning_multidate.regions import regularize
    g = parcel_grid(mapping(OUTER), 10.0)
    ring = g.inside & ~g.core
    day = np.where(ring, 0.35, 0.6).astype("float32")
    day[:, : g.shape[1] // 2] *= 1.25
    stack = np.stack([day, day * 0.9, day * 1.1])
    res = classify(stack, g.core)
    lv = regularize(res.levels, g.inside, 10.0, 0.25)
    assert 3 not in np.unique(lv[ring]) and 4 not in np.unique(lv[ring])


def _patchwork(grid):
    h, w = grid.shape
    lv = np.where(grid.inside, 2, 0).astype("int8")
    lv[2:9, 3:9] = np.where(grid.inside[2:9, 3:9], 1, 0)       # high block
    lv[2:9, 9:14] = np.where(grid.inside[2:9, 9:14], 3, 0)     # low block touching it
    lv[10:13, 4:7] = np.where(grid.inside[10:13, 4:7], 4, 0)   # critical patch
    return lv


def test_no_sub_pixel_parts_in_any_level():
    g = parcel_grid(mapping(OUTER), 10.0)
    polys = level_polygons(_patchwork(g), g)
    for rank, p in polys.items():
        for part in getattr(p, "geoms", [p]):
            assert part.area >= 100.0, (rank, part.area)
    assert sum(p.area for p in polys.values()) == pytest.approx(g.parcel_utm.area, rel=0.005)


def test_mean_value_ignores_the_border_ring():
    g = parcel_grid(mapping(OUTER), 10.0)
    lv = _levels(g)
    polys = level_polygons(lv, g)
    ring = g.inside & ~g.core
    stack = np.stack([np.where(ring, 0.1, 0.6).astype("float32") for _ in range(3)])
    rel = np.stack([d / np.nanmean(np.where(g.core, d, np.nan)) for d in stack])
    zones = summarize(lv, stack, rel, polys, core=g.core)
    assert all(z.mean_value == pytest.approx(0.6, abs=1e-6) for z in zones)
