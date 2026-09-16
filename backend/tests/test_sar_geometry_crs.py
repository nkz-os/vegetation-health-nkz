"""The parcel geometry must reach rasterize() in the raster's own CRS.

Sentinel-1 GRD products are projected in UTM metres; NGSI-LD parcel geometry is
EPSG:4326 degrees. sar_tasks rasterized the 4326 geometry straight against the
raster transform, so the mask landed nowhere near the data, every pixel was
masked out, and the task hit its "No valid pixels" branch — which `continue`s.
The job still completed, reporting `polarizations_computed: []`.

Every other processor in the module (lst_processor, clms_lst_processor,
scl_validation) already reprojects; sar_tasks was the only one that did not.
"""
import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.features import rasterize
from shapely.geometry import box

from app.tasks.sar_tasks import _geom_in_raster_crs

# A parcel near Pamplona, in degrees, and the UTM 30N grid a S1 GRD would use.
PARCEL_4326 = box(-1.6450, 42.4900, -1.6400, 42.4950)
UTM30N = CRS.from_epsg(32630)
# 10 m pixels, origin placed so the parcel falls inside a 400x400 window.
UTM_TRANSFORM = from_origin(609000.0, 4706000.0, 10.0, 10.0)


def test_degrees_are_converted_to_the_raster_grid():
    out = _geom_in_raster_crs(PARCEL_4326, UTM30N)
    minx, miny, maxx, maxy = out.bounds
    assert 500_000 < minx < 800_000, f"x still looks like degrees: {minx}"
    assert 4_000_000 < miny < 5_000_000, f"y still looks like degrees: {miny}"


def test_mask_covers_pixels_after_reprojection():
    """The actual failure: an unprojected geometry masks nothing."""
    shape = (400, 400)
    raw = rasterize([(PARCEL_4326, 1)], out_shape=shape, transform=UTM_TRANSFORM,
                    fill=0, dtype="uint8")
    assert np.sum(raw) == 0, "precondition: degrees against a UTM grid select nothing"

    fixed = rasterize([(_geom_in_raster_crs(PARCEL_4326, UTM30N), 1)],
                      out_shape=shape, transform=UTM_TRANSFORM, fill=0, dtype="uint8")
    assert np.sum(fixed) > 0, "reprojected geometry must select pixels"


def test_geometry_already_in_4326_raster_is_left_alone():
    """A raster that really is in 4326 needs no conversion."""
    out = _geom_in_raster_crs(PARCEL_4326, CRS.from_epsg(4326))
    assert out.bounds == pytest.approx(PARCEL_4326.bounds)


def test_missing_crs_is_left_alone():
    """Some products carry no CRS; guessing one would be worse than not touching it."""
    out = _geom_in_raster_crs(PARCEL_4326, None)
    assert out.bounds == pytest.approx(PARCEL_4326.bounds)


def test_empty_stats_is_reported_as_failure_not_success():
    """A run that measures nothing must not close as completed.

    Every SAR run in production reported success with polarizations_computed: []
    while writing no EOProduct, so the pipeline looked healthy for months.
    """
    import inspect
    from app.tasks import sar_tasks

    src = inspect.getsource(sar_tasks.download_sentinel1_scene)
    assert "if stats_by_pol:" in src, "the outcome must branch on whether anything was measured"
    marker = src.index("if stats_by_pol:")
    assert "mark_failed" in src[marker:], "the empty case must fail the job"


# ---------------------------------------------------------------------------
# Sentinel-1 GRD is not geocoded.
#
# Verified against a real product (S1D_..._20260916T060843, 26410x16668):
#   crs=None, transform=identity, bounds=(0, 16668, 26410, 0) — pixel indices —
#   and 210 GCPs in EPSG:4326. The georeferencing lives in the GCPs, so there is
#   no CRS to reproject to and the geometry landed at pixel (-2, 42).
# ---------------------------------------------------------------------------
from rasterio.control import GroundControlPoint

from app.tasks.sar_tasks import _raster_grid


class _FakeSrc:
    def __init__(self, crs, transform, gcps=((), None)):
        self.crs, self.transform, self.gcps = crs, transform, gcps


def _gcps_for(minx, miny, maxx, maxy, width, height):
    """Four corner GCPs mapping a lon/lat box onto a pixel grid."""
    return [
        GroundControlPoint(row=0, col=0, x=minx, y=maxy),
        GroundControlPoint(row=0, col=width, x=maxx, y=maxy),
        GroundControlPoint(row=height, col=0, x=minx, y=miny),
        GroundControlPoint(row=height, col=width, x=maxx, y=miny),
    ]


def test_projected_raster_uses_its_own_transform():
    src = _FakeSrc(UTM30N, UTM_TRANSFORM)
    transform, crs = _raster_grid(src)
    assert transform == UTM_TRANSFORM and crs == UTM30N


def test_grd_without_crs_is_georeferenced_from_its_gcps():
    gcps = _gcps_for(-2.5, 42.0, -1.5, 43.0, 1000, 1000)
    src = _FakeSrc(None, from_origin(0, 0, 1, 1), (gcps, CRS.from_epsg(4326)))
    transform, crs = _raster_grid(src)
    assert crs == CRS.from_epsg(4326)
    # The parcel must now land on a real pixel instead of pixel (-2, 42).
    mask = rasterize([(PARCEL_4326, 1)], out_shape=(1000, 1000),
                     transform=transform, fill=0, dtype="uint8")
    assert np.sum(mask) > 0


def test_no_crs_and_no_gcps_is_left_alone():
    """Nothing to georeference with: do not invent a grid."""
    identity = from_origin(0, 0, 1, 1)
    transform, crs = _raster_grid(_FakeSrc(None, identity))
    assert transform == identity and crs is None
