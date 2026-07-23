# backend/app/main.py
"""
FastAPI entry point for Vegetation Prime module.
"""
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Database and Middleware
from app.database import init_db

# Specialized Routers (SOLID refactor)
from app.api.jobs import router as jobs_router
from app.api.parcels import router as parcels_router
from app.api.tiles import router as tiles_router
from app.api.entities import router as entities_router
from app.api.subscriptions import router as subscriptions_router
from app.api.internal import router as internal_router
from app.api.internal_setup import router as internal_setup_router
from app.api.timeseries_adapter import router as timeseries_adapter_router
from app.api.scenes import router as scenes_router
from app.api.sync import router as sync_router
from app.api.custom_formulas import router as custom_formulas_router
from app.api.monitoring_periods import router as crop_seasons_router  # replaced crop_seasons (legacy redirect)
from app.api.export import router as export_router
from app.api.parcels import router as parcels_router
from app.api.sar import router as sar_router
from app.api.history import router as history_router
from app.api.config import router as config_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for the module."""
    logger.info("Starting Vegetation Prime API...")
    init_db()

    # Fail-fast: INTERNAL_SERVICE_SECRET is required for internal auth
    if not os.getenv("INTERNAL_SERVICE_SECRET"):
        logger.warning("INTERNAL_SERVICE_SECRET not set — internal endpoints will reject all requests")

    # Singleton EngineSelector (shared across all requests, preserves degradation state)
    from app.engines.selector import EngineSelector
    app.state.engine_selector = EngineSelector()
    logger.info("EngineSelector initialized")

    # BYOK encryption: warn if key is missing (plaintext storage in dev only)
    if not os.getenv("VEGETATION_ENCRYPTION_KEY"):
        logger.warning(
            "VEGETATION_ENCRYPTION_KEY not set — BYOK secrets will be stored as plaintext. "
            "Set this in production via K8s Secret."
        )

    yield

    # Cleanup Sentinel Hub clients
    try:
        selector = app.state.engine_selector
        for engine in selector._engines.values():
            await engine.close()
    except Exception as e:
        logger.debug("Engine cleanup: %s", e)

    logger.info("Shutting down Vegetation Prime API...")

app = FastAPI(
    title="Vegetation Prime API",
    description="High-performance vegetation intelligence suite (IIFE/SDK Compliant)",
    version="2.0.0",
    lifespan=lifespan
)

# Exception Handler for Validation Errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()}
    )

# CORS Configuration
ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "https://nekazari.robotika.cloud").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID", "Fiware-Service"],
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Health checks (exempt from rate limiting)
@limiter.exempt
@app.get("/health")
@app.get("/healthz")
@app.get("/api/vegetation/health")
async def health():
    return {"status": "healthy", "module": "vegetation-prime"}

@limiter.exempt
@app.get("/readyz")
async def readyz():
    """Readiness probe — checks DB connectivity."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=str(e))
    finally:
        db.close()

# Include Routers (The core of the platform)
app.include_router(jobs_router)
app.include_router(parcels_router)
app.include_router(tiles_router)
app.include_router(entities_router)
app.include_router(subscriptions_router, prefix="/api/vegetation", tags=["subscriptions"])
app.include_router(scenes_router)
app.include_router(internal_router)
app.include_router(internal_setup_router)
app.include_router(timeseries_adapter_router)
app.include_router(sync_router)
app.include_router(custom_formulas_router)
app.include_router(crop_seasons_router)
app.include_router(export_router)
app.include_router(sar_router)
app.include_router(history_router)
app.include_router(config_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
