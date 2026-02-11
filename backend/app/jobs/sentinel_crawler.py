#!/usr/bin/env python3
"""
Sentinel-2 Crawler Job (Phase F1)
---------------------------------
Daily cron job to:
1. Fetch AgriParcel entities from FIWARE Orion-LD.
2. Check for new Sentinel-2 L2A images via Copernicus API.
3. Download bands, convert to COG, and upload to MinIO.
4. Update AgriParcel with new scene metadata.
"""

import sys
import os
import asyncio
import logging
import tempfile
import shutil
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add backend directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.config import VegetationConfig as ConfigModel
from app.services.copernicus_client import CopernicusDataSpaceClient
from app.services.fiware_integration import FIWAREClient
from app.services.storage import create_storage_service

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("sentinel_crawler")


class SentinelCrawler:
    def __init__(self, db: Session):
        self.db = db
        self.config = self._get_config()
        self.storage = self._init_storage()
        self.copernicus = self._init_copernicus()
        self.fiware = self._init_fiware()
        
    def _get_config(self) -> ConfigModel:
        # Get first tenant config or default master
        config = self.db.query(ConfigModel).first()
        if not config:
            logger.warning("No VegetationConfig found in DB. Using defaults.")
            return ConfigModel() # Empty config
        return config

    def _init_storage(self):
        return create_storage_service(
            storage_type=self.config.storage_type or 'minio',
            default_bucket=self.config.storage_bucket or 'vegetation-data'
        )

    def _init_copernicus(self):
        # Decrypt secret logic should be here (omitted for MVP)
        client_id = self.config.copernicus_client_id or os.getenv("COPERNICUS_CLIENT_ID")
        client_secret = os.getenv("COPERNICUS_CLIENT_SECRET") # Env var generally safer
        return CopernicusDataSpaceClient(client_id, client_secret)

    def _init_fiware(self):
        url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
        return FIWAREClient(url, tenant_id="master") # Multi-tenant loop needed in future

    def get_parcels(self) -> List[Dict[str, Any]]:
        """Fetch all AgriParcels."""
        logger.info("Fetching AgriParcel entities from Orion-LD...")
        return self.fiware.query_entities(entity_type="AgriParcel", limit=1000)

    def process_parcels(self):
        parcels = self.get_parcels()
        logger.info(f"Processing {len(parcels)} parcels")
        
        for parcel in parcels:
            try:
                self.process_single(parcel)
            except Exception as e:
                logger.error(f"Failed to process parcel {parcel['id']}: {e}")

    def process_single(self, parcel: Dict[str, Any]):
        parcel_id = parcel['id']
        logger.info(f"Checking parcel {parcel_id}")

        # 1. Determine Search Range
        last_date_str = parcel.get("lastSceneDate", {}).get("value")
        if last_date_str:
            start_date = datetime.fromisoformat(last_date_str.replace("Z", "")).date() + timedelta(days=1)
        else:
            start_date = date.today() - timedelta(days=30) # Default lookback
        
        end_date = date.today()
        
        if start_date > end_date:
            logger.info("Up to date.")
            return

        # 2. Extract BBOX (Simplified from geometry)
        # TODO: Real geometry bbox extraction
        bbox = [-180, -90, 180, 90] # Placeholder

        # 3. Search Scenes
        scenes = self.copernicus.search_scenes(
            bbox=bbox, 
            start_date=start_date, 
            end_date=end_date,
            cloud_cover_max=float(self.config.cloud_coverage_threshold or 20.0)
        )
        
        if not scenes:
            logger.info("No new scenes found.")
            return

        for scene in scenes:
            self.ingest_scene(parcel_id, scene)

    def ingest_scene(self, parcel_id: str, scene: Dict[str, Any]):
        scene_id = scene['id']
        date_str = scene['sensing_date']
        
        logger.info(f"Ingesting scene {scene_id} for date {date_str}")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Download Bands (B04, B08 for NDVI)
            # Add SCL for cloud masking, B03/B11 etc as needed
            bands_local = self.copernicus.download_scene_bands(
                scene_id, ['B04', 'B08'], tmpdir
            )
            
            # Convert and Upload
            for band, path in bands_local.items():
                cog_path = path.replace(".tif", "_cog.tif")
                self.convert_to_cog(path, cog_path)
                
                remote_key = f"{parcel_id}/{date_str}/{band}.tif"
                self.storage.upload_file(cog_path, remote_key)
        
        # Update Entity
        self.fiware.update_entity({
            "id": parcel_id,
            "lastSceneDate": {"type": "Property", "value": date_str}
        })
        logger.info(f"Parcel {parcel_id} updated with scene {date_str}")

    def convert_to_cog(self, input_path: str, output_path: str):
        """Convert standard GeoTIFF to Cloud Optimized GeoTIFF."""
        try:
            from rio_cogeo.cogeo import cog_translate
            from rio_cogeo.profiles import cog_profiles

            dst_profile = cog_profiles.get("deflate")
            cog_translate(
                input_path,
                output_path,
                dst_profile,
                in_memory=True, # Use memory if file is small enough
                quiet=True
            )
        except ImportError:
            logger.error("rio-cogeo not installed. Uploading raw GeoTIFF.")
            shutil.copy(input_path, output_path)

def main():
    db = SessionLocal()
    try:
        crawler = SentinelCrawler(db)
        crawler.process_parcels()
    finally:
        db.close()

if __name__ == "__main__":
    main()
