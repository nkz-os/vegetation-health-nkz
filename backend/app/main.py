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

# Database and Middleware
from app.database import init_db

# Specialized Routers (SOLID refactor)
from app.api.jobs import router as jobs_router
from app.api.tiles import router as tiles_router
from app.api.entities import router as entities_router
from app.api.crops import router as crops_router
from app.api.subscriptions import router as subscriptions_router
from app.api.internal import router as internal_router
from app.api.timeseries_adapter import router as timeseries_adapter_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for the module."""
    logger.info("Starting Vegetation Prime API...")
    init_db()
    yield
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

# Health checks
@app.get("/health")
@app.get("/api/vegetation/health")
async def health():
    return {"status": "healthy", "module": "vegetation-prime"}

# Include Routers (The core of the platform)
app.include_router(jobs_router)
app.include_router(tiles_router)
app.include_router(entities_router)
app.include_router(crops_router, prefix="/api/vegetation", tags=["crops"])
app.include_router(subscriptions_router, prefix="/api/vegetation", tags=["subscriptions"])
app.include_router(internal_router)
app.include_router(timeseries_adapter_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
