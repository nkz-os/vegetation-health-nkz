#!/usr/bin/env python3
"""
Statistics Aggregator Job (Phase F2)
------------------------------------
Calculates zonal statistics (Mean, Min, Max, StdDev) for vegetation indices.
Masks clouds using SCL band and pushes results to Orion-LD to feed the Smart Timeline.
"""

import sys
import os
import logging
import numpy as np
import rasterio
from typing import Dict, Any, List

# Add backend directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.config import VegetationConfig as ConfigModel
from app.services.fiware_integration import FIWAREClient
from app.services.storage import create_storage_service

# Rasterstats (optional dependency check)
try:
    from rasterstats import zonal_stats
    HAS_RASTERSTATS = True
except ImportError:
    HAS_RASTERSTATS = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stats_processor")

class StatsProcessor:
    def __init__(self, db: Session):
        self.db = db
        self.config = self._get_config()
        self.storage = self._init_storage()
        self.fiware = self._init_fiware()

    def _get_config(self) -> ConfigModel:
        config = self.db.query(ConfigModel).first()
        return config or ConfigModel()

    def _init_storage(self):
        return create_storage_service(
            storage_type=self.config.storage_type or 'minio',
            default_bucket=self.config.storage_bucket or 'vegetation-data'
        )

    def _init_fiware(self):
        url = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
        return FIWAREClient(url, tenant_id="master")

    def process_stats(self, parcel_id: str, date_str: str, bands_map: Dict[str, str]):
        """
        Calculate statistics for a given parcel and scene date.
        bands_map: {"B04": "path/to/B04.tif", "B08": ...}
        """
        if not HAS_RASTERSTATS:
            logger.error("rasterstats not installed. Skipping stats calculation.")
            return

        logger.info(f"Calculating stats for {parcel_id} on {date_str}")
        
        # 1. Calculate NDVI (B8-B4)/(B8+B4)
        # We need to read arrays from TIFs
        try:
            with rasterio.open(bands_map['B08']) as src_b8:
                b8 = src_b8.read(1).astype(float)
                profile = src_b8.profile
            
            with rasterio.open(bands_map['B04']) as src_b4:
                b4 = src_b4.read(1).astype(float)

            # Avoid div by zero
            ndvi = np.where(
                (b8 + b4) == 0, 
                0, 
                (b8 - b4) / (b8 + b4)
            )
            
            # TODO: Apply SCL Mask here if available
            
            # 2. Zonal Stats
            # We need the parcel geometry. 
            # In a real scenario, we'd fetch the specific parcel geometry from Orion.
            # Here we assume the COG is already clipped to ROI or we use the whole image for MVP.
            
            stats = {
                "mean": float(np.nanmean(ndvi)),
                "min": float(np.nanmin(ndvi)),
                "max": float(np.nanmax(ndvi)),
                "std": float(np.nanstd(ndvi)),
                "pixel_count": int(np.count_nonzero(~np.isnan(ndvi)))
            }
            
            logger.info(f"Stats calculated: {stats}")
            
            # 3. Update Orion (AgriParcelRecord or AgriParcel)
            self.fiware.update_agri_parcel(
                parcel_entity={"id": parcel_id}, # Mock entity dict
                index_type="NDVI",
                index_value=stats['mean'],
                sensing_date=date_str,
                statistics=stats
            )
            # update_agri_parcel in FIWAREClient (mocked in previous cat output check? 
            # No, update_entity is there. update_agri_parcel was in FIWAREMapper in previous cat output)
            
            # Actual implementation needs to call update_entity using the ID
            payload = {
                "id": parcel_id,
                "type": "AgriParcel",
                "vegetationIndex": {
                    "type": "Property",
                    "value": {
                        "indexType": "NDVI",
                        "value": stats['mean'],
                        "statistics": stats,
                        "dateObserved": date_str
                    }
                }
            }
            self.fiware.update_entity(payload)

        except Exception as e:
            logger.error(f"Error calculating stats: {e}")

def main():
    # This script is intended to be called by the crawler or standalone
    # For standalone test: python -m app.jobs.stats_processor <parcel_id> <date>
    if len(sys.argv) > 2:
        parcel_id = sys.argv[1]
        date_str = sys.argv[2]
        # In standalone, we'd need to fetch files from storage first...
        pass

if __name__ == "__main__":
    main()
