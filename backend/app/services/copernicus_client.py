"""
Copernicus Data Space Ecosystem client for downloading Sentinel-2 data.
SOTA Implementation: Uses STAC for discovery and S3 (boto3) for high-performance downloads.
"""

import logging
import os
from typing import List, Dict, Any, Optional
from datetime import date, datetime, timedelta
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth
import boto3
from botocore.config import Config
import json

logger = logging.getLogger(__name__)


class CopernicusDataSpaceClient:
    """Client for Copernicus Data Space Ecosystem API."""
    
    BASE_URL = "https://dataspace.copernicus.eu"
    # Identity API for token generation (used for STAC catalog)
    OAUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    # STAC API for scene search
    CATALOG_URL = "https://stac.dataspace.copernicus.eu/v1"
    # S3 Endpoint for downloads
    S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
    
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        """Initialize Copernicus Data Space client."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        self._s3_client = None
        
        if not client_id or not client_secret:
            logger.info("Copernicus client initialized without credentials - will use platform credentials")

    def set_credentials(self, client_id: str, client_secret: str):
        """Set credentials for the client."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expires_at = None
        self._s3_client = None # Reset S3 client to force re-init with new creds

    def _get_access_token(self) -> str:
        """Get OAuth2 access token for STAC catalog (with caching)."""
        if not self.client_id or not self.client_secret:
            raise ValueError("Copernicus credentials not set.")
        
        if self.access_token and self.token_expires_at:
            if datetime.utcnow() < (self.token_expires_at - timedelta(minutes=5)):
                return self.access_token
        
        try:
            response = requests.post(
                self.OAUTH_URL,
                auth=HTTPBasicAuth(self.client_id, self.client_secret),
                data={'grant_type': 'client_credentials'},
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data['access_token']
            expires_in = token_data.get('expires_in', 3600)
            self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            return self.access_token
        except requests.RequestException as e:
            logger.error(f"Failed to obtain access token: {str(e)}")
            raise Exception(f"Authentication failed: {str(e)}")

    def _get_s3_client(self):
        """Initialize and return a boto3 S3 client for Copernicus eodata.

        S3 credentials are separate from OAuth credentials in CDSE.
        Priority: AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env vars > OAuth creds (fallback).
        """
        if self._s3_client:
            return self._s3_client

        s3_access_key = os.getenv('AWS_ACCESS_KEY_ID')
        s3_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')

        if not s3_access_key or not s3_secret_key:
            if not self.client_id or not self.client_secret:
                raise ValueError("No S3 credentials available. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env vars.")
            logger.warning("No dedicated S3 credentials found, falling back to OAuth credentials (may fail with 403)")
            s3_access_key = self.client_id
            s3_secret_key = self.client_secret

        self._s3_client = boto3.client(
            "s3",
            endpoint_url=self.S3_ENDPOINT,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
            region_name="default",
            config=Config(s3={"addressing_style": "path"})
        )
        return self._s3_client

    def search_scenes(
        self,
        bbox: Optional[List[float]] = None,
        intersects: Optional[Dict[str, Any]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        cloud_cover_max: Optional[float] = None,
        cloud_cover_lte: Optional[float] = None,
        product_type: str = "S2MSI2A",
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Search for Sentinel-2 scenes using STAC API."""
        token = self._get_access_token()
        start_date = start_date or date.today() - timedelta(days=30)
        end_date = end_date or date.today()
        
        # SOTA: Full ISO datetime format required by CDSE STAC
        datetime_filter = f"{start_date.isoformat()}T00:00:00Z/{end_date.isoformat()}T23:59:59Z"

        if intersects:
            query = {
                "collections": ["sentinel-2-l2a"],
                "intersects": intersects,
                "datetime": datetime_filter,
                "limit": limit,
                "query": {
                    "eo:cloud_cover": {"lte": cloud_cover_lte if cloud_cover_lte is not None else 60}
                }
            }
        else:
            bbox = bbox or [-180, -90, 180, 90]
            query = {
                "collections": ["sentinel-2-l2a"],
                "bbox": bbox,
                "datetime": datetime_filter,
                "limit": limit,
                "query": {
                    "eo:cloud_cover": {"lt": cloud_cover_max if cloud_cover_max is not None else 20.0}
                }
            }

        try:
            headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
            response = requests.post(f"{self.CATALOG_URL}/search", json=query, headers=headers)
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
        """Fetch a single STAC item by id."""
        token = self._get_access_token()
        url = f"{self.CATALOG_URL}/collections/sentinel-2-l2a/items/{scene_id}"
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
        band: str,
        output_path: str
    ) -> str:
        """Download a specific band from a scene using S3 interface (eodata bucket)."""
        scene = self.get_scene_item(scene_id)
        assets = scene.get('assets', {})
        
        # SOTA band resolution priority: 10m > 20m > 60m
        resolutions = ["10m", "20m", "60m"]
        band_asset = None
        for res in resolutions:
            name = f"{band}_{res}"
            if name in assets:
                band_asset = assets[name]
                break
        
        if not band_asset:
            band_asset = assets.get(band) or assets.get(f"{band}.tif") or (assets.get(f"B{band}") if band.startswith("B") else None)
        
        if not band_asset:
            raise ValueError(f"Band {band} not found in scene {scene_id}.")
        
        href = band_asset.get('href', '')
        
        # Extract the S3 key from the href
        # Standard CDSE STAC href: s3://eodata/Sentinel-2/MSI/L2A/.../band.jp2
        # Or relative path: /eodata/Sentinel-2/...
        s3_key = href.replace('s3://eodata/', '').replace('/eodata/', '')
        
        try:
            s3 = self._get_s3_client()
            logger.info(f"Downloading band {band} via S3 from key: {s3_key}")
            
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Download using boto3 from 'eodata' bucket
            s3.download_file("eodata", s3_key, output_path)
            
            logger.info(f"Successfully downloaded {band} to {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Failed to download band {band} via S3: {str(e)}")
            raise Exception(f"S3 Download failed: {str(e)}")

    def download_scene_bands(self, scene_id: str, bands: List[str], output_dir: str) -> Dict[str, str]:
        """Download multiple bands from a scene."""
        band_paths = {}
        for band in bands:
            output_path = os.path.join(output_dir, f"{scene_id}_{band}.tif")
            try:
                downloaded_path = self.download_band(scene_id, band, output_path)
                band_paths[band] = downloaded_path
            except Exception as e:
                logger.error(f"Failed to download band {band}: {str(e)}")
        return band_paths
