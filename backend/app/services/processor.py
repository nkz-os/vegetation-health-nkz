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
    
    def __init__(self, band_paths: Dict[str, str], bbox: Optional[list] = None):
        """Initialize processor with band file paths.

        Args:
            band_paths: Dictionary mapping band names to file paths
                       e.g., {'B04': '/path/to/B04.tif', 'B08': '/path/to/B08.tif'}
            bbox: Optional [minx, miny, maxx, maxy] in EPSG:4326 to crop bands
        """
        self.band_paths = band_paths
        self.band_data: Dict[str, np.ndarray] = {}
        self.band_meta: Optional[Dict] = None
        self.bbox = bbox

    def load_bands(self, bands: list[str]) -> None:
        """Load specified bands into memory, cropping to bbox if provided.

        Args:
            bands: List of band names to load (e.g., ['B04', 'B08'])
        """
        from rasterio.windows import from_bounds
        from rasterio.transform import from_bounds as transform_from_bounds

        for band in bands:
            if band not in self.band_paths:
                raise ValueError(f"Band {band} not found in band_paths")

            band_path = self.band_paths[band]
            if not Path(band_path).exists():
                raise FileNotFoundError(f"Band file not found: {band_path}")

            with rasterio.open(band_path) as src:
                if self.bbox:
                    # Reproject bbox from EPSG:4326 to raster CRS, then crop
                    from pyproj import Transformer
                    buf = 0.005  # ~500m buffer in degrees
                    minx, miny, maxx, maxy = self.bbox
                    try:
                        raster_crs = src.crs
                        if raster_crs and str(raster_crs) != 'EPSG:4326':
                            transformer = Transformer.from_crs(
                                'EPSG:4326', raster_crs, always_xy=True
                            )
                            proj_minx, proj_miny = transformer.transform(minx - buf, miny - buf)
                            proj_maxx, proj_maxy = transformer.transform(maxx + buf, maxy + buf)
                        else:
                            proj_minx, proj_miny = minx - buf, miny - buf
                            proj_maxx, proj_maxy = maxx + buf, maxy + buf

                        window = from_bounds(
                            proj_minx, proj_miny, proj_maxx, proj_maxy,
                            src.transform
                        )
                        window = window.intersection(
                            rasterio.windows.Window(0, 0, src.width, src.height)
                        )
                        data = src.read(1, window=window).astype(np.float32)
                        if self.band_meta is None:
                            self.band_meta = src.meta.copy()
                            self.band_meta.update({
                                'width': data.shape[1],
                                'height': data.shape[0],
                                'transform': src.window_transform(window),
                            })
                        logger.info(f"Band {band}: cropped from {src.width}x{src.height} to {data.shape[1]}x{data.shape[0]}")
                    except Exception as e:
                        logger.warning(f"Failed to crop band {band} to bbox: {e}, loading full raster")
                        data = src.read(1).astype(np.float32)
                        if self.band_meta is None:
                            self.band_meta = src.meta.copy()
                else:
                    data = src.read(1).astype(np.float32)
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

    def calculate_ndwi(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate NDWI (Normalized Difference Water Index).

        Detects water content in vegetation canopy. Useful for:
        - Irrigation management
        - Drought stress detection
        - Water body mapping

        Formula: (NIR - SWIR1) / (NIR + SWIR1)
        Uses B08 (NIR, 10m) and B11 (SWIR1, 20m)
        Range: -1 to 1 (higher values = more water content)

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B08', 'B11'])

        nir = self.band_data['B08']
        swir1 = self.band_data['B11']

        # Resample SWIR1 (20m) to match NIR (10m)
        if swir1.shape != nir.shape:
            logger.info(f"Resampling B11 from {swir1.shape} to {nir.shape} for NDWI")
            swir1 = self._resample_to_10m(swir1, nir)

        # Create cloud mask if requested
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        denominator = nir + swir1
        ndwi = np.where(denominator != 0, (nir - swir1) / denominator, 0)

        if cloud_mask is not None:
            ndwi = self.apply_cloud_mask(ndwi, cloud_mask)

        ndwi = np.clip(ndwi, -1, 1)
        return ndwi

    def calculate_ndmi(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate NDMI (Normalized Difference Moisture Index).

        Measures vegetation water content. More sensitive to moisture stress
        than NDVI. Useful for:
        - Crop water stress monitoring
        - Fire risk assessment
        - Drought monitoring

        Formula: (NIR - SWIR1) / (NIR + SWIR1)
        Uses B8A (NIR narrow, 20m) and B11 (SWIR1, 20m)
        Range: -1 to 1 (higher values = more moisture)

        Note: Similar to NDWI but uses B8A instead of B08 for better
        moisture sensitivity in dense vegetation.

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B8A', 'B11'])

        nir = self.band_data['B8A']
        swir1 = self.band_data['B11']

        # Both bands are 20m, but ensure same shape
        if swir1.shape != nir.shape:
            logger.warning(f"B8A and B11 shape mismatch: {nir.shape} vs {swir1.shape}")

        # Get reference 10m band for cloud mask if available
        reference_shape = nir.shape
        if 'B08' in self.band_data:
            reference_shape = self.band_data['B08'].shape
        elif 'B04' in self.band_paths:
            self.load_bands(['B04'])
            reference_shape = self.band_data['B04'].shape

        # Create cloud mask if requested
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        denominator = nir + swir1
        ndmi = np.where(denominator != 0, (nir - swir1) / denominator, 0)

        if cloud_mask is not None:
            ndmi = self.apply_cloud_mask(ndmi, cloud_mask)

        ndmi = np.clip(ndmi, -1, 1)
        return ndmi

    def calculate_msavi(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate MSAVI2 (Modified Soil-Adjusted Vegetation Index).

        Self-adjusting index that minimizes soil background effects without
        requiring an empirical soil adjustment factor (L). Better than SAVI
        for areas with exposed soil or sparse vegetation.

        Formula: (2 * NIR + 1 - sqrt((2 * NIR + 1)² - 8 * (NIR - RED))) / 2
        Range: -1 to 1

        Best for:
        - Early crop growth stages
        - Sparse vegetation
        - Areas with variable soil types

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B04', 'B08'])

        red = self.band_data['B04']
        nir = self.band_data['B08']

        # Create cloud mask if requested
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        # MSAVI2 formula: (2 * NIR + 1 - sqrt((2 * NIR + 1)^2 - 8 * (NIR - RED))) / 2
        term1 = 2 * nir + 1
        term2 = np.sqrt(np.maximum(0, term1**2 - 8 * (nir - red)))  # max(0,...) to avoid sqrt of negative
        msavi = (term1 - term2) / 2

        if cloud_mask is not None:
            msavi = self.apply_cloud_mask(msavi, cloud_mask)

        msavi = np.clip(msavi, -1, 1)
        return msavi

    def calculate_lai(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate LAI (Leaf Area Index) approximation from NDVI.

        LAI represents the total one-sided area of leaf tissue per unit
        ground surface area. This is an empirical approximation based on
        NDVI correlation studies.

        Formula (empirical): LAI = 0.57 * exp(2.33 * NDVI)
        Range: 0 to ~8 (m²/m²)

        Note: This is an approximation. True LAI requires ground calibration.

        Best for:
        - Biomass estimation
        - Crop growth monitoring
        - Carbon sequestration estimates

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        # First calculate NDVI
        ndvi = self.calculate_ndvi(apply_cloud_mask=apply_cloud_mask)

        # Empirical LAI approximation (based on literature)
        # LAI = 0.57 * exp(2.33 * NDVI) - common approximation
        # Only valid for positive NDVI values
        lai = np.where(ndvi > 0, 0.57 * np.exp(2.33 * ndvi), 0)

        # Clip to reasonable LAI range (0-8 is typical max for dense vegetation)
        lai = np.clip(lai, 0, 8)

        return lai

    def calculate_cire(self, apply_cloud_mask: bool = True) -> np.ndarray:
        """Calculate CIre (Chlorophyll Index Red Edge).

        Highly sensitive to chlorophyll content in leaves. Excellent for:
        - Nitrogen stress detection
        - Chlorophyll content estimation
        - Precision fertilization planning

        Formula: (NIR / RedEdge1) - 1
        Uses B08 (NIR) and B05 (RedEdge1, 20m)
        Range: 0 to ~10 (no upper bound, typically 0-5)

        Args:
            apply_cloud_mask: If True, mask cloudy pixels using SCL band
        """
        self.load_bands(['B05', 'B08'])

        nir = self.band_data['B08']
        rededge1 = self.band_data['B05']

        # Resample B05 (20m) to match B08 (10m)
        if rededge1.shape != nir.shape:
            logger.info(f"Resampling B05 from {rededge1.shape} to {nir.shape} for CIre")
            rededge1 = self._resample_to_10m(rededge1, nir)

        # Create cloud mask if requested
        cloud_mask = None
        if apply_cloud_mask and 'SCL' in self.band_paths:
            cloud_mask = self.create_cloud_mask(reference_shape=nir.shape)

        # CIre formula: (NIR / RedEdge1) - 1
        cire = np.where(rededge1 > 0, (nir / rededge1) - 1, 0)

        if cloud_mask is not None:
            cire = self.apply_cloud_mask(cire, cloud_mask)

        # Clip to reasonable range
        cire = np.clip(cire, 0, 10)
        return cire

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
    
    def create_geometry_mask(self, geometry_geojson: dict) -> np.ndarray:
        """Rasterize a GeoJSON polygon into a boolean mask matching the raster grid.

        Returns:
            Boolean mask where True = pixel is OUTSIDE the polygon (to be excluded).
        """
        from rasterio.features import rasterize as rio_rasterize
        from shapely.geometry import shape as shp
        from pyproj import Transformer
        from shapely.ops import transform

        geom = shp(geometry_geojson)

        # Reproject geometry to raster CRS if needed
        raster_crs = self.band_meta.get('crs')
        if raster_crs and str(raster_crs) != 'EPSG:4326':
            transformer = Transformer.from_crs('EPSG:4326', raster_crs, always_xy=True)
            geom = transform(transformer.transform, geom)

        # Rasterize: 1 inside polygon, 0 outside
        inside = rio_rasterize(
            [(geom, 1)],
            out_shape=(self.band_meta['height'], self.band_meta['width']),
            transform=self.band_meta['transform'],
            fill=0,
            dtype=np.uint8,
        )
        # Invert: True where pixel should be excluded
        return inside == 0

    def calculate_statistics(self, index_array: np.ndarray, mask: Optional[np.ndarray] = None) -> Dict[str, float]:
        """Calculate statistics for index array.

        Args:
            index_array: Index values array
            mask: Optional boolean mask (True = exclude pixel)

        Returns:
            Dictionary with statistics
        """
        if mask is not None:
            masked = np.ma.masked_array(index_array, mask=mask)
            valid_data = masked.compressed()  # removes masked values
            valid_data = valid_data[~np.isnan(valid_data)]
        else:
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
        
        # Update metadata for single band output — force GeoTIFF driver
        output_meta = self.band_meta.copy()
        output_meta.update({
            'driver': 'GTiff',
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
            Composite array
        """
        if not index_arrays:
            raise ValueError("No arrays provided for composite")
        
        if len(index_arrays) == 1:
            return index_arrays[0]
            
        stacked = np.stack(index_arrays, axis=0)
        
        if method == 'median':
            return np.nanmedian(stacked, axis=0)
        elif method == 'mean':
            # Mean composite
            composite = np.mean(stacked, axis=0)
        else:
            raise ValueError(f"Unknown composite method: {method}")
        
        # Restore NaN for invalid pixels (where all scenes had -9999)
        composite = np.where(composite == -9999, np.nan, composite)
        
        logger.info(f"Created {method} temporal composite from {len(index_arrays)} scenes")
        return composite

    def vectorize_index(self, index_array: np.ndarray, index_type: str, mask: Optional[np.ndarray] = None) -> dict:
        """
        Vectorize the continuous index raster into discrete polygons (GeoJSON FeatureCollection).
        Used for Asynchronous Sync to Cabin HMI (WatermelonDB).

        Args:
            index_array: The calculated vegetation index array.
            index_type: The type of index (e.g. 'NDVI').
            mask: Optional geometry mask. True pixels will be ignored.

        Returns:
            A GeoJSON FeatureCollection dict.
        """
        import rasterio.features
        from pyproj import Transformer
        from shapely.geometry import shape, mapping
        from shapely.ops import transform
        
        if self.band_meta is None:
            raise ValueError("Band metadata missing. Cannot vectorize.")

        # Reclassify continuous data into 5 discrete zones for the frontend to render easily
        # Classes: 1 (Very Low), 2 (Low), 3 (Moderate), 4 (High), 5 (Very High)
        discrete = np.full_like(index_array, 0, dtype=np.uint8)
        
        # Valid range masks (avoid nodata/nans)
        valid = ~np.isnan(index_array)
        if mask is not None:
            valid = valid & ~mask

        # Example breaks for NDVI. For generic, we just use equidistant bins between min and max
        min_val = float(np.nanmin(index_array[valid])) if np.any(valid) else 0.0
        max_val = float(np.nanmax(index_array[valid])) if np.any(valid) else 1.0
        
        # If absolute range is tiny, fallback to standard -1 to +1 assumption
        if (max_val - min_val) < 0.1:
            min_val, max_val = 0.0, 1.0

        bins = np.linspace(min_val, max_val, 6) # 5 bins
        
        discrete[valid & (index_array < bins[1])] = 1
        discrete[valid & (index_array >= bins[1]) & (index_array < bins[2])] = 2
        discrete[valid & (index_array >= bins[2]) & (index_array < bins[3])] = 3
        discrete[valid & (index_array >= bins[3]) & (index_array < bins[4])] = 4
        discrete[valid & (index_array >= bins[4])] = 5
        
        # Mask out nodata completely (0)
        discrete[~valid] = 0

        # Vectorize
        shapes = rasterio.features.shapes(
            discrete, 
            transform=self.band_meta['transform'], 
            mask=(discrete > 0)
        )
        
        features = []
        
        # Setup projection transformer if needed (to EPSG:4326)
        raster_crs = self.band_meta.get('crs')
        transformer = None
        if raster_crs and str(raster_crs) != 'EPSG:4326':
            transformer = Transformer.from_crs(raster_crs, 'EPSG:4326', always_xy=True)

        class_labels = {
            1: "Very Low",
            2: "Low",
            3: "Moderate",
            4: "High",
            5: "Very High"
        }

        for geom, val in shapes:
            value = int(val)
            if value == 0:
                continue
                
            poly = shape(geom)
            if transformer:
                poly = transform(transformer.transform, poly)
                
            features.append({
                "type": "Feature",
                "geometry": mapping(poly),
                "properties": {
                    "index_type": index_type,
                    "zone_class": value,
                    "label": class_labels.get(value, "Unknown"),
                    "range_min": float(bins[value-1]),
                    "range_max": float(bins[value])
                }
            })

        return {
            "type": "FeatureCollection",
            "features": features
        }

