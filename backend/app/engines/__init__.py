"""Vegetation index computation engines.

SentinelHubEngine  — primary, zero-download via Sentinel Hub API
LocalProcessingEngine — sovereign fallback, GDAL/rasterio local processing
"""

from .base import BaseVegetationEngine, IndexResult, EngineHealth

try:
    from .selector import EngineSelector
except ImportError:
    EngineSelector = None  # Created in Task 5

__all__ = [
    "BaseVegetationEngine",
    "IndexResult",
    "EngineHealth",
    "EngineSelector",
]
