"""
Copernicus Data Space Ecosystem client for downloading Sentinel-2 data.
"""

import logging
import os
from typing import List, Dict, Any, Optional
from datetime import date, datetime, timedelta
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth
import json

logger = logging.getLogger(__name__)


class CopernicusDataSpaceClient:
    """Client for Copernicus Data Space Ecosystem API."""
    
    BASE_URL = "https://dataspace.copernicus.eu"
    # Identity API for token generation
    OAUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    # STAC API for scene search
    CATALOG_URL = "https://stac.dataspace.copernicus.eu/v1"
    
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        """Initialize Copernicus Data Space client.
        
        Args:
            client_id: OAuth2 client ID (optional if using platform credentials)
            client_secret: OAuth2 client secret (optional if using platform credentials)
            
        Note:
            If client_id and client_secret are not provided, the client will attempt
            to retrieve credentials from the platform's central storage via
            get_copernicus_credentials(). This allows modules to use platform-managed
            credentials without requiring user configuration.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        
        # If credentials not provided, they should be set via set_credentials() before use
        if not client_id or not client_secret:
            logger.info("Copernicus client initialized without credentials - will use platform credentials")
    
    def set_credentials(self, client_id: str, client_secret: str):
        """Set credentials for the client (useful when loading from platform).
        
        Args:
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret
        """
        self.client_id = client_id
        self.client_secret = client_secret
        # Clear cached token when credentials change
        self.access_token = None
        self.token_expires_at = None
    
    def _get_access_token(self) -> str:
        """Get OAuth2 access token (with caching).
        
        Returns:
            Access token string
            
        Raises:
            ValueError: If credentials are not set
        """
        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Copernicus credentials not set. "
                "Either provide them in __init__ or use set_credentials() method. "
                "You can also use get_copernicus_credentials() from platform_credentials service."
            )
        
        # Check if token is still valid (with 5 min buffer)
        if self.access_token and self.token_expires_at:
            if datetime.utcnow() < (self.token_expires_at - timedelta(minutes=5)):
                return self.access_token
        
        # Request new token
        try:
            response = requests.post(
                self.OAUTH_URL,
                auth=HTTPBasicAuth(self.client_id, self.client_secret),
                data={
                    'grant_type': 'client_credentials'
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            
            # Calculate expiration (default to 1 hour if not provided)
            expires_in = token_data.get('expires_in', 3600)
            self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            logger.info("Successfully obtained Copernicus access token")
            return self.access_token
            
        except requests.RequestException as e:
            logger.error(f"Failed to obtain access token: {str(e)}")
            raise Exception(f"Authentication failed: {str(e)}")
    
    def search_scenes(
        self,
        bbox: Optional[List[float]] = None,  # [min_lon, min_lat, max_lon, max_lat]
        intersects: Optional[Dict[str, Any]] = None,  # GeoJSON geometry for strict intersection
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        cloud_cover_max: Optional[float] = None,
        cloud_cover_lte: Optional[float] = None,
        product_type: str = "S2MSI2A",
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Search for Sentinel-2 scenes.
        
        Phase 3 SOTA: Use intersects (exact parcel geometry) + cloud_cover_lte (e.g. 60)
        to avoid discarding useful scenes where the tile is partly cloudy but the parcel is clear.
        
        Args:
            bbox: Bounding box [min_lon, min_lat, max_lon, max_lat] (use when intersects not provided)
            intersects: GeoJSON geometry (Polygon) for strict intersection; preferred for subscriptions
            start_date: Start date for search
            end_date: End date for search
            cloud_cover_max: Max cloud (lt) when using bbox
            cloud_cover_lte: Max cloud (lte) when using intersects; default 60 for macro filter
            product_type: Product type (default: S2MSI2A for L2A)
            limit: Maximum number of results
            
        Returns:
            List of scene metadata dictionaries with id, sensing_date, datetime, assets, geometry, etc.
        """
        token = self._get_access_token()
        start_date = start_date or date.today() - timedelta(days=30)
        end_date = end_date or date.today()

        if intersects:
            query = {
                "collections": ["sentinel-s2-l2a-cogs"],
                "intersects": intersects,
                "datetime": f"{start_date.isoformat()}/{end_date.isoformat()}",
                "limit": limit,
                "query": {
                    "eo:cloud_cover": {"lte": cloud_cover_lte if cloud_cover_lte is not None else 60}
                }
            }
        else:
            bbox = bbox or [-180, -90, 180, 90]
            query = {
                "collections": ["sentinel-s2-l2a-cogs"],
                "bbox": bbox,
                "datetime": f"{start_date.isoformat()}/{end_date.isoformat()}",
                "limit": limit,
                "query": {
                    "eo:cloud_cover": {"lt": cloud_cover_max if cloud_cover_max is not None else 20.0}
                }
            }

        try:
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }
            response = requests.post(
                f"{self.CATALOG_URL}/search",
                json=query,
                headers=headers
            )
            response.raise_for_status()
            results = response.json()
            scenes = []
            for feature in results.get('features', []):
                dt_raw = feature['properties'].get('datetime') or ''
                scene = {
                    'id': feature['id'],
                    'sensing_date': dt_raw.split('T')[0] if dt_raw else '',
                    'datetime': dt_raw if dt_raw else None,
                    'cloud_cover': feature['properties'].get('eo:cloud_cover', 0),
                    'geometry': feature.get('geometry'),
                    'assets': feature.get('assets', {}),
                    'links': feature.get('links', [])
                }
                scenes.append(scene)
            logger.info(f"Found {len(scenes)} scenes matching criteria")
            return scenes
        except requests.RequestException as e:
            logger.error(f"Failed to search scenes: {str(e)}")
            raise Exception(f"Scene search failed: {str(e)}")

    def get_scene_item(self, scene_id: str) -> Dict[str, Any]:
        """Fetch a single STAC item by id (for SCL validation and asset URLs)."""
        token = self._get_access_token()
        url = f"{self.CATALOG_URL}/collections/sentinel-s2-l2a-cogs/items/{scene_id}"
        headers = {'Authorization': f'Bearer {token}'}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        feature = response.json()
        dt_raw = feature.get('properties', {}).get('datetime') or ''
        return {
            'id': feature['id'],
            'sensing_date': dt_raw.split('T')[0] if dt_raw else '',
            'datetime': dt_raw if dt_raw else None,
            'cloud_cover': feature.get('properties', {}).get('eo:cloud_cover', 0),
            'geometry': feature.get('geometry'),
            'assets': feature.get('assets', {}),
            'links': feature.get('links', []),
        }
    
    def download_band(
        self,
        scene_id: str,
        band: str,  # e.g., "B04", "B08", "SCL"
        output_path: str
    ) -> str:
        """Download a specific band from a scene.
        
        Args:
            scene_id: Sentinel-2 scene ID
            band: Band name (B02, B03, B04, B08, SCL, etc.)
            output_path: Local path to save the file
            
        Returns:
            Path to downloaded file
        """
        token = self._get_access_token()
        scene = self.get_scene_item(scene_id)
        assets = scene.get('assets', {})
        band_asset = assets.get(band) or assets.get(f"{band}.tif") or (assets.get(f"B{band}") if band.startswith("B") else None)
        
        if not band_asset:
            raise ValueError(f"Band {band} not found in scene {scene_id}")
        
        # Get download URL
        download_url = band_asset.get('href')
        if not download_url:
            raise ValueError(f"No download URL for band {band}")
        
        # Download file
        try:
            headers = {'Authorization': f'Bearer {token}'}
            
            response = requests.get(download_url, headers=headers, stream=True)
            response.raise_for_status()
            
            # Ensure output directory exists
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Download with progress
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            
            logger.info(f"Downloaded band {band} from scene {scene_id} to {output_path}")
            return output_path
            
        except requests.RequestException as e:
            logger.error(f"Failed to download band {band}: {str(e)}")
            raise Exception(f"Download failed: {str(e)}")
    
    def download_scene_bands(
        self,
        scene_id: str,
        bands: List[str],
        output_dir: str
    ) -> Dict[str, str]:
        """Download multiple bands from a scene.
        
        Args:
            scene_id: Sentinel-2 scene ID
            bands: List of band names to download
            output_dir: Directory to save bands
            
        Returns:
            Dictionary mapping band names to file paths
        """
        band_paths = {}
        
        for band in bands:
            output_path = os.path.join(output_dir, f"{scene_id}_{band}.tif")
            try:
                downloaded_path = self.download_band(scene_id, band, output_path)
                band_paths[band] = downloaded_path
            except Exception as e:
                logger.error(f"Failed to download band {band}: {str(e)}")
                # Continue with other bands
        
        return band_paths

