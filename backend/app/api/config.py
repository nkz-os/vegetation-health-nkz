"""Tenant configuration endpoints — BYOK credentials + processing preferences."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db_with_tenant
from app.middleware.auth import require_auth
from app.models.config import VegetationConfig
from app.services.encryption import encrypt_secret, decrypt_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vegetation/config", tags=["config"])


class CopernicusCredentialsRequest(BaseModel):
    copernicus_client_id: str = Field(..., min_length=1, max_length=256)
    copernicus_client_secret: str = Field(default="", max_length=512)


class ConfigResponse(BaseModel):
    tenant_id: str
    copernicus_client_id: str | None = None
    copernicus_configured: bool = False
    default_index_type: str = "NDVI"
    cloud_coverage_threshold: int = 50
    auto_process: bool = True


@router.put("", response_model=ConfigResponse, status_code=status.HTTP_200_OK)
async def upsert_config(
    body: CopernicusCredentialsRequest,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Store or update Copernicus CDSE credentials for the current tenant.

    The client_secret is encrypted at rest using Fernet (VEGETATION_ENCRYPTION_KEY).
    client_id is stored as plaintext (it's a public identifier).
    """
    tenant_id = current_user["tenant_id"]

    existing = (
        db.query(VegetationConfig)
        .filter(VegetationConfig.tenant_id == tenant_id)
        .first()
    )

    encrypted_secret = encrypt_secret(body.copernicus_client_secret)

    if existing:
        existing.copernicus_client_id = body.copernicus_client_id
        # Keep existing secret when updating without a new one
        if body.copernicus_client_secret:
            existing.copernicus_client_secret_encrypted = encrypt_secret(body.copernicus_client_secret)
        existing.created_by = current_user.get("user_id")
        db.commit()
        db.refresh(existing)
        logger.info("Updated Copernicus credentials for tenant %s", tenant_id)
        return ConfigResponse(
            tenant_id=tenant_id,
            copernicus_client_id=existing.copernicus_client_id,
            copernicus_configured=True,
            default_index_type=existing.default_index_type or "NDVI",
            cloud_coverage_threshold=int(existing.cloud_coverage_threshold or 50),
            auto_process=existing.auto_process if existing.auto_process is not None else True,
        )

    if not body.copernicus_client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Client Secret is required for new credentials",
        )

    config = VegetationConfig(
        tenant_id=tenant_id,
        copernicus_client_id=body.copernicus_client_id,
        copernicus_client_secret_encrypted=encrypted_secret,
        created_by=current_user.get("user_id"),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    logger.info("Created Copernicus credentials for tenant %s", tenant_id)
    return ConfigResponse(
        tenant_id=tenant_id,
        copernicus_client_id=config.copernicus_client_id,
        copernicus_configured=True,
        default_index_type=config.default_index_type or "NDVI",
        cloud_coverage_threshold=int(config.cloud_coverage_threshold or 50),
        auto_process=config.auto_process if config.auto_process is not None else True,
    )


@router.get("", response_model=ConfigResponse)
async def get_config(
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Get the current tenant's vegetation configuration (no secrets exposed)."""
    tenant_id = current_user["tenant_id"]

    config = (
        db.query(VegetationConfig)
        .filter(VegetationConfig.tenant_id == tenant_id)
        .first()
    )

    if not config:
        return ConfigResponse(tenant_id=tenant_id, copernicus_configured=False)

    return ConfigResponse(
        tenant_id=tenant_id,
        copernicus_client_id=config.copernicus_client_id,
        copernicus_configured=bool(
            config.copernicus_client_id and config.copernicus_client_secret_encrypted
        ),
        default_index_type=config.default_index_type or "NDVI",
        cloud_coverage_threshold=int(config.cloud_coverage_threshold or 50),
        auto_process=config.auto_process if config.auto_process is not None else True,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    current_user: dict = Depends(require_auth),
    db: Session = Depends(get_db_with_tenant),
):
    """Remove the tenant's Copernicus credentials (clears BYOK)."""
    tenant_id = current_user["tenant_id"]

    config = (
        db.query(VegetationConfig)
        .filter(VegetationConfig.tenant_id == tenant_id)
        .first()
    )

    if not config:
        return

    config.copernicus_client_id = None
    config.copernicus_client_secret_encrypted = None
    db.commit()
    logger.info("Cleared Copernicus credentials for tenant %s", tenant_id)
