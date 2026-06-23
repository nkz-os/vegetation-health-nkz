"""
Historical baseline — build per-parcel index history for 5-10 years.
Results persisted as AgriParcelRecord entities in Orion-LD.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.tasks.historical_baseline import build_historical_baseline
from nkz_platform_sdk import SyncOrionClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/parcels", tags=["history"])


class BuildHistoryRequest(BaseModel):
    years: int = Field(5, ge=1, le=20)
    index: str = Field("NDVI", pattern="^(NDVI|GNDVI|NDRE|SAVI|EVI)$")
    window_days: int = Field(20, ge=5, le=90)
    cloud_threshold: float = Field(30.0, ge=0, le=100)


@router.post("/{entity_id}/build-history")
async def build_history(
    entity_id: str,
    request: BuildHistoryRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Disparar worker de histórico para una parcela."""
    tenant_id = current_user["tenant_id"]

    task = build_historical_baseline.delay(
        tenant_id=tenant_id,
        entity_id=entity_id,
        years=request.years,
        index=request.index,
        window_days=request.window_days,
        cloud_threshold=request.cloud_threshold,
    )

    return {
        "job_id": task.id,
        "message": f"Building {request.years}-year {request.index} history for parcel",
    }


@router.get("/{entity_id}/history")
async def get_history(
    entity_id: str,
    index: str = "NDVI",
    year: Optional[int] = None,
    window_days: int = 20,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Read historical AgriParcelRecord entries for a parcel from Orion-LD."""
    tenant_id = current_user["tenant_id"]

    # Build query
    attr = f"{index.lower()}Mean"
    parcel_id = entity_id if entity_id.startswith("urn:ngsi-ld:AgriParcel:") else f"urn:ngsi-ld:AgriParcel:{entity_id}"
    q = f'hasAgriParcel=="{parcel_id}"'
    if year:
        q += f';year=={year}'

    try:
        orion = SyncOrionClient(tenant_id)
        resp = orion.get("/ngsi-ld/v1/entities", params={
            "type": "AgriParcelRecord",
            "q": q,
            "limit": 500,
            "attrs": f"observedAt,{attr},{index.lower()}Min,{index.lower()}Max,{index.lower()}Std,windowSize,year",
        })
        if resp.status_code != 200:
            raise HTTPException(502, detail=f"Orion-LD query failed: {resp.status_code}")

        records = resp.json()
    except Exception as e:
        raise HTTPException(502, detail=f"Orion-LD unreachable: {e}")

    return {
        "entity_id": entity_id,
        "index": index,
        "data": [
            {
                "observedAt": r.get("observedAt", {}).get("value"),
                f"{index.lower()}Mean": r.get(f"{index.lower()}Mean", {}).get("value"),
                f"{index.lower()}Min": r.get(f"{index.lower()}Min", {}).get("value"),
                f"{index.lower()}Max": r.get(f"{index.lower()}Max", {}).get("value"),
                f"{index.lower()}Std": r.get(f"{index.lower()}Std", {}).get("value"),
                "windowSize": r.get("windowSize", {}).get("value"),
                "year": r.get("year", {}).get("value"),
            }
            for r in records if isinstance(records, list)
        ],
    }
