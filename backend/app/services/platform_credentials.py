"""
Service to retrieve external API credentials from the platform's central storage.
This allows modules to use platform-managed credentials without requiring user configuration.
"""

import os
import logging
from typing import Optional, Dict
import psycopg2

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
    Get platform Copernicus CDSE credentials from environment variables.

    The platform admin panel writes the OAuth client id/secret pair to the
    shared K8s secret `copernicus-cdse-secret`, which this deployment
    exposes as the COPERNICUS_CLIENT_ID / COPERNICUS_CLIENT_SECRET env vars.

    NOTE: this deliberately does NOT read `external_api_credentials` in the
    platform DB. That table's `password_encrypted` column is a one-way
    salted SHA-256 digest (services/common/hash_utils.py:
    salted_credential_digest) — an audit fingerprint, not reversible
    ciphertext. It can never be used to authenticate against Copernicus and
    must not be treated as a credential source.

    Args:
        db: Unused, kept for call-site signature compatibility.

    Returns:
        Dictionary with 'client_id', 'client_secret', 'service_url',
        'auth_type', or None if either env var is unset/empty.
    """
    client_id = os.getenv('COPERNICUS_CLIENT_ID', '')
    client_secret = os.getenv('COPERNICUS_CLIENT_SECRET', '')

    if not client_id or not client_secret:
        logger.debug("Platform Copernicus credentials not configured (env vars unset)")
        return None

    return {
        'client_id': client_id,
        'client_secret': client_secret,
        'service_url': 'https://dataspace.copernicus.eu',
        'auth_type': 'basic_auth',
    }


def get_copernicus_credentials_with_fallback(
    db=None,
    fallback_client_id: Optional[str] = None,
    fallback_client_secret: Optional[str] = None
) -> Optional[Dict[str, str]]:
    """
    Get Copernicus credentials with fallback to module-specific config.

    First tries platform credentials (env vars). If not available, falls
    back to provided module-specific credentials.

    Args:
        db: Database session
        fallback_client_id: Fallback client ID (from module config)
        fallback_client_secret: Fallback client secret (from module config)

    Returns:
        Dictionary with credentials or None if neither source available
    """
    # Try platform credentials (env vars)
    platform_creds = get_copernicus_credentials()
    if platform_creds:
        logger.info("Using Copernicus credentials from platform (env)")
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

