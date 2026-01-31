"""
Vegetation index processor with support for multiple indices and custom formulas.
"""

import logging
from typing import Dict, Optional, Tuple, Any
from pathlib import Path
import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.features import rasterize
from shapely.geometry import shape
import simpleeval

logger = logging.getLogger(__name__)


class VegetationIndexProcessor:
    """Processes vegetation indices from Sentinel-2 bands."""
    
    # Sentinel-2 band mappings (10m and 20m resolution)
    BAND_MAPPING = {
        'B02': 'Blue',      # 10m
        'B03': 'Green',     # 10m
        'B04': 'Red',       # 10m
        'B08': 'NIR',       # 10m
        'B05': 'RedEdge1',  # 20m
        'B06': 'RedEdge2',  # 20m
        'B07': 'RedEdge3',  # 20m
        'B8A': 'RedEdge4',  # 20m
        'B11': 'SWIR1',     # 20m
        'B12': 'SWIR2',     # 20m
        'SCL': 'SceneClassification',  # 20m - Scene Classification Layer
    }

    # Sentinel-2 L2A Scene Classification Layer (SCL) values
    # Reference: https://sentinels.copernicus.eu/web/sentinel/technical-guides/sentinel-2-msi/level-2a/algorithm
    SCL_CLASSES = {
        0: 'NO_DATA',
        1: 'SATURATED_DEFECTIVE',
        2: 'DARK_AREA_PIXELS',
        3: 'CLOUD_SHADOWS',
        4: 'VEGETATION',
        5: 'BARE_SOILS',
        6: 'WATER',
        7: 'UNCLASSIFIED',
        8: 'CLOUD_MEDIUM_PROBABILITY',
        9: 'CLOUD_HIGH_PROBABILITY',
        10: 'THIN_CIRRUS',
        11: 'SNOW_ICE',
    }

    # Valid SCL classes for vegetation analysis (exclude clouds, shadows, water, snow)
    VALID_SCL_CLASSES = {4, 5, 6, 7}  # Vegetation, Bare soils, Water, Unclassified
    EXCLUDE_SCL_CLASSES = {0, 1, 3, 8, 9, 10, 11}  # No data, Saturated, Cloud shadows, Clouds, Cirrus, Snow
    
    def __init__(self, band_paths: Dict[str, str]):
        """Initialize processor with band file paths.
        
        Args:
            band_paths: Dictionary mapping band names to file paths
                       e.g., {'B04': '/path/to/B04.tif', 'B08': '/path/to/B08.tif'}
        """
        self.band_paths = band_paths
        self.band_data: Dict[str, np.ndarray] = {}
        self.band_meta: Optional[Dict] = None
    
    def load_bands(self, bands: list[str]) -> None:
        """Load specified bands into memory.
        
        Args:
            bands: List of band names to load (e.g., ['B04', 'B08'])
        """
        for band in bands:
            if band not in self.band_paths:
                raise ValueError(f"Band {band} not found in band_paths")
            
            band_path = self.band_paths[band]
            if not Path(band_path).exists():
                raise FileNotFoundError(f"Band file not found: {band_path}")
            
            with rasterio.open(band_path) as src:
                data = src.read(1).astype(np.float32)
                # Store metadata from first band
                if self.band_meta is None:
                    self.band_meta = src.meta.copy()
                
                self.band_data[band] = data
        
        logger.info(f"Loaded {len(bands)} bands: {bands}")
    
    def _resample_to_10m(self, band_20m: np.ndarray, reference_10m: np.ndarray) -> np.ndarray:
        """Resample 20m band to 10m resolution using scipy zoom.

        Args:
            band_20m: 20m resolution band data
            reference_10m: Reference 10m band for target shape

        Returns:
            Resampled band at 10m resolution
        """
        from scipy.ndimage import zoom

        target_shape = reference_10m.shape
        source_shape = band_20m.shape

        # Calculate zoom factors
        zoom_factors = (
            target_shape[0] / source_shape[0],
            target_shape[1] / source_shape[1]
        )

        # Resample using bilinear interpolation (order=1)
        resampled = zoom(band_20m, zoom_factors, order=1, mode='nearest')

        # Ensure exact shape match (zoom may be off by 1 pixel due to rounding)
        if resampled.shape != target_shape:
            resampled = resampled[:target_shape[0], :target_shape[1]]

        logger.info(f"Resampled 20m band from {source_shape} to {resampled.shape}")
        return resampled.astype(np.float32)

    def create_cloud_mask(self, reference_shape: Optional[Tuple[int, int]] = None) -> np.ndarray:
        """Create cloud mask from SCL (Scene Classification Layer) band.

        The SCL band identifies clouds, shadows, and other artifacts that should
        be excluded from vegetation analysis for accurate results.

        Args:
            reference_shape: Target shape for 10m resolution (if SCL needs resampling)

        Returns:
            Boolean mask where True = invalid pixel (cloud/shadow/etc), False = valid
        """
        if 'SCL' not in self.band_paths:
            logger.warning("SCL band not available, skipping cloud masking")
            return None

        try:
            # Load SCL band if not already loaded
            if 'SCL' not in self.band_data:
                self.load_bands(['SCL'])

            scl = self.band_data['SCL']

            # Resample SCL to 10m if reference shape provided and different
            if reference_shape and scl.shape != reference_shape:
                logger.info(f"Resampling SCL from {scl.shape} to {reference_shape}")
                # Use nearest neighbor for categorical data (SCL is classification)
                from scipy.ndimage import zoom
                zoom_factors = (
                    reference_shape[0] / scl.shape[0],
                    reference_shape[1] / scl.shape[1]
                )
                scl = zoom(scl, zoom_factors, order=0, mode='nearest')  # order=0 = nearest neighbor
                if scl.shape != reference_shape:
                    scl = scl[:reference_shape[0], :reference_shape[1]]

            # Create mask: True for pixels to EXCLUDE (clouds, shadows, etc.)
            cloud_mask = np.isin(scl.astype(int), list(self.EXCLUDE_SCL_CLASSES))

            # Count statistics
            total_pixels = cloud_mask.size
            masked_pixels = np.sum(cloud_mask)
            cloud_percentage = (masked_pixels / total_pixels) * 100

            logger.info(
                f"Cloud mask created: {masked_pixels}/{total_pixels} pixels masked "
                f"({cloud_percentage:.1f}% cloudy/invalid)"
            )

            return cloud_mask

        except Exception as e:
            logger.warning(f"Failed to create cloud mask: {e}")
            return None

    def apply_cloud_mask(self, data: np.ndarray, cloud_mask: Optional[np.ndarray]) -> np.ndarray:
        """Apply cloud mask to data array, setting masked pixels to NaN.

        Args:
            data: Input data array
            cloud_mask: Boolean mask (True = masked/invalid)

        Returns:
            Data array with masked pixels set to NaN
        """
        if cloud_mask is None:
            return data

        if data.shape != cloud_mask.shape:
            logger.warning(f"Shape mismatch: data {data.shape} vs mask {cloud_mask.shape}")
            return data

        # Set cloudy pixels to NaN
        result = data.copy()
        result[cloud_mask] = np.nan
        return result

    def calculate_ndvi(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate NDVI (Normalized Difference Vegetation Index).

        Formula: (NIR - Red) / (NIR + Red)
        Range: -1 to 1 (typically 0 to 1 for vegetation)

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B04', 'B08'])

        red = self.band_data['B04']
        nir = self.band_data['B08']

        # Create cloud mask if requested and SCL available
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        # Avoid division by zero
        denominator = nir + red
        ndvi = np.where(denominator != 0, (nir - red) / denominator, 0)

        # Apply cloud mask
        if cloud_mask is not None:
            ndvi = self.apply_cloud_mask(ndvi, cloud_mask)

        # Clip to valid range
        ndvi = np.clip(ndvi, -1, 1)

        return ndvi
    
    def calculate_evi(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate EVI (Enhanced Vegetation Index).

        Formula: 2.5 * ((NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1))
        Range: -1 to 1 (typically 0 to 1)

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B02', 'B04', 'B08'])

        blue = self.band_data['B02']
        red = self.band_data['B04']
        nir = self.band_data['B08']

        # Create cloud mask if requested
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        denominator = nir + 6 * red - 7.5 * blue + 1
        evi = np.where(denominator != 0, 2.5 * ((nir - red) / denominator), 0)

        # Apply cloud mask
        if cloud_mask is not None:
            evi = self.apply_cloud_mask(evi, cloud_mask)

        evi = np.clip(evi, -1, 1)

        return evi
    
    def calculate_savi(self, l: float = 0.5, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate SAVI (Soil-Adjusted Vegetation Index).

        Formula: ((NIR - Red) / (NIR + Red + L)) * (1 + L)
        L: Soil adjustment factor (typically 0.5)
        Range: -1 to 1

        Args:
            l: Soil adjustment factor (default 0.5)
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B04', 'B08'])

        red = self.band_data['B04']
        nir = self.band_data['B08']

        # Create cloud mask if requested
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        denominator = nir + red + l
        savi = np.where(denominator != 0, ((nir - red) / denominator) * (1 + l), 0)

        # Apply cloud mask
        if cloud_mask is not None:
            savi = self.apply_cloud_mask(savi, cloud_mask)

        savi = np.clip(savi, -1, 1)

        return savi
    
    def calculate_gndvi(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate GNDVI (Green Normalized Difference Vegetation Index).

        Formula: (NIR - Green) / (NIR + Green)
        Range: -1 to 1

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B03', 'B08'])

        green = self.band_data['B03']
        nir = self.band_data['B08']

        # Create cloud mask if requested
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        denominator = nir + green
        gndvi = np.where(denominator != 0, (nir - green) / denominator, 0)

        # Apply cloud mask
        if cloud_mask is not None:
            gndvi = self.apply_cloud_mask(gndvi, cloud_mask)

        gndvi = np.clip(gndvi, -1, 1)

        return gndvi
    
    def calculate_ndre(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate NDRE (Normalized Difference Red Edge).

        Formula: (NIR - RedEdge) / (NIR + RedEdge)
        Uses B8A (RedEdge4) as RedEdge band (20m -> resampled to 10m)
        Range: -1 to 1

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B8A', 'B08'])

        rededge = self.band_data['B8A']
        nir = self.band_data['B08']

        # Resample B8A (20m) to match B08 (10m) resolution
        if rededge.shape != nir.shape:
            logger.info(f"Resampling B8A from {rededge.shape} to {nir.shape} for NDRE")
            rededge = self._resample_to_10m(rededge, nir)

        # Create cloud mask if requested
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        denominator = nir + rededge
        ndre = np.where(denominator != 0, (nir - rededge) / denominator, 0)

        # Apply cloud mask
        if cloud_mask is not None:
            ndre = self.apply_cloud_mask(ndre, cloud_mask)

        ndre = np.clip(ndre, -1, 1)

        return ndre
    
    def calculate_custom_index(self, formula: str) -> np.ndarray:
        """Calculate custom index using safe formula evaluation.

        Args:
            formula: Mathematical formula using band names
                    e.g., "(B08-B04)/(B08+B04)" for NDVI
                    Available bands: B02, B03, B04, B05, B06, B07, B08, B8A, B11, B12

        Returns:
            Calculated index array

        Security: Uses simpleeval for safe formula parsing instead of eval()
        """
        try:
            # Load all bands mentioned in formula
            required_bands = self._extract_bands_from_formula(formula)
            if not required_bands:
                raise ValueError("No valid bands found in formula")

            self.load_bands(required_bands)

            # Create safe evaluator with numpy functions
            evaluator = simpleeval.EvalWithCompoundTypes(
                functions={
                    # Safe math functions
                    'sqrt': np.sqrt,
                    'abs': np.abs,
                    'log': np.log,
                    'log10': np.log10,
                    'exp': np.exp,
                    'sin': np.sin,
                    'cos': np.cos,
                    'tan': np.tan,
                    'arctan': np.arctan,
                    'arctan2': np.arctan2,
                    'clip': np.clip,
                    'where': np.where,
                    'maximum': np.maximum,
                    'minimum': np.minimum,
                    'power': np.power,
                },
                names={}
            )

            # Add band arrays as named variables
            for band in required_bands:
                if band in self.band_data:
                    evaluator.names[band] = self.band_data[band]

            # Evaluate safely (simpleeval blocks dangerous operations)
            result = evaluator.eval(formula)

            # Validate result
            if not isinstance(result, np.ndarray):
                # Try to broadcast scalar to array shape
                if isinstance(result, (int, float)):
                    reference_shape = next(iter(self.band_data.values())).shape
                    result = np.full(reference_shape, result, dtype=np.float32)
                else:
                    raise ValueError("Formula must return a numeric value or array")

            # Clip to reasonable range
            result = np.clip(result, -10, 10)

            logger.info(f"Custom formula evaluated successfully: {formula[:50]}...")
            return result.astype(np.float32)

        except simpleeval.InvalidExpression as e:
            logger.error(f"Invalid formula syntax: {str(e)}")
            raise ValueError(f"Invalid formula syntax: {str(e)}")
        except Exception as e:
            logger.error(f"Error calculating custom index: {str(e)}")
            raise ValueError(f"Invalid formula: {str(e)}")
    
    def _extract_bands_from_formula(self, formula: str) -> list[str]:
        """Extract band names from formula string.
        
        Args:
            formula: Formula string
            
        Returns:
            List of band names found in formula
        """
        available_bands = list(self.BAND_MAPPING.keys())
        found_bands = []
        
        for band in available_bands:
            if band in formula:
                found_bands.append(band)
        
        return found_bands
    
    def calculate_statistics(self, index_array: np.ndarray, mask: Optional[np.ndarray] = None) -> Dict[str, float]:
        """Calculate statistics for index array.
        
        Args:
            index_array: Index values array
            mask: Optional mask to exclude certain pixels
            
        Returns:
            Dictionary with statistics
        """
        if mask is not None:
            index_array = np.ma.masked_array(index_array, mask=mask)
        
        valid_data = index_array[~np.isnan(index_array)]
        if len(valid_data) == 0:
            return {
                'mean': 0.0,
                'min': 0.0,
                'max': 0.0,
                'std': 0.0,
                'pixel_count': 0
            }
        
        return {
            'mean': float(np.mean(valid_data)),
            'min': float(np.min(valid_data)),
            'max': float(np.max(valid_data)),
            'std': float(np.std(valid_data)),
            'pixel_count': int(len(valid_data))
        }
    
    def save_index_raster(self, index_array: np.ndarray, output_path: str) -> str:
        """Save index array as GeoTIFF.
        
        Args:
            index_array: Index values array
            output_path: Output file path
            
        Returns:
            Output file path
        """
        if self.band_meta is None:
            raise ValueError("No band metadata available. Load bands first.")
        
        # Update metadata for single band output
        output_meta = self.band_meta.copy()
        output_meta.update({
            'count': 1,
            'dtype': 'float32',
            'compress': 'lzw',
            'nodata': -9999
        })
        
        with rasterio.open(output_path, 'w', **output_meta) as dst:
            dst.write(index_array.astype(np.float32), 1)
        
        logger.info(f"Saved index raster to {output_path}")
        return output_path
    
    @staticmethod
    def create_temporal_composite(
        index_arrays: list[np.ndarray],
        method: str = 'median'
    ) -> np.ndarray:
        """Create temporal composite from multiple index arrays using median (cloud-free).
        
        Args:
            index_arrays: List of index arrays from different scenes (must be same shape)
            method: Composite method ('median' or 'mean'). Median is recommended for cloud removal.
            
        Returns:
            Composite index array
        """
        if not index_arrays:
            raise ValueError("No index arrays provided for composite")
        
        if len(index_arrays) == 1:
            return index_arrays[0]
        
        # Stack arrays (shape: [n_scenes, height, width])
        stacked = np.stack(index_arrays, axis=0)
        
        # Replace NaN with -9999 for proper handling
        stacked = np.where(np.isnan(stacked), -9999, stacked)
        
        if method == 'median':
            # Median composite (best for cloud removal)
            composite = np.median(stacked, axis=0)
        elif method == 'mean':
            # Mean composite
            composite = np.mean(stacked, axis=0)
        else:
            raise ValueError(f"Unknown composite method: {method}")
        
        # Restore NaN for invalid pixels (where all scenes had -9999)
        composite = np.where(composite == -9999, np.nan, composite)
        
        logger.info(f"Created {method} temporal composite from {len(index_arrays)} scenes")
        return composite

