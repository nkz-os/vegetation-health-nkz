"""
Phase F6: Management Zone Clustering (VRA)
Uses K-Means clustering on accumulated vegetation indices to define management zones.
Designed for N8N integration and Intelligence Module handoff.
"""

import logging
import math
import os
import uuid
import numpy as np
import rasterio
from rasterio.features import shapes
from scipy.cluster.vq import kmeans2, whiten
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import desc, text
from app.database import get_db_session
from app.models.jobs import VegetationJob
from app.models.config import VegetationConfig
from app.services.storage import create_storage_service, generate_tenant_bucket_name
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

    def vectorize_zones(self, label_img: np.ndarray, transform, ndvi_data: np.ndarray,
                        centroids: np.ndarray, crs=None):
        """
        Converts clustering result to ONE GeoJSON (Multi)Polygon feature per cluster,
        in WGS84, with properties: zone_id, cluster_id, zone_class, mean_value, area_ha.

        All connected patches of a cluster are dissolved (unary_union), patches smaller
        than one pixel are dropped, and the geometry is simplified with half a pixel
        tolerance (in raster CRS units) before reprojecting to EPSG:4326.
        mean_value / area_ha describe the whole cluster.

        crs: raster CRS (rasterio CRS or anything CRS.from_user_input accepts).
        ASSUMPTION: crs=None means EPSG:4326.

        zone_class is computed from the cluster centroid order:
          - Highest centroid -> "high", lowest -> "low", others -> "medium"
        """
        from shapely.geometry import shape as shapely_shape, mapping
        from shapely.ops import unary_union
        from rasterio.crs import CRS
        from rasterio.warp import transform_geom

        src_crs = CRS.from_user_input(crs) if crs is not None else CRS.from_epsg(4326)

        label_img_int = label_img.astype(np.int32)
        mask = label_img_int != -1

        # Sort zone IDs by their centroid (ascending NDVI)
        centroid_vals = centroids.flatten()
        sorted_zones = np.argsort(centroid_vals)
        n_zones = len(sorted_zones)
        zone_class_map: dict = {}
        for rank, zone_id in enumerate(sorted_zones):
            if n_zones == 1:
                zone_class_map[int(zone_id)] = "medium"
            elif rank == 0:
                zone_class_map[int(zone_id)] = "low"
            elif rank == n_zones - 1:
                zone_class_map[int(zone_id)] = "high"
            else:
                zone_class_map[int(zone_id)] = "medium"

        px_w, px_h = abs(transform[0]), abs(transform[4])
        px_area_crs = px_w * px_h
        tolerance = 0.5 * min(px_w, px_h)

        patches: Dict[int, list] = {}
        for geom, v in shapes(label_img_int, mask=mask, transform=transform):
            patches.setdefault(int(v), []).append(shapely_shape(geom))

        def _pixel_area_m2(lat_deg: float) -> float:
            if src_crs.is_projected:
                factor = src_crs.linear_units_factor[1]
                return px_area_crs * factor * factor
            return (px_w * 111320.0 * math.cos(math.radians(lat_deg))) * (px_h * 110574.0)

        features = []
        for zone_id in sorted(patches):
            parts = [g for g in patches[zone_id] if g.area >= px_area_crs * 0.999]
            if not parts:
                parts = [max(patches[zone_id], key=lambda g: g.area)]
            merged = unary_union(parts)
            simple = merged.simplify(tolerance, preserve_topology=True)
            if simple.is_empty:
                simple = merged
            wgs = transform_geom(src_crs, "EPSG:4326", mapping(simple), precision=7)

            zone_mask = label_img_int == zone_id
            zone_pixels = ndvi_data[zone_mask]
            mean_val = float(np.nanmean(zone_pixels)) if len(zone_pixels) > 0 else 0.0
            lat = shapely_shape(wgs).centroid.y
            area_ha = float(np.count_nonzero(zone_mask)) * _pixel_area_m2(lat) / 10000.0
            prescription_rate = 1.0 + (mean_val - 0.3) * 0.5  # simple linear mapping

            features.append({
                'properties': {
                    'zone_id': zone_id,
                    'cluster_id': zone_id,
                    'zone_class': zone_class_map.get(zone_id, "medium"),
                    'mean_value': round(mean_val, 4),
                    'area_ha': round(area_ha, 2),
                    'prescription_rate': round(max(0.5, min(2.0, prescription_rate)), 2),
                },
                'geometry': wgs,
            })
        return features

    def publish_zones(self, parcel_id: str, vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Create/update zone entities for one run; if any succeeded, delete stale zones.

        Returns {"run_id", "published", "failed"}.
        """
        run_id = str(uuid.uuid4())
        short = parcel_id.split(':')[-1]
        published = set()
        failed = 0
        for zone in vectors:
            props = zone['properties']
            zone_id = f"urn:ngsi-ld:AgriManagementZone:{short}:Z{props['zone_id']}"
            entity = {
                "id": zone_id,
                "type": "AgriManagementZone",
                "hasAgriParcel": {
                    "type": "Relationship",
                    "object": parcel_id
                },
                "location": {
                    "type": "GeoProperty",
                    "value": zone['geometry']
                },
                "zoneName": {
                    "type": "Property",
                    "value": f"Zone {props['zone_id'] + 1}"
                },
                "zoneId": {
                    "type": "Property",
                    "value": props['zone_id']
                },
                "zoneClass": {
                    "type": "Property",
                    "value": props['zone_class']
                },
                "prescriptionRate": {
                    "type": "Property",
                    "value": props['prescription_rate']
                },
                "areaHa": {
                    "type": "Property",
                    "value": props['area_ha']
                },
                "meanValue": {
                    "type": "Property",
                    "value": round(float(props['mean_value']), 4)
                },
                "zoningRunId": {
                    "type": "Property",
                    "value": run_id
                },
                "variableAttribute": {
                    "type": "Property",
                    "value": "NDVI"
                }
            }
            # POST; the client falls back to PATCH on 409 (zone already exists)
            try:
                ok = self.fiware.create_entity(entity)
            except Exception as e:
                logger.warning(f"Failed to publish zone {zone_id}: {e}")
                ok = False
            if ok:
                published.add(zone_id)
                logger.info(f"Created/Updated Zone: {zone_id}")
            else:
                failed += 1
                logger.warning(f"Failed to publish zone {zone_id}")

        if published:
            self._delete_stale_zones(parcel_id, short, published)
        else:
            logger.error(f"No zone published for {parcel_id}; stale zones kept")
        return {"run_id": run_id, "published": len(published), "failed": failed}

    def _delete_stale_zones(self, parcel_id: str, short: str, published: set) -> None:
        """Remove zones of this parcel left over from a previous run with more zones."""
        prefix = f"urn:ngsi-ld:AgriManagementZone:{short}:Z"
        try:
            existing = self.fiware.list_entity_ids(
                "AgriManagementZone", f'hasAgriParcel=="{parcel_id}"'
            )
        except Exception as e:
            logger.warning(f"Could not list previous zones for {parcel_id}: {e}")
            return
        for eid in existing:
            if not eid.startswith(prefix) or eid in published:
                continue
            try:
                self.fiware.delete_entity(eid)
                logger.info(f"Deleted stale zone: {eid}")
            except Exception as e:
                logger.warning(f"Failed to delete stale zone {eid}: {e}")

    async def generate_zones(self, parcel_id: str, n_zones: int=3, source_index: str = 'NDVI'):
        """
        Main workflow:
        1. Fetch latest index raster from VegetationJob results
        2. Cluster
        3. Create AgriManagementZone entities
        """
        logger.info(f"Generating {n_zones} management zones for {parcel_id} (source: {source_index})")

        db = next(get_db_session())
        try:
            # 1. Fetch latest raster from completed calculate_index jobs
            #    (VegetationIndexCache is dead — rasters live in VegetationJob.result)
            raster_job = (
                db.query(VegetationJob)
                .filter(
                    VegetationJob.tenant_id == self.tenant_id,
                    VegetationJob.entity_id == parcel_id,
                    VegetationJob.job_type == "calculate_index",
                    VegetationJob.status == "completed",
                    text("result->>'index_type' = :idx"),
                    VegetationJob.result["raster_path"].astext.isnot(None),
                )
                .params(idx=source_index)
                .order_by(desc(VegetationJob.created_at))
                .first()
            )

            if not raster_job or not raster_job.result:
                logger.warning(
                    "No %s raster found in jobs for parcel %s. Zones cannot be generated.",
                    source_index, parcel_id,
                )
                return {"status": "error", "message": f"No {source_index} data available for parcel"}

            raster_path = raster_job.result.get("raster_path")
            if not raster_path:
                return {"status": "error", "message": f"No raster path in {source_index} job result"}

            # Get storage config
            config = db.query(VegetationConfig).filter(VegetationConfig.tenant_id == self.tenant_id).first()
            storage_type = config.storage_type if config else 's3'
            bucket_name = os.getenv("VEGETATION_COG_BUCKET") or generate_tenant_bucket_name(self.tenant_id)
            storage = create_storage_service(storage_type=storage_type, default_bucket=bucket_name)

            # Download raster locally for processing
            local_path = f"/tmp/zoning_{parcel_id.replace(':', '_')}.tif"
            try:
                storage.download_file(raster_path, local_path)
            except Exception as e:
                logger.error(f"Failed to download raster: {e}")
                return {"status": "error", "message": "Failed to retrieve raster data"}

            # Load raster
            with rasterio.open(local_path) as src:
                ndvi_data = src.read(1)
                transform = src.transform
                raster_crs = src.crs

            # 2. Cluster
            labels, centroids = self.cluster_raster(ndvi_data, n_zones)
            
            # Clean up
            if os.path.exists(local_path):
                os.remove(local_path)

            if labels is None:
                logger.warning("Clustering failed or yielded no results")
                return {"status": "error", "message": "Clustering failed"}

            # 3. Vectorize — pass NDVI data for per-zone statistics
            vectors = self.vectorize_zones(labels, transform, ndvi_data, centroids, raster_crs)

            if not vectors:
                logger.warning("No zones generated")
                return {"status": "error", "message": "No zones generated after vectorization"}
        finally:
            db.close()

        # 4. Upload to Orion — enriched with zoneClass and prescriptionRate
        outcome = self.publish_zones(parcel_id, vectors)
        if outcome["published"] == 0:
            return {"status": "error", "message": "Failed to publish any management zone to the context broker"}

        logger.info(f"Successfully generated {len(vectors)} zones for {parcel_id}")

        # Return N8N-friendly response with GeoJSON for API retrieval
        geojson = {
            "type": "FeatureCollection",
            "features": vectors,
        }
        return {
            "status": "success",
            "parcel_id": parcel_id,
            "zones_created": outcome["published"],
            "zones_failed": outcome["failed"],
            "geojson": geojson,
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
            parameters: Additional parameters (n_zones, source_index, etc.)

        Returns:
            N8N-compatible result dictionary
        """
        import asyncio

        n_zones = parameters.get('n_zones', 3)
        source_index = parameters.get('source_index', 'NDVI')

        # Run the async method synchronously
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(self.generate_zones(parcel_id, n_zones, source_index))
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
