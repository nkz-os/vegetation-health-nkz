"""Vegetation index computation engines.

SentinelHubEngine  — primary, zero-download via Sentinel Hub API
LocalProcessingEngine — sovereign fallback, GDAL/rasterio local processing
"""

from .base import BaseVegetationEngine, IndexResult, EngineHealth, EngineDegradedException

from .selector import EngineSelector
from .sentinel_hub import SentinelHubEngine
from .local import LocalProcessingEngine

__all__ = [
    "BaseVegetationEngine",
    "IndexResult",
    "EngineHealth",
    "EngineDegradedException",
    "SentinelHubEngine",
    "LocalProcessingEngine",
    "EngineSelector",
]
