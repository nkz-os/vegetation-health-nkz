/**
 * Vegetation Layer - Cesium Direct Implementation
 * Receives 'viewer' from CesiumMap slot renderer and manages layers directly.
 */

import React, { useEffect, useRef } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';

interface VegetationLayerProps {
  viewer?: any; // Injected by CesiumMap
}

// Get base API URL from window.__ENV__ (injected by nginx at runtime)
const getApiBaseUrl = (): string => {
  if (typeof window !== 'undefined' && window.__ENV__) {
    return window.__ENV__.API_URL || window.__ENV__.VITE_API_URL || '';
  }
  return '';
};

export const VegetationLayer: React.FC<VegetationLayerProps> = ({ viewer }) => {
  const { selectedIndex, selectedDate, selectedSceneId } = useVegetationContext();
  
  // Track our added layers to remove them cleanly
  const layerRef = useRef<any>(null);
  const dataSourceRef = useRef<any>(null);

  useEffect(() => {
    if (!viewer) {
      console.warn('[VegetationLayer] No viewer provided');
      return;
    }

    // @ts-ignore
    const Cesium = window.Cesium;
    if (!Cesium) return;

    // 1. CLEANUP PREVIOUS LAYERS
    if (layerRef.current) {
        viewer.imageryLayers.remove(layerRef.current);
        layerRef.current = null;
    }
    if (dataSourceRef.current) {
        viewer.dataSources.remove(dataSourceRef.current);
        dataSourceRef.current = null;
    }

    // If nothing selected, just exit (cleanup done)
    if (!selectedSceneId && !selectedIndex) return;

    // 2. HANDLE VECTOR LAYER (VRA ZONES)
    if (selectedIndex === 'VRA_ZONES' && selectedSceneId) {
        const apiBaseUrl = getApiBaseUrl();
        const geoJsonUrl = `${apiBaseUrl}/api/vegetation/jobs/zoning/${selectedSceneId}/geojson`;
        console.log('[VegetationLayer] Loading VRA Zones:', geoJsonUrl);

        Cesium.GeoJsonDataSource.load(geoJsonUrl, {
            stroke: Cesium.Color.BLACK,
            fill: Cesium.Color.BLUE.withAlpha(0.5),
            strokeWidth: 3
        }).then((dataSource: any) => {
            if (viewer.isDestroyed()) return;
            
            // Apply custom styling per feature
            const entities = dataSource.entities.values;
            for (let i = 0; i < entities.length; i++) {
                const entity = entities[i];
                const clusterId = entity.properties.cluster_id?.getValue();
                
                // Color ramp
                const colors = [
                    Cesium.Color.RED.withAlpha(0.6),
                    Cesium.Color.ORANGE.withAlpha(0.6),
                    Cesium.Color.YELLOW.withAlpha(0.6),
                    Cesium.Color.GREEN.withAlpha(0.6),
                    Cesium.Color.BLUE.withAlpha(0.6)
                ];
                const color = colors[clusterId % colors.length] || Cesium.Color.GRAY.withAlpha(0.6);
                
                entity.polygon.material = color;
                entity.polygon.outline = true;
                entity.polygon.outlineColor = Cesium.Color.BLACK;
                entity.polygon.extrudedHeight = 10; // Slight extrusion for visibility
            }

            viewer.dataSources.add(dataSource);
            dataSourceRef.current = dataSource;
            viewer.flyTo(dataSource);
        }).catch((err: any) => {
            console.error('[VegetationLayer] Error loading zones:', err);
        });
        return;
    }

    // 3. HANDLE RASTER LAYER (NDVI, etc.)
    if (selectedIndex && selectedSceneId) {
        const apiBaseUrl = getApiBaseUrl();
        // Construct Tile URL (XYZ format) with absolute URL
        const tileUrl = `${apiBaseUrl}/api/vegetation/tiles/{z}/{x}/{y}.png?scene_id=${selectedSceneId}&index_type=${selectedIndex}`;
        console.log('[VegetationLayer] Adding Raster Layer:', tileUrl);

        const provider = new Cesium.UrlTemplateImageryProvider({
            url: tileUrl,
            maximumLevel: 18,
            credit: 'Vegetation Prime Module'
        });

        const layer = viewer.imageryLayers.addImageryProvider(provider);
        layer.alpha = 0.8; // Default opacity
        layerRef.current = layer;
    }

  }, [viewer, selectedIndex, selectedDate, selectedSceneId]);

  return null; // Side-effect only
};

export default VegetationLayer;
