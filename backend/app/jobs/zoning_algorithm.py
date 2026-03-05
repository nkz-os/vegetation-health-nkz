"""
Phase F6: Management Zone Clustering (VRA)
Uses K-Means clustering on accumulated vegetation indices to define management zones.
Designed for N8N integration and Intelligence Module handoff.
"""

import logging
import os
import numpy as np
import rasterio
from rasterio.features import shapes
from scipy.cluster.vq import kmeans2, whiten
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session
from app.database import get_db_session
from app.models.indices import VegetationIndexCache
from app.models.config import VegetationConfig
from app.services.storage import create_storage_service, generate_tenant_bucket_name
from app.services.fiware_integration import FIWAREClient

from app.services.fiware_integration import FIWAREClient

logger = logging.getLogger(__name__)


class ZoningAlgorithm:
    """
    Management Zone Clustering (VRA) Algorithm.
    
    Integration Points:
    - N8N: Can be triggered via webhook, results are webhook-friendly
    - Intelligence Module: Prepares data for advanced ML clustering
    - Nekazari Platform: Updates AgriManagementZone entities in Orion-LD
    """
    
    def __init__(self, orion_url: Optional[str] = None, tenant_id: str = "master"):
        url = orion_url or os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")
        self.fiware = FIWAREClient(url, tenant_id=tenant_id)
        self.tenant_id = tenant_id

    def cluster_raster(self, raster_data: np.ndarray, n_clusters: int=3):
        """
        Clusters a raster into n zones.
        """
        # Flatten and remove NaNs/Masked values
        valid_mask = ~np.isnan(raster_data) & (raster_data > -1) # Basic NDVI validity
        valid_pixels = raster_data[valid_mask]

        if len(valid_pixels) < n_clusters:
            logger.warning("Not enough valid pixels for clustering")
            return None, None

        # Reshape for scipy (N, 1)
        observations = valid_pixels.reshape(-1, 1)
        
        # Whiten (normalize)
        whitened = whiten(observations)
        
        # K-Means
        centroids, labels = kmeans2(whitened, n_clusters, minit='points')
        
        # Reconstruct label image
        output_labels = np.full(raster_data.shape, -1, dtype=int)
        output_labels[valid_mask] = labels
        
        return output_labels, centroids

    def vectorize_zones(self, label_img: np.ndarray, transform):
        """
        Converts clustering result to GeoJSON polygons.
        """
        # Ensure label_img is int32 for shapes
        label_img_int = label_img.astype(np.int32)
        mask = label_img_int != -1
        
        results = (
            {'properties': {'zone_id': int(v)}, 'geometry': s}
            for i, (s, v) 
            in enumerate(shapes(label_img_int, mask=mask, transform=transform))
        )
        return list(results)

    async def generate_zones(self, parcel_id: str, n_zones: int=3):
        """
        Main workflow:
        1. Fetch latest NDVI Raster URL from Orion/MinIO (now using VegetationIndexCache)
        2. Cluster
        3. Create AgriManagementZone entities
        """
        logger.info(f"Generating {n_zones} management zones for {parcel_id}")
        
        db = next(get_db_session())
        try:
            # 1. Fetch latest NDVI from cache
            cache_entry = (
                db.query(VegetationIndexCache)
                .filter(
                    VegetationIndexCache.tenant_id == self.tenant_id,
                    VegetationIndexCache.entity_id == parcel_id,
                    VegetationIndexCache.index_type == 'NDVI'
                )
                .order_by(VegetationIndexCache.calculated_at.desc())
                .first()
            )
            
            if not cache_entry or not cache_entry.result_raster_path:
                logger.warning(f"No NDVI raster found in cache for parcel {parcel_id}. Zones cannot be generated.")
                return {"status": "error", "message": "No NDVI data available for parcel"}

            # Get storage config
            config = db.query(VegetationConfig).filter(VegetationConfig.tenant_id == self.tenant_id).first()
            storage_type = config.storage_type if config else 's3'
            bucket_name = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(self.tenant_id)
            storage = create_storage_service(storage_type=storage_type, default_bucket=bucket_name)

            # Download raster locally for processing
            local_path = f"/tmp/zoning_{parcel_id.replace(':', '_')}.tif"
            try:
                storage.download_file(cache_entry.result_raster_path, local_path)
            except Exception as e:
                logger.error(f"Failed to download raster: {e}")
                return {"status": "error", "message": "Failed to retrieve raster data"}

            # Load raster
            with rasterio.open(local_path) as src:
                ndvi_data = src.read(1)
                transform = src.transform

            # 2. Cluster
            labels, centroids = self.cluster_raster(ndvi_data, n_zones)
            
            # Clean up
            if os.path.exists(local_path):
                os.remove(local_path)

            if labels is None:
                logger.warning("Clustering failed or yielded no results")
                return {"status": "error", "message": "Clustering failed"}

            # 3. Vectorize
            vectors = self.vectorize_zones(labels, transform)
            
            if not vectors:
                logger.warning("No zones generated")
                return {"status": "error", "message": "No zones generated after vectorization"}
        finally:
            db.close()

        # 4. Upload to Orion
        for zone in vectors:
            zone_id = f"urn:ngsi-ld:AgriManagementZone:{parcel_id.split(':')[-1]}:Z{zone['properties']['zone_id']}"
            entity = {
                "id": zone_id,
                "type": "AgriManagementZone",
                "refAgriParcel": {
                    "type": "Relationship",
                    "object": parcel_id
                },
                "location": {
                    "type": "GeoProperty",
                    "value": zone['geometry']
                },
                "zoneName": {
                    "type": "Property",
                    "value": f"Zone {zone['properties']['zone_id'] + 1}"
                },
                "variableAttribute": {
                    "type": "Property",
                    "value": "NDVI"
                }
            }
            # Create or Update via FIWARE Client
            try:
                self.fiware.update_entity(entity)
                logger.info(f"Created/Updated Zone: {zone_id}")
            except Exception as e:
                logger.warning(f"Failed to update zone {zone_id}: {e}")

        logger.info(f"Successfully generated {len(vectors)} zones for {parcel_id}")
        
        # Return N8N-friendly response
        return {
            "status": "success",
            "parcel_id": parcel_id,
            "zones_created": len(vectors),
            "webhook_metadata": {
                "intelligence_module_compatible": True,
                "n8n_ready": True,
                "can_delegate_to": ["intelligence-module", "n8n-workflow"]
            }
        }

    def execute(self, parcel_id: str, scene_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synchronous execution wrapper for background task.
        
        Args:
            parcel_id: Target parcel ID
            scene_id: Scene to use for clustering (can be same as parcel_id for latest)
            parameters: Additional parameters (n_zones, etc.)
            
        Returns:
            N8N-compatible result dictionary
        """
        import asyncio
        
        n_zones = parameters.get('n_zones', 3)
        
        # Run the async method synchronously
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(self.generate_zones(parcel_id, n_zones))
        return result or {"status": "no_zones_generated", "parcel_id": parcel_id}

    def prepare_for_intelligence_module(self, parcel_id: str, raster_data: np.ndarray) -> Dict[str, Any]:
        """
        Prepare data for handoff to Intelligence Module.
        
        The Intelligence Module can perform more sophisticated clustering
        (e.g., deep learning, multi-temporal analysis).
        
        Returns:
            Dictionary with data ready for Intelligence Module API
        """
        valid_mask = ~np.isnan(raster_data) & (raster_data > -1)
        valid_pixels = raster_data[valid_mask].tolist()
        
        return {
            "task_type": "advanced_clustering",
            "parcel_id": parcel_id,
            "data": {
                "pixel_values": valid_pixels,
                "shape": raster_data.shape,
                "valid_pixel_count": len(valid_pixels)
            },
            "suggested_algorithms": ["dbscan", "spectral_clustering", "deep_clustering"],
            "callback_endpoint": "/api/vegetation/jobs/zoning/callback"
        }


# Global instance for ORION_URL reference in main.py
ORION_URL = os.getenv("FIWARE_CONTEXT_BROKER_URL", "http://orion-ld-service:1026")


if __name__ == "__main__":
    algo = ZoningAlgorithm()
