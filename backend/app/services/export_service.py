"""
Export service for prescription maps and management zones.

Supports:
- GeoJSON (universal)
- Shapefile (ArcGIS, QGIS, machinery)
- CSV (spreadsheets, analysis)
- ISOXML (ISO 11783 - modern tractors)
"""

import logging
import json
import zipfile
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import io

logger = logging.getLogger(__name__)


class PrescriptionMapExporter:
    """Export management zones and prescription maps in multiple formats."""

    def __init__(self):
        self.supported_formats = ['geojson', 'shapefile', 'csv', 'isoxml']

    def export_geojson(
        self,
        features: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None
    ) -> bytes:
        """Export features as GeoJSON.

        Args:
            features: List of GeoJSON Feature objects
            metadata: Optional metadata to include

        Returns:
            GeoJSON bytes
        """
        geojson = {
            "type": "FeatureCollection",
            "features": features,
            "metadata": metadata or {},
            "generated_at": datetime.utcnow().isoformat(),
            "generator": "Nekazari Vegetation Prime"
        }

        return json.dumps(geojson, indent=2).encode('utf-8')

    def export_shapefile(
        self,
        features: List[Dict[str, Any]],
        name: str = "prescription_map"
    ) -> bytes:
        """Export features as Shapefile (zipped).

        Creates a complete Shapefile package (.shp, .shx, .dbf, .prj)
        compatible with QGIS, ArcGIS, and farm machinery software.

        Args:
            features: List of GeoJSON Feature objects
            name: Base name for the shapefile

        Returns:
            Zipped shapefile bytes
        """
        try:
            import fiona
            from fiona.crs import from_epsg
            from shapely.geometry import shape, mapping
        except ImportError:
            logger.error("fiona/shapely not installed for shapefile export")
            raise ImportError("Shapefile export requires fiona and shapely packages")

        if not features:
            raise ValueError("No features to export")

        # Determine schema from first feature properties
        sample_props = features[0].get('properties', {})
        schema_properties = {}

        for key, value in sample_props.items():
            if isinstance(value, bool):
                schema_properties[key] = 'str'  # Shapefile doesn't support bool
            elif isinstance(value, int):
                schema_properties[key] = 'int'
            elif isinstance(value, float):
                schema_properties[key] = 'float'
            else:
                schema_properties[key] = 'str'

        # Determine geometry type
        geom_type = features[0].get('geometry', {}).get('type', 'Polygon')

        schema = {
            'geometry': geom_type,
            'properties': schema_properties
        }

        # Create temporary directory for shapefile components
        with tempfile.TemporaryDirectory() as tmpdir:
            shp_path = Path(tmpdir) / f"{name}.shp"

            # Write shapefile
            with fiona.open(
                str(shp_path),
                'w',
                driver='ESRI Shapefile',
                crs=from_epsg(4326),  # WGS84
                schema=schema
            ) as shp:
                for feature in features:
                    # Convert properties to match schema
                    props = {}
                    for key, value in feature.get('properties', {}).items():
                        if key in schema_properties:
                            if isinstance(value, bool):
                                props[key] = str(value)
                            else:
                                props[key] = value

                    shp.write({
                        'geometry': feature.get('geometry'),
                        'properties': props
                    })

            # Create zip with all shapefile components
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg']:
                    file_path = Path(tmpdir) / f"{name}{ext}"
                    if file_path.exists():
                        zf.write(file_path, f"{name}{ext}")

            zip_buffer.seek(0)
            return zip_buffer.read()

    def export_csv(
        self,
        features: List[Dict[str, Any]],
        include_geometry: bool = True
    ) -> bytes:
        """Export features as CSV.

        Useful for spreadsheet analysis and simple data exchange.

        Args:
            features: List of GeoJSON Feature objects
            include_geometry: Include WKT geometry column

        Returns:
            CSV bytes
        """
        import csv

        if not features:
            raise ValueError("No features to export")

        # Collect all property keys
        all_keys = set()
        for f in features:
            all_keys.update(f.get('properties', {}).keys())

        # Build header
        headers = ['id'] + sorted(all_keys)
        if include_geometry:
            headers.append('geometry_wkt')

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)

        for i, feature in enumerate(features):
            row = [i + 1]
            props = feature.get('properties', {})

            for key in sorted(all_keys):
                row.append(props.get(key, ''))

            if include_geometry:
                # Convert geometry to WKT
                geom = feature.get('geometry', {})
                wkt = self._geometry_to_wkt(geom)
                row.append(wkt)

            writer.writerow(row)

        return output.getvalue().encode('utf-8')

    def export_isoxml(
        self,
        features: List[Dict[str, Any]],
        task_name: str = "VRA_Prescription",
        product_name: str = "Fertilizer",
        default_rate: float = 100.0,
        rate_property: str = "application_rate"
    ) -> bytes:
        """Export as ISOXML (ISO 11783) for ISOBUS-compatible tractors.

        Creates a Task Data file that can be loaded directly into
        modern tractors and sprayers for variable rate application.

        Args:
            features: List of GeoJSON Feature objects with rate values
            task_name: Name for the task
            product_name: Name of the product being applied
            default_rate: Default application rate if not in properties
            rate_property: Property name containing the rate value

        Returns:
            ISOXML bytes (zipped TASKDATA folder)
        """
        from xml.etree import ElementTree as ET
        from xml.dom import minidom

        # Create ISO 11783-10 TaskData structure
        iso = ET.Element('ISO11783_TaskData', {
            'VersionMajor': '4',
            'VersionMinor': '0',
            'ManagementSoftwareManufacturer': 'Nekazari',
            'ManagementSoftwareVersion': '1.0',
            'DataTransferOrigin': '1'  # FMIS
        })

        # Task element
        task = ET.SubElement(iso, 'TSK', {
            'A': 'TSK-1',  # Task ID
            'B': task_name,  # Task designator
            'G': '1'  # Task status: planned
        })

        # Product (what's being applied)
        product = ET.SubElement(iso, 'PDT', {
            'A': 'PDT-1',
            'B': product_name
        })

        # Treatment zone (prescription grid)
        tzn = ET.SubElement(task, 'TZN', {
            'A': '1',  # Zone code
            'B': task_name
        })

        # Grid definition
        if features:
            # Calculate bounding box
            all_coords = []
            for f in features:
                geom = f.get('geometry', {})
                coords = geom.get('coordinates', [[]])
                if geom.get('type') == 'Polygon':
                    all_coords.extend(coords[0])
                elif geom.get('type') == 'MultiPolygon':
                    for poly in coords:
                        all_coords.extend(poly[0])

            if all_coords:
                min_lon = min(c[0] for c in all_coords)
                max_lon = max(c[0] for c in all_coords)
                min_lat = min(c[1] for c in all_coords)
                max_lat = max(c[1] for c in all_coords)

                # Grid element
                grd = ET.SubElement(tzn, 'GRD', {
                    'A': str(min_lon),  # Min longitude
                    'B': str(min_lat),  # Min latitude
                    'C': '0.0001',  # Cell size longitude (degrees, ~10m)
                    'D': '0.0001',  # Cell size latitude
                    'E': str(int((max_lon - min_lon) / 0.0001) + 1),  # Columns
                    'F': str(int((max_lat - min_lat) / 0.0001) + 1),  # Rows
                    'G': 'GRD00001.BIN',  # Grid data file
                    'H': '2',  # Grid type: treatment zone
                    'I': '1'  # Treatment zone code
                })

        # Process data variables (rates)
        for i, feature in enumerate(features):
            props = feature.get('properties', {})
            rate = props.get(rate_property, default_rate)

            pdv = ET.SubElement(tzn, 'PDV', {
                'A': str(i + 1),  # Process data DDI
                'B': str(int(rate)),  # Value
                'C': 'PDT-1'  # Product reference
            })

        # Pretty print XML
        xml_str = minidom.parseString(ET.tostring(iso)).toprettyxml(indent="  ")

        # Create TASKDATA zip structure
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('TASKDATA/TASKDATA.XML', xml_str.encode('utf-8'))

            # Create binary grid file (simplified - just zone IDs)
            grid_data = bytearray()
            for i, _ in enumerate(features):
                grid_data.extend((i + 1).to_bytes(1, 'little'))
            zf.writestr('TASKDATA/GRD00001.BIN', bytes(grid_data))

        zip_buffer.seek(0)
        return zip_buffer.read()

    def _geometry_to_wkt(self, geometry: Dict[str, Any]) -> str:
        """Convert GeoJSON geometry to WKT string."""
        geom_type = geometry.get('type', '')
        coords = geometry.get('coordinates', [])

        if geom_type == 'Point':
            return f"POINT ({coords[0]} {coords[1]})"

        elif geom_type == 'Polygon':
            rings = []
            for ring in coords:
                points = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                rings.append(f"({points})")
            return f"POLYGON ({', '.join(rings)})"

        elif geom_type == 'MultiPolygon':
            polygons = []
            for poly in coords:
                rings = []
                for ring in poly:
                    points = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                    rings.append(f"({points})")
                polygons.append(f"({', '.join(rings)})")
            return f"MULTIPOLYGON ({', '.join(polygons)})"

        else:
            return f"GEOMETRY({geom_type})"

    def calculate_prescription_zones(
        self,
        index_stats: List[Dict[str, Any]],
        geometry: Dict[str, Any],
        n_zones: int = 3,
        rate_formula: str = "linear"
    ) -> List[Dict[str, Any]]:
        """Calculate prescription zones from vegetation index statistics.

        This creates management zones based on vegetation vigor for
        variable rate application (VRA).

        Args:
            index_stats: List of stats with mean values per area
            geometry: Parcel geometry
            n_zones: Number of management zones (2-5)
            rate_formula: How to calculate rates ('linear', 'inverse')

        Returns:
            List of GeoJSON features with prescription rates
        """
        # This is a simplified version - real implementation would use
        # k-means clustering on the actual raster data

        # For now, create zones based on NDVI ranges
        features = []
        ndvi_ranges = [
            (0.0, 0.3, 'Low', 140),   # Low NDVI -> High fertilizer
            (0.3, 0.5, 'Medium', 100),
            (0.5, 1.0, 'High', 60),   # High NDVI -> Low fertilizer
        ]

        for i, (min_ndvi, max_ndvi, label, rate) in enumerate(ndvi_ranges):
            features.append({
                "type": "Feature",
                "properties": {
                    "zone_id": i + 1,
                    "zone_name": f"Zone {label}",
                    "ndvi_range": f"{min_ndvi}-{max_ndvi}",
                    "potential_yield": label,
                    "application_rate": rate,
                    "nitrogen_kg_ha": rate,
                    "recommendation": f"Apply {rate} kg/ha of nitrogen"
                },
                "geometry": geometry  # In real impl, this would be clustered polygons
            })

        return features


# Singleton instance
exporter = PrescriptionMapExporter()
