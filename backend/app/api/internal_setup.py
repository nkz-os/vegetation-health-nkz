"""Internal endpoints for parcel activation workflow."""
import logging
import os
import hmac
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/internal", tags=["internal"])

_INTERNAL_SECRET: str | None = None


def _get_internal_secret() -> str:
    global _INTERNAL_SECRET
    if _INTERNAL_SECRET is None:
        _INTERNAL_SECRET = os.getenv("INTERNAL_SERVICE_SECRET", "")
    return _INTERNAL_SECRET


def _verify_internal_secret(request: Request) -> None:
    """Verify X-Internal-Service-Secret header."""
    secret = _get_internal_secret()
    provided = request.headers.get("X-Internal-Service-Secret", "")
    if not secret or not hmac.compare_digest(secret, provided):
        raise HTTPException(status_code=403, detail="Invalid internal service secret")


@router.post("/setup-parcel")
async def setup_parcel(
    request: Request,
    db: Session = Depends(get_db_session),
):
    """Called by entity-manager when activating this module for a parcel.
    
    Body: { parcel_id, tenant_id }
    This endpoint is authenticated ONLY by X-Internal-Service-Secret.
    """
    _verify_internal_secret(request)
    body = await request.json()
    parcel_id = body.get("parcel_id")
    tenant_id = body.get("tenant_id")
    if not parcel_id or not tenant_id:
        raise HTTPException(status_code=400, detail="parcel_id and tenant_id required")
    
    logger.info("Setting up vegetation-prime for parcel %s / tenant %s", parcel_id, tenant_id)
    # TODO: create monitoring period defaults, subscriptions, etc.
    return {"status": "ok", "parcel_id": parcel_id, "tenant_id": tenant_id}
