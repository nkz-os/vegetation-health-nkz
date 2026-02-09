#!/usr/bin/env python3
"""
Migration runner script with PostgreSQL Advisory Locks.
Executes SQL migrations in order with race condition protection.
"""

import os
import sys
import logging
import time
from pathlib import Path
from typing import List, Tuple
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from psycopg2 import sql

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Migration lock ID (unique identifier for advisory lock)
MIGRATION_LOCK_ID = 0x4E4B5A4D  # "NKZM" in hex (Nekazari Module)

# Maximum wait time for lock (seconds)
LOCK_TIMEOUT = 300  # 5 minutes

# Retry interval (seconds)
RETRY_INTERVAL = 5


def get_migration_files(migrations_dir: Path) -> List[Tuple[int, Path]]:
    """Get migration files sorted by number.
    
    Args:
        migrations_dir: Path to migrations directory
        
    Returns:
        List of (migration_number, file_path) tuples, sorted
    """
    migrations = []
    
    for file_path in sorted(migrations_dir.glob('*.sql')):
        # Extract migration number from filename (e.g., "001_*.sql" -> 1)
        filename = file_path.name
        if filename.startswith('rollback'):
            continue  # Skip rollback scripts
        
        try:
            # Extract number from filename (format: NNN_description.sql)
            parts = filename.split('_', 1)
            if parts and parts[0].isdigit():
                migration_num = int(parts[0])
                migrations.append((migration_num, file_path))
        except (ValueError, IndexError):
            logger.warning(f"Could not parse migration number from {filename}, skipping")
            continue
    
    return sorted(migrations)


def check_migration_applied(conn, migration_num: int) -> bool:
    """Check if a migration has already been applied.
    
    We check by looking for a table that should exist after the migration.
    This is a simple heuristic - in production, you might want a migrations table.
    
    Args:
        conn: Database connection
        migration_num: Migration number
        
    Returns:
        True if migration appears to be applied
    """
    cursor = conn.cursor()
    
    try:
        if migration_num == 1:
            # Migration 001 creates vegetation_config table
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'vegetation_config'
                );
            """)
            return cursor.fetchone()[0]
        elif migration_num == 2:
            # Migration 002 creates vegetation_plan_limits table
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'vegetation_plan_limits'
                );
            """)
            return cursor.fetchone()[0]
        elif migration_num == 3:
            # Migration 003 creates global_scene_cache table
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'global_scene_cache'
                );
            """)
            return cursor.fetchone()[0]
        elif migration_num == 4:
            # Migration 004 creates vegetation_subscriptions table
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'vegetation_subscriptions'
                );
            """)
            return cursor.fetchone()[0]
        else:
            # Unknown migration - assume not applied
            return False
    except Exception as e:
        logger.warning(f"Error checking migration {migration_num}: {str(e)}")
        return False
    finally:
        cursor.close()


def acquire_advisory_lock(conn) -> bool:
    """Acquire PostgreSQL advisory lock.
    
    Args:
        conn: Database connection
        
    Returns:
        True if lock acquired, False otherwise
    """
    cursor = conn.cursor()
    
    try:
        # Try to acquire lock (non-blocking)
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s)",
            (MIGRATION_LOCK_ID,)
        )
        acquired = cursor.fetchone()[0]
        
        if acquired:
            logger.info(f"Acquired advisory lock {hex(MIGRATION_LOCK_ID)}")
        else:
            logger.warning(f"Could not acquire lock {hex(MIGRATION_LOCK_ID)} - another process is running migrations")
        
        return acquired
    except Exception as e:
        logger.error(f"Error acquiring advisory lock: {str(e)}")
        return False
    finally:
        cursor.close()


def release_advisory_lock(conn) -> None:
    """Release PostgreSQL advisory lock.
    
    Args:
        conn: Database connection
    """
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "SELECT pg_advisory_unlock(%s)",
            (MIGRATION_LOCK_ID,)
        )
        released = cursor.fetchone()[0]
        
        if released:
            logger.info(f"Released advisory lock {hex(MIGRATION_LOCK_ID)}")
        else:
            logger.warning(f"Lock {hex(MIGRATION_LOCK_ID)} was not held by this process")
    except Exception as e:
        logger.error(f"Error releasing advisory lock: {str(e)}")
    finally:
        cursor.close()


def run_migration(conn, migration_file: Path) -> bool:
    """Execute a single migration file.
    
    Args:
        conn: Database connection
        migration_file: Path to migration SQL file
        
    Returns:
        True if successful, False otherwise
    """
    logger.info(f"Running migration: {migration_file.name}")
    
    try:
        # Read migration SQL
        with open(migration_file, 'r', encoding='utf-8') as f:
            migration_sql = f.read()
        
        # Execute migration
        cursor = conn.cursor()
        cursor.execute(migration_sql)
        conn.commit()
        cursor.close()
        
        logger.info(f"✓ Migration {migration_file.name} completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"✗ Migration {migration_file.name} failed: {str(e)}")
        conn.rollback()
        return False


def main():
    """Main migration runner."""
    # Get database URL from environment
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable not set")
        sys.exit(1)
    
    # Get migrations directory
    script_dir = Path(__file__).parent
    migrations_dir = script_dir.parent / 'migrations'
    
    if not migrations_dir.exists():
        logger.error(f"Migrations directory not found: {migrations_dir}")
        sys.exit(1)
    
    logger.info(f"Migration runner starting...")
    logger.info(f"Migrations directory: {migrations_dir}")
    
    # DEBUG: List all files in migrations directory
    try:
        files = list(migrations_dir.glob('*'))
        logger.info(f"Files in migrations dir: {[f.name for f in files]}")
    except Exception as e:
        logger.error(f"Error listing files: {e}")
    
    # Get migration files
    migrations = get_migration_files(migrations_dir)
    
    if not migrations:
        logger.warning("No migration files found")
        return
    
    logger.info(f"Found {len(migrations)} migration(s)")
    
    # Connect to database
    try:
        conn = psycopg2.connect(database_url)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    except Exception as e:
        logger.error(f"Failed to connect to database: {str(e)}")
        sys.exit(1)
    
    try:
        # Try to acquire lock (with retries)
        lock_acquired = False
        start_time = time.time()
        
        while not lock_acquired:
            lock_acquired = acquire_advisory_lock(conn)
            
            if not lock_acquired:
                elapsed = time.time() - start_time
                if elapsed > LOCK_TIMEOUT:
                    logger.error(f"Timeout waiting for migration lock after {LOCK_TIMEOUT}s")
                    sys.exit(1)
                
                logger.info(f"Waiting for migration lock... (retrying in {RETRY_INTERVAL}s)")
                time.sleep(RETRY_INTERVAL)
        
        # Run migrations
        success_count = 0
        skipped_count = 0
        
        for migration_num, migration_file in migrations:
            # Check if already applied
            if check_migration_applied(conn, migration_num):
                logger.info(f"⏭ Migration {migration_num} ({migration_file.name}) already applied, skipping")
                skipped_count += 1
                continue
            
            # Run migration
            if run_migration(conn, migration_file):
                success_count += 1
            else:
                logger.error(f"Migration {migration_num} failed, stopping")
                sys.exit(1)
        
        logger.info(f"Migration runner completed: {success_count} applied, {skipped_count} skipped")
        
    finally:
        # Always release lock
        release_advisory_lock(conn)
        conn.close()


if __name__ == '__main__':
    main()



