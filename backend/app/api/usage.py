"""Read-only satellite-computation usage endpoint (BYOK quota reporting).

Separate from `app/api/config.py`'s CRUD router because it reports platform
DB state (via `SatelliteQuota`), not the tenant's `VegetationConfig` row —
no `get_db_with_tenant` dependency needed here.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.middleware.auth import require_auth
from app.services.satellite_quota import SatelliteQuota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/config", tags=["config"])


class UsageResponse(BaseModel):
    used: int
    limit: int | None = None
    remaining: int | None = None
    period: str


@router.get("/usage", response_model=UsageResponse)
async def get_usage(current_user: dict = Depends(require_auth)):
    """Current-month satellite-computation usage for the caller's tenant.

    Backed by `SatelliteQuota.get_usage`, which is fail-open/best-effort —
    this endpoint never 500s on a platform DB failure, it returns the
    unknown-usage shape instead.
    """
    tenant_id = current_user["tenant_id"]
    return SatelliteQuota().get_usage(tenant_id)
