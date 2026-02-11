"""
Usage tracking service for calculating and storing usage metrics.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, date
from decimal import Decimal
import math

from sqlalchemy import func, text
from sqlalchemy.orm import Session
from shapely.geometry import shape
from shapely.ops import transform
import pyproj

from app.models import VegetationUsageStats, VegetationUsageLog, VegetationJob
from app.database import get_db_session

logger = logging.getLogger(__name__)


class UsageTracker:
    """Tracks usage metrics (Ha processed, jobs created, etc.)."""
    
    @staticmethod
    def calculate_area_hectares(bounds: Dict[str, Any]) -> Decimal:
        """Calculate area in hectares from GeoJSON bounds.
        
        Args:
            bounds: GeoJSON polygon or bbox [min_lon, min_lat, max_lon, max_lat]
            
        Returns:
            Area in hectares (Decimal)
        """
        try:
            if bounds is None:
                return Decimal('0.0')
            
            # Handle bbox format [min_lon, min_lat, max_lon, max_lat]
            if isinstance(bounds, list) and len(bounds) == 4:
                min_lon, min_lat, max_lon, max_lat = bounds
                # Create polygon from bbox
                polygon = shape({
                    'type': 'Polygon',
                    'coordinates': [[
                        [min_lon, min_lat],
                        [max_lon, min_lat],
                        [max_lon, max_lat],
                        [min_lon, max_lat],
                        [min_lon, min_lat]
                    ]]
                })
            elif isinstance(bounds, dict) and bounds.get('type') == 'Polygon':
                polygon = shape(bounds)
            else:
                logger.warning(f"Invalid bounds format: {bounds}")
                return Decimal('0.0')
            
            # Try to use PostGIS if available (more accurate)
            # Otherwise use Shapely with projection
            try:
                # Use equal-area projection for accurate area calculation
                # WGS84 to World Mollweide (equal area)
                wgs84 = pyproj.CRS('EPSG:4326')
                mollweide = pyproj.CRS('EPSG:54009')  # World Mollweide
                
                project = pyproj.Transformer.from_crs(
                    wgs84, mollweide, always_xy=True
                ).transform
                
                projected_polygon = transform(project, polygon)
                area_m2 = projected_polygon.area
                
            except Exception as e:
                logger.warning(f"PostGIS/projection calculation failed, using Shapely: {str(e)}")
                # Fallback: simple calculation (less accurate for large areas)
                # This is approximate but works for small-medium areas
                area_m2 = polygon.area * 111000 * 111000  # Rough conversion
            
            # Convert m² to hectares (1 Ha = 10,000 m²)
            area_ha = Decimal(str(area_m2 / 10000))
            
            return area_ha
            
        except Exception as e:
            logger.error(f"Error calculating area: {str(e)}", exc_info=True)
            return Decimal('0.0')
    
    @staticmethod
    def record_job_usage(
        db: Session,
        tenant_id: str,
        job_id: str,
        job_type: str,
        bounds: Optional[Dict[str, Any]] = None,
        ha_processed: Optional[Decimal] = None
    ) -> Decimal:
        """Record usage for a job.
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            job_id: Job ID
            job_type: Type of job
            bounds: Optional bounds for area calculation
            ha_processed: Optional pre-calculated area
            
        Returns:
            Hectares processed
        """
        try:
            # Calculate area if not provided
            if ha_processed is None:
                ha_processed = UsageTracker.calculate_area_hectares(bounds)
            
            now = datetime.utcnow()
            current_year = now.year
            current_month = now.month
            
            # Get or create usage stats for current month
            stats = db.query(VegetationUsageStats).filter(
                VegetationUsageStats.tenant_id == tenant_id,
                VegetationUsageStats.year == current_year,
                VegetationUsageStats.month == current_month
            ).first()
            
            if not stats:
                stats = VegetationUsageStats(
                    tenant_id=tenant_id,
                    year=current_year,
                    month=current_month,
                    first_job_at=now
                )
                db.add(stats)
            
            # Update aggregated stats (NULL-safe)
            stats.ha_processed = (stats.ha_processed or Decimal('0.0')) + ha_processed
            stats.ha_processed_count = (stats.ha_processed_count or 0) + 1
            stats.jobs_created = (stats.jobs_created or 0) + 1
            
            # Update job type counters
            if job_type == 'download':
                stats.download_jobs = (stats.download_jobs or 0) + 1
            elif job_type == 'process':
                stats.process_jobs = (stats.process_jobs or 0) + 1
            elif job_type == 'calculate_index':
                stats.calculate_jobs = (stats.calculate_jobs or 0) + 1
            
            stats.last_job_at = now
            
            # Create detailed log entry
            log_entry = VegetationUsageLog(
                tenant_id=tenant_id,
                job_id=job_id,
                ha_processed=ha_processed,
                job_type=job_type,
                processed_at=now,
                bounds=None  # Could store bounds if needed for verification
            )
            db.add(log_entry)
            
            db.commit()
            
            logger.info(f"Recorded usage: {ha_processed} Ha for job {job_id} (tenant: {tenant_id})")
            
            return ha_processed
            
        except Exception as e:
            logger.error(f"Error recording usage: {str(e)}", exc_info=True)
            db.rollback()
            return Decimal('0.0')
    
    @staticmethod
    def get_current_month_usage(db: Session, tenant_id: str) -> Dict[str, Any]:
        """Get current month usage statistics.
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            
        Returns:
            Dictionary with usage metrics
        """
        now = datetime.utcnow()
        current_year = now.year
        current_month = now.month
        
        stats = db.query(VegetationUsageStats).filter(
            VegetationUsageStats.tenant_id == tenant_id,
            VegetationUsageStats.year == current_year,
            VegetationUsageStats.month == current_month
        ).first()
        
        if not stats:
            return {
                'ha_processed': Decimal('0.0'),
                'ha_processed_count': 0,
                'jobs_created': 0,
                'jobs_completed': 0,
                'jobs_failed': 0,
                'download_jobs': 0,
                'process_jobs': 0,
                'calculate_jobs': 0,
            }
        
        return {
            'ha_processed': stats.ha_processed or Decimal('0.0'),
            'jobs_created': stats.jobs_created or 0,
            'jobs_completed': stats.jobs_completed or 0,
            'jobs_failed': stats.jobs_failed or 0,
            'download_jobs': stats.download_jobs or 0,
            'process_jobs': stats.process_jobs or 0,
            'calculate_jobs': stats.calculate_jobs or 0,
        }
    
    @staticmethod
    def update_job_status(
        db: Session,
        tenant_id: str,
        job_id: str,
        status: str
    ) -> None:
        """Update job status counters in usage stats.
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            job_id: Job ID
            status: New job status
        """
        try:
            now = datetime.utcnow()
            current_year = now.year
            current_month = now.month
            
            stats = db.query(VegetationUsageStats).filter(
                VegetationUsageStats.tenant_id == tenant_id,
                VegetationUsageStats.year == current_year,
                VegetationUsageStats.month == current_month
            ).first()
            
            if stats:
                if status == 'completed':
                    stats.jobs_completed = (stats.jobs_completed or 0) + 1
                elif status == 'failed':
                    stats.jobs_failed = (stats.jobs_failed or 0) + 1
                
                db.commit()
                
        except Exception as e:
            logger.error(f"Error updating job status: {str(e)}")
            db.rollback()
