"""
Middleware and decorators for limits validation.
"""

import logging
from functools import wraps
from typing import Callable, Any
from fastapi import HTTPException, status, Depends
from sqlalchemy.orm import Session

from app.middleware.auth import require_auth
from app.database import get_db_with_tenant
from app.services.limits import LimitsValidator

logger = logging.getLogger(__name__)


def check_limits(job_type: str):
    """Decorator to check limits before executing a function.
    
    Args:
        job_type: Type of job (download, process, calculate_index)
    
    Usage:
        @check_limits('download')
        async def create_download_job(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request and dependencies
            request = None
            current_user = None
            db = None
            
            for arg in args:
                if hasattr(arg, 'headers'):  # FastAPI Request
                    request = arg
                elif isinstance(arg, dict) and 'tenant_id' in arg:  # current_user
                    current_user = arg
                elif hasattr(arg, 'query'):  # SQLAlchemy Session
                    db = arg
            
            # Also check kwargs
            if not current_user and 'current_user' in kwargs:
                current_user = kwargs['current_user']
            if not db and 'db' in kwargs:
                db = kwargs['db']
            
            if not current_user or not db:
                logger.error("Could not extract current_user or db from function args")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Internal error: could not validate limits"
                )
            
            tenant_id = current_user.get('tenant_id')
            
            # Get bounds and ha from request body if available
            bounds = None
            ha_to_process = None
            
            if request and hasattr(request, 'json'):
                try:
                    body = await request.json() if hasattr(request, 'json') else {}
                    bounds = body.get('bounds')
                    ha_to_process = body.get('ha_to_process')
                except:
                    pass
            
            # Check limits
            validator = LimitsValidator(db, tenant_id)
            is_allowed, error_message, usage_info = validator.check_all_limits(
                job_type=job_type,
                bounds=bounds,
                ha_to_process=ha_to_process
            )
            
            if not is_allowed:
                logger.warning(
                    f"Limit exceeded for tenant {tenant_id}, job_type {job_type}: {error_message}"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        'error': 'Limit exceeded',
                        'message': error_message,
                        'usage': usage_info,
                        'limits': validator.limits,
                    }
                )
            
            # Limits OK, proceed
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


async def validate_limits_dependency(
    job_type: str,
    bounds: dict = None,
    current_user: dict = Depends(require_auth),
    db: Session = Depends(lambda: next(get_db_with_tenant(current_user['tenant_id'])))
) -> dict:
    """FastAPI dependency for limits validation.
    
    Args:
        job_type: Type of job
        bounds: Optional bounds for area calculation
        current_user: Current user (from auth)
        db: Database session
        
    Returns:
        Usage info dictionary
        
    Raises:
        HTTPException 429 if limits exceeded
    """
    tenant_id = current_user.get('tenant_id')
    validator = LimitsValidator(db, tenant_id)
    
    is_allowed, error_message, usage_info = validator.check_all_limits(
        job_type=job_type,
        bounds=bounds
    )
    
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                'error': 'Limit exceeded',
                'message': error_message,
                'usage': usage_info,
                'limits': validator.limits,
            }
        )
    
    return usage_info



