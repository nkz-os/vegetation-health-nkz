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


__all__ = ["MULTI_INDEX", "NDVI_COLOR"]
