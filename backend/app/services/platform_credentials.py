"""
Service to retrieve external API credentials from the platform's central storage.
This allows modules to use platform-managed credentials without requiring user configuration.
"""

import os
import logging
from typing import Optional, Dict, Any
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


def _get_platform_db_connection():
    """
    Get connection to platform database (where external_api_credentials table is stored).
    
    The platform database is typically 'fiware_history' or 'nekazari' and is accessed
    via POSTGRES_URL environment variable (which points to the platform database).
    """
    # Get the module database URL and replace the database name with platform database name
    # The platform database is 'nekazari' where external_api_credentials table is stored
    database_url = os.getenv('DATABASE_URL', '')
    platform_db_name = os.getenv('PLATFORM_DATABASE_NAME', 'nekazari')
    
    if not database_url:
        logger.warning("Cannot construct platform database URL: DATABASE_URL not set")
        return None
    
    # Replace database name in connection string
    if '/' in database_url:
        # Extract base URL (everything before the last /)
        base_url = database_url.rsplit('/', 1)[0]
        platform_db_url = f"{base_url}/{platform_db_name}"
    else:
        logger.warning(f"Cannot parse DATABASE_URL to construct platform database URL: {database_url}")
        return None
    
    try:
        conn = psycopg2.connect(platform_db_url)
        return conn
    except Exception as e:
        logger.warning(f"Failed to connect to platform database: {e}")
        return None


def get_copernicus_credentials(db=None) -> Optional[Dict[str, str]]:
    """
    Get Copernicus CDSE credentials from platform's external_api_credentials table.
    
    The credentials are stored centrally in the platform database, allowing
    all modules to use the same credentials without requiring per-module configuration.
    
    Args:
        db: Optional database session (ignored - we use direct connection to platform DB)
        
    Returns:
        Dictionary with 'client_id' and 'client_secret', or None if not found
        
    Note:
        This function queries the platform's external_api_credentials table.
        The service_name should be 'copernicus-cdse' as configured in the platform.
    """
    conn = None
    try:
        # Connect directly to platform database (not module database)
        conn = _get_platform_db_connection()
        if not conn:
            logger.debug("Cannot connect to platform database - credentials not available")
            return None
        
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Query external_api_credentials table (platform table, not module-specific)
        cur.execute("""
            SELECT 
                username,
                password_encrypted,
                service_url,
                auth_type
            FROM external_api_credentials
            WHERE service_name = 'copernicus-cdse'
            AND is_active = true
            LIMIT 1
        """)
        
        row = cur.fetchone()
        cur.close()
        
        if not row:
            logger.debug("Copernicus CDSE credentials not found in platform database")
            return None
        
        # Extract credentials
        # Note: password_encrypted might need decryption in production
        # For now, assuming it's stored in a way that can be used directly
        # In production, you might need to decrypt using pgcrypto or similar
        username = row['username']
        password_encrypted = row['password_encrypted']
        service_url = row.get('service_url') or 'https://dataspace.copernicus.eu'
        auth_type = row.get('auth_type') or 'basic_auth'
        
        if not username or not password_encrypted:
            logger.warning("Copernicus CDSE credentials incomplete (missing username or password)")
            return None
        
        # TODO: Decrypt password_encrypted if needed
        # For now, assuming the platform stores it in a way that can be used directly
        # In production, implement proper decryption based on platform's encryption method
        
        logger.info(f"Successfully retrieved Copernicus CDSE credentials from platform (username: {username[:10]}...)")
        
        return {
            'client_id': username,
            'client_secret': password_encrypted,  # May need decryption
            'service_url': service_url,
            'auth_type': auth_type
        }
        
    except psycopg2.errors.UndefinedTable:
        logger.debug("Platform credentials table (external_api_credentials) does not exist")
        return None
    except Exception as e:
        # Table might not exist or not accessible - this is OK for modules
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "relation" in error_msg or "undefined table" in error_msg:
            logger.debug(f"Platform credentials table not accessible: {e}")
        else:
            logger.warning(f"Could not retrieve Copernicus credentials from platform: {e}")
        return None
    finally:
        if conn:
            conn.close()


def get_copernicus_credentials_with_fallback(
    db=None,
    fallback_client_id: Optional[str] = None,
    fallback_client_secret: Optional[str] = None
) -> Optional[Dict[str, str]]:
    """
    Get Copernicus credentials with fallback to module-specific config.
    
    First tries to get credentials from platform's central storage.
    If not available, falls back to provided module-specific credentials.
    
    Args:
        db: Database session
        fallback_client_id: Fallback client ID (from module config)
        fallback_client_secret: Fallback client secret (from module config)
        
    Returns:
        Dictionary with credentials or None if neither source available
    """
    # Try env vars first (simplest and most reliable)
    env_client_id = os.getenv('COPERNICUS_CLIENT_ID', '')
    env_client_secret = os.getenv('COPERNICUS_CLIENT_SECRET', '')
    if env_client_id and env_client_secret:
        logger.info("Using Copernicus credentials from environment variables")
        return {
            'client_id': env_client_id,
            'client_secret': env_client_secret,
            'service_url': 'https://dataspace.copernicus.eu',
            'auth_type': 'basic_auth',
        }

    # Try platform credentials from DB
    platform_creds = get_copernicus_credentials()

    if platform_creds:
        return platform_creds

    # Fallback to module-specific config
    if fallback_client_id and fallback_client_secret:
        logger.info("Using module-specific Copernicus credentials (fallback)")
        return {
            'client_id': fallback_client_id,
            'client_secret': fallback_client_secret,
            'service_url': 'https://dataspace.copernicus.eu',
            'auth_type': 'basic_auth'
        }
    
    return None

