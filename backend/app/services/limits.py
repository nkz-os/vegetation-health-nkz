"""
Limits validation service for monetization.
Implements double-layer limits: volume (Ha) and frequency (jobs/day).
"""

import logging
import os
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, date, timedelta
from decimal import Decimal
import redis
from redis.exceptions import RedisError

from sqlalchemy.orm import Session

from app.models import VegetationPlanLimits, VegetationUsageStats
from app.services.usage_tracker import UsageTracker
logger = logging.getLogger(__name__)

# Default limits (fallback if not synced from Core)
DEFAULT_MONTHLY_HA_LIMIT = Decimal(os.getenv('DEFAULT_MONTHLY_HA_LIMIT', '10.0'))
DEFAULT_DAILY_HA_LIMIT = Decimal(os.getenv('DEFAULT_DAILY_HA_LIMIT', '5.0'))
DEFAULT_DAILY_JOBS_LIMIT = int(os.getenv('DEFAULT_DAILY_JOBS_LIMIT', '5'))
DEFAULT_MONTHLY_JOBS_LIMIT = int(os.getenv('DEFAULT_MONTHLY_JOBS_LIMIT', '100'))
DEFAULT_DAILY_DOWNLOAD_JOBS = int(os.getenv('DEFAULT_DAILY_DOWNLOAD_JOBS', '3'))
DEFAULT_DAILY_PROCESS_JOBS = int(os.getenv('DEFAULT_DAILY_PROCESS_JOBS', '10'))
DEFAULT_DAILY_CALCULATE_JOBS = int(os.getenv('DEFAULT_DAILY_CALCULATE_JOBS', '20'))


class LimitsValidator:
    """Validates usage limits before allowing operations."""
    
    def __init__(self, db: Session, tenant_id: str):
        """Initialize limits validator.
        
        Args:
            db: Database session
            tenant_id: Tenant ID
        """
        self.db = db
        self.tenant_id = tenant_id
        self.redis_client = self._get_redis_client()
        self.limits = self._load_limits()
    
    def _get_redis_client(self) -> Optional[redis.Redis]:
        """Get Redis client for rate limiting."""
        try:
            # Use same Redis as cache (database 1)
            redis_url = os.getenv('REDIS_CACHE_URL') or os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
            if '/1' not in redis_url and '/0' in redis_url:
                redis_url = redis_url.replace('/0', '/1')
            
            client = redis.from_url(redis_url, decode_responses=False)
            client.ping()
            return client
        except Exception as e:
            logger.warning(f"Redis not available for rate limiting: {str(e)}")
            return None
    
    def _load_limits(self) -> Dict[str, Any]:
        """Load limits from database or use defaults.
        
        Returns:
            Dictionary with limit values
        """
        limits = self.db.query(VegetationPlanLimits).filter(
            VegetationPlanLimits.tenant_id == self.tenant_id,
            VegetationPlanLimits.is_active == True
        ).first()
        
        if limits:
            return {
                'monthly_ha_limit': Decimal(str(limits.monthly_ha_limit)) if limits.monthly_ha_limit else DEFAULT_MONTHLY_HA_LIMIT,
                'daily_ha_limit': Decimal(str(limits.daily_ha_limit)) if limits.daily_ha_limit else DEFAULT_DAILY_HA_LIMIT,
                'daily_jobs_limit': limits.daily_jobs_limit or DEFAULT_DAILY_JOBS_LIMIT,
                'monthly_jobs_limit': limits.monthly_jobs_limit or DEFAULT_MONTHLY_JOBS_LIMIT,
                'daily_download_jobs_limit': limits.daily_download_jobs_limit or DEFAULT_DAILY_DOWNLOAD_JOBS,
                'daily_process_jobs_limit': limits.daily_process_jobs_limit or DEFAULT_DAILY_PROCESS_JOBS,
                'daily_calculate_jobs_limit': limits.daily_calculate_jobs_limit or DEFAULT_DAILY_CALCULATE_JOBS,
                'plan_type': limits.plan_type,
                'plan_name': limits.plan_name,
            }
        else:
            # Use defaults (safe fallback)
            logger.warning(f"No limits found for tenant {self.tenant_id}, using defaults")
            return {
                'monthly_ha_limit': DEFAULT_MONTHLY_HA_LIMIT,
                'daily_ha_limit': DEFAULT_DAILY_HA_LIMIT,
                'daily_jobs_limit': DEFAULT_DAILY_JOBS_LIMIT,
                'monthly_jobs_limit': DEFAULT_MONTHLY_JOBS_LIMIT,
                'daily_download_jobs_limit': DEFAULT_DAILY_DOWNLOAD_JOBS,
                'daily_process_jobs_limit': DEFAULT_DAILY_PROCESS_JOBS,
                'daily_calculate_jobs_limit': DEFAULT_DAILY_CALCULATE_JOBS,
                'plan_type': 'unconfigured',  # Changed from 'basic' to indicate not configured
                'plan_name': None,  # No plan name when using defaults
            }
    
    def _get_rate_limit_key(self, job_type: str, date_str: Optional[str] = None) -> str:
        """Generate Redis key for rate limiting.
        
        Args:
            job_type: Type of job (download, process, calculate_index)
            date_str: Date string (YYYY-MM-DD), defaults to today
            
        Returns:
            Redis key string
        """
        if date_str is None:
            date_str = date.today().isoformat()
        
        return f"rate_limit:{self.tenant_id}:{job_type}:{date_str}"
    
    def check_volume_limit(
        self,
        bounds: Optional[Dict[str, Any]] = None,
        ha_to_process: Optional[Decimal] = None
    ) -> Tuple[bool, Optional[str], Decimal]:
        """Check if volume limit (Ha) would be exceeded.
        
        Args:
            bounds: GeoJSON bounds for area calculation
            ha_to_process: Pre-calculated hectares (optional)
            
        Returns:
            Tuple of (is_allowed, error_message, ha_processed)
        """
        try:
            # Calculate area if not provided
            if ha_to_process is None:
                ha_to_process = UsageTracker.calculate_area_hectares(bounds)
            
            # Get current month usage
            current_usage = UsageTracker.get_current_month_usage(self.db, self.tenant_id)
            current_ha = current_usage.get('ha_processed', Decimal('0.0'))
            
            # Ensure Decimal types
            if not isinstance(current_ha, Decimal):
                current_ha = Decimal(str(current_ha))
            
            # Check monthly limit
            monthly_limit = self.limits['monthly_ha_limit']
            if not isinstance(monthly_limit, Decimal):
                monthly_limit = Decimal(str(monthly_limit))
            
            if current_ha + ha_to_process > monthly_limit:
                return (
                    False,
                    f"Monthly hectare limit exceeded: {float(current_ha + ha_to_process):.2f} Ha > {float(monthly_limit)} Ha",
                    ha_to_process
                )
            
            # Check daily limit (approximate - would need daily stats table for exact)
            daily_limit = self.limits['daily_ha_limit']
            if not isinstance(daily_limit, Decimal):
                daily_limit = Decimal(str(daily_limit))
            
            # For now, we'll use a simple check (could be improved with daily stats)
            if ha_to_process > daily_limit:
                return (
                    False,
                    f"Daily hectare limit exceeded: {float(ha_to_process):.2f} Ha > {float(daily_limit)} Ha",
                    ha_to_process
                )
            
            return (True, None, ha_to_process)
            
        except Exception as e:
            logger.error(f"Error checking volume limit: {str(e)}", exc_info=True)
            # Fail open for now (could be made configurable)
            return (True, None, ha_to_process or Decimal('0.0'))
    
    def check_frequency_limit(self, job_type: str) -> Tuple[bool, Optional[str], int]:
        """Check if frequency limit (jobs/day) would be exceeded.
        
        Uses Redis for atomic rate limiting.
        
        Args:
            job_type: Type of job (download, process, calculate_index)
            
        Returns:
            Tuple of (is_allowed, error_message, current_count)
        """
        if not self.redis_client:
            logger.warning("Redis not available, skipping frequency limit check")
            return (True, None, 0)
        
        try:
            # Get rate limit key for today
            rate_key = self._get_rate_limit_key(job_type)
            
            # Get job-specific limit
            if job_type == 'download':
                limit = self.limits['daily_download_jobs_limit']
            elif job_type == 'process':
                limit = self.limits['daily_process_jobs_limit']
            elif job_type == 'calculate_index':
                limit = self.limits['daily_calculate_jobs_limit']
            else:
                limit = self.limits['daily_jobs_limit']
            
            # Atomic increment and check
            # Set expiration to end of day (TTL in seconds until midnight)
            now = datetime.now()
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            ttl = int((midnight - now).total_seconds())
            
            current_count = self.redis_client.incr(rate_key)
            
            # Set expiration if this is the first increment
            if current_count == 1:
                self.redis_client.expire(rate_key, ttl)
            
            # Check limit
            if current_count > limit:
                return (
                    False,
                    f"Daily {job_type} jobs limit exceeded: {current_count} > {limit}",
                    current_count
                )
            
            return (True, None, current_count)
            
        except RedisError as e:
            logger.error(f"Redis error checking frequency limit: {str(e)}")
            # Fail open if Redis is down
            return (True, None, 0)
        except Exception as e:
            logger.error(f"Error checking frequency limit: {str(e)}", exc_info=True)
            return (True, None, 0)
    
    def check_all_limits(
        self,
        job_type: str,
        bounds: Optional[Dict[str, Any]] = None,
        ha_to_process: Optional[Decimal] = None
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """Check all limits (volume + frequency).
        
        Args:
            job_type: Type of job
            bounds: GeoJSON bounds
            ha_to_process: Pre-calculated hectares
            
        Returns:
            Tuple of (is_allowed, error_message, usage_info)
        """
        # Check frequency limit first (cheaper check)
        freq_allowed, freq_error, freq_count = self.check_frequency_limit(job_type)
        if not freq_allowed:
            return (False, freq_error, {'frequency_count': freq_count})
        
        # Check volume limit
        vol_allowed, vol_error, ha_processed = self.check_volume_limit(bounds, ha_to_process)
        if not vol_allowed:
            return (False, vol_error, {'ha_processed': float(ha_processed)})
        
        # All checks passed
        return (
            True,
            None,
            {
                'frequency_count': freq_count,
                'ha_processed': float(ha_processed),
                'limits': self.limits,
            }
        )
    
    def get_current_usage(self) -> Dict[str, Any]:
        """Get current usage statistics.
        
        Returns:
            Dictionary with current usage metrics in simplified format
        """
        current_usage = UsageTracker.get_current_month_usage(self.db, self.tenant_id)
        
        # Get daily job counts from Redis
        daily_jobs_total = 0
        if self.redis_client:
            today = date.today().isoformat()
            for job_type in ['download', 'process', 'calculate_index']:
                key = self._get_rate_limit_key(job_type, today)
                try:
                    count = self.redis_client.get(key)
                    daily_jobs_total += int(count) if count else 0
                except:
                    pass
        
        # Get plan type
        plan_type_raw = self.limits.get('plan_type', 'unconfigured')
        plan_name = self.limits.get('plan_name')
        
        # Format plan type for display
        if plan_type_raw == 'unconfigured':
            plan_type = 'NO_CONFIGURADO'
        elif plan_name:
            plan_type = plan_name.upper()
        else:
            plan_type = plan_type_raw.upper()
        
        # Calculate daily jobs limit (sum of all job type limits)
        daily_jobs_limit = (
            self.limits.get('daily_download_jobs_limit', 0) +
            self.limits.get('daily_process_jobs_limit', 0) +
            self.limits.get('daily_calculate_jobs_limit', 0)
        )
        
        return {
            'plan': plan_type,
            'volume': {
                'used_ha': float(current_usage.get('ha_processed', Decimal('0.0'))),
                'limit_ha': float(self.limits.get('monthly_ha_limit', Decimal('0.0'))),
            },
            'frequency': {
                'used_jobs_today': daily_jobs_total,
                'limit_jobs_today': daily_jobs_limit,
            },
            # Keep detailed info for internal use
            '_detailed': {
                'monthly': {
                    'ha_processed': float(current_usage.get('ha_processed', Decimal('0.0'))),
                    'ha_processed_count': current_usage.get('ha_processed_count', 0),
                    'jobs_created': current_usage.get('jobs_created', 0),
                    'jobs_completed': current_usage.get('jobs_completed', 0),
                    'jobs_failed': current_usage.get('jobs_failed', 0),
                },
                'daily': {
                    'download_jobs': current_usage.get('download_jobs', 0),
                    'process_jobs': current_usage.get('process_jobs', 0),
                    'calculate_jobs': current_usage.get('calculate_jobs', 0),
                },
                'limits': self.limits,
            }
        }
