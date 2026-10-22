"""
Legacy crop seasons router — delegates to monitoring-periods.
"""
from app.api.monitoring_periods import router as legacy_router

# All /api/vegetation/crop-seasons/X routes are now served by monitoring_periods.py
# Re-exporting the router for backward compatibility in main.py
router = legacy_router
