"""Abstract engine interface for vegetation index computation."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal


@dataclass
class IndexResult:
    """One vegetation index result for a single (date, index_type) pair."""
    index_type: str          # "NDVI", "EVI", "SAVI", "GNDVI", "NDRE"
    sensing_date: date
    mean: float
    std: float
    min: float
    max: float
    p10: float
    p90: float
    valid_pixels: int
    total_pixels: int
    data_fidelity: str = "sentinel_hub"  # "sentinel_hub" | "local_full" | "degraded_fallback"


@dataclass
class EngineHealth:
    """Health status of an engine."""
    status: Literal["ok", "degraded", "unavailable"]
    reason: str | None = None
    last_checked: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EngineDegradedException(Exception):
    """Raised when the primary engine cannot serve a request and degradation is needed."""
    def __init__(self, reason: str, retry_after_seconds: int = 3600):
        super().__init__(reason)
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


class TileLocalFallback(Exception):
    """Raised by `EngineSelector.get_tile` when the primary (SentinelHub)
    engine cannot serve a tile and the caller must fall back to querying
    the locally stored COG directly.

    Unlike `compute_indices`, there is no working engine-level tile
    fallback: `LocalProcessingEngine.get_tile` unconditionally raises
    `NotImplementedError` (Phase 3 tile rendering through the engine
    interface is not implemented yet). The real local-tile fallback lives
    in `api/tiles.py`'s COG query, so the selector signals "go do that"
    via this typed exception instead of a bare `NotImplementedError`.
    """
    pass


class BaseVegetationEngine(ABC):
    """Shared contract for vegetation index computation engines.

    Both SentinelHubEngine and LocalProcessingEngine implement this interface,
    producing identical EOProduct outputs regardless of the underlying engine.
    """

    @abstractmethod
    async def compute_indices(
        self,
        tenant_id: str,
        parcel_id: str,
        parcel_geometry: dict,       # GeoJSON geometry
        date_range: tuple[date, date],
        index_types: list[str],      # ["NDVI", "EVI", "SAVI", …]
        cloud_cover_max: float = 50.0,
        formula: str | None = None,       # custom expression (local engine only)
        formula_id: str | None = None,    # tenant custom-formula id (local engine only)
    ) -> list[IndexResult]:
        """Compute vegetation indices for a parcel over a date range.

        Returns one IndexResult per (sensing_date, index_type) pair.

        `formula`/`formula_id` are honored only by the local engine (custom
        indices). SentinelHub ignores them — it is only ever routed
        eligible, non-custom indices.
        """
        ...

    @abstractmethod
    async def get_tile(
        self,
        tenant_id: str,
        index_type: str,
        z: int,
        x: int,
        y: int,
        date_str: str | None = None,
        color_ramp: str = "agronomic",
    ) -> bytes:
        """Return a rendered PNG tile (256x256) for the given index and zoom level."""
        ...

    @abstractmethod
    async def health_check(self) -> EngineHealth:
        """Return current engine health status."""
        ...

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Human-readable engine identifier."""
        ...
