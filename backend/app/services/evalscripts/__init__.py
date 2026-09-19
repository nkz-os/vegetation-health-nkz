"""Sentinel Hub evalscripts — bundled as Python strings.

Each script is loaded from its .js file at import time.
Statistical API scripts output per-band statistics.
Process API scripts output visual RGBA images.
"""

from pathlib import Path

_EVALSCRIPT_DIR = Path(__file__).parent


def _load(name: str) -> str:
    """Load an evalscript from its .js file."""
    path = _EVALSCRIPT_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Evalscript not found: {path}")
    return path.read_text()


# Statistical API — multi-index computation in one call
MULTI_INDEX = _load("multi_index.js")

# Process API — visual tile rendering
NDVI_COLOR = _load("ndvi_color.js")

# Process API — single-index FLOAT32 GeoTIFF (parameterized)
INDEX_FLOAT = _load("index_float.js")
_SUPPORTED_FLOAT_INDICES = {"NDVI", "EVI", "SAVI", "GNDVI", "NDRE"}


def build_index_float(index_type: str) -> str:
    idx = (index_type or "").upper()
    if idx not in _SUPPORTED_FLOAT_INDICES:
        raise ValueError(f"index_float does not support {index_type!r}")
    return INDEX_FLOAT.replace("__INDEX__", idx)


__all__ = ["MULTI_INDEX", "NDVI_COLOR", "INDEX_FLOAT", "build_index_float"]
