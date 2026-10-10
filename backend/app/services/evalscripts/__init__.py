"""Sentinel Hub evalscripts — bundled as Python strings.

Each script is loaded from its .js file at import time.
Statistical API scripts output per-band statistics.
Process API scripts output visual RGBA images.
"""

import json
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

# Statistical API — NDMI only, so other requests never pay for B11
MOISTURE_INDEX = _load("moisture_index.js")

# Process API — visual tile rendering
NDVI_COLOR = _load("ndvi_color.js")

# Process API — single-index FLOAT32 GeoTIFF (parameterized)
INDEX_FLOAT = _load("index_float.js")

# Spectral bands each index reads; the Process API is billed per input band.
_FLOAT_INDEX_BANDS = {
    "NDVI": ["B04", "B08"],
    "EVI": ["B02", "B04", "B08"],
    "SAVI": ["B04", "B08"],
    "OSAVI": ["B04", "B08"],
    "GNDVI": ["B03", "B08"],
    "NDRE": ["B05", "B8A"],
    "NDMI": ["B8A", "B11"],
}
_SUPPORTED_FLOAT_INDICES = set(_FLOAT_INDEX_BANDS)


def build_index_float(index_type: str) -> str:
    idx = (index_type or "").upper()
    if idx not in _SUPPORTED_FLOAT_INDICES:
        raise ValueError(f"index_float does not support {index_type!r}")
    spectral = _FLOAT_INDEX_BANDS[idx]
    bands = json.dumps(spectral + ["SCL", "dataMask"])
    units = json.dumps(["reflectance"] * len(spectral) + ["DN", "DN"])
    return (INDEX_FLOAT.replace("__INDEX__", idx)
            .replace("__BANDS__", bands)
            .replace("__UNITS__", units))


__all__ = ["MULTI_INDEX", "MOISTURE_INDEX", "NDVI_COLOR", "INDEX_FLOAT", "build_index_float"]
