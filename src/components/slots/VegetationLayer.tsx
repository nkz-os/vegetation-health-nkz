/**
 * Vegetation Layer - Cesium + TiTiler (Phase 4)
 * Raster: GET viewer-url → presigned TiTiler template → UrlTemplateImageryProvider.
 * Vector: VRA zones via GeoJsonDataSource. No Deck.gl in this slot.
 */

import React, { useEffect, useRef, useCallback } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';

interface VegetationLayerProps {
  viewer?: any; // Injected by CesiumMap
}

// Get base API URL from window.__ENV__ (injected by nginx at runtime)
const getApiBaseUrl = (): string => {
  if (typeof window !== 'undefined' && (window as any).__ENV__) {
    const env = (window as any).__ENV__;
    return env.API_URL || env.VITE_API_URL || '';
  }
  return '';
};

export const VegetationLayer: React.FC<VegetationLayerProps> = ({ viewer }) => {
  const { selectedIndex, selectedSceneId, selectedEntityId } = useVegetationContext();
  const layerRef = useRef<any>(null);
  const dataSourceRef = useRef<any>(null);

  const addRasterLayer = useCallback(
    (viewerRef: any, sceneId: string, indexType: string) => {
      const Cesium = (window as any).Cesium;
      if (!Cesium || !viewerRef || viewerRef.isDestroyed()) return null;

      let currentLayer: any = null;
      let currentProvider: any = null;

      const removeCurrentRaster = () => {
        if (viewerRef && !viewerRef.isDestroyed() && currentLayer) {
          viewerRef.imageryLayers.remove(currentLayer, true);
          currentLayer = null;
        }
        if (currentProvider && !currentProvider.isDestroyed()) {
          try {
            currentProvider.destroy();
          } catch (_) { }
          currentProvider = null;
        }
      };

      const applyNewTemplate = (sId: string, iType: string) => {
        if (!viewerRef || viewerRef.isDestroyed()) return;
        removeCurrentRaster();
        
        const apiBaseUrl = getApiBaseUrl();
        // La URL ahora apunta a nuestro propio backend (rio-tiler integrado)
        // Usamos iType si el backend lo requiere, aunque actualmente sId es suficiente
        const tileUrlTemplate = `${apiBaseUrl}/api/vegetation/tiles/${sId}/{z}/{x}/{y}.png?index=${iType}`;
        
        const provider = new Cesium.UrlTemplateImageryProvider({
          url: tileUrlTemplate,
          maximumLevel: 19,
          minimumLevel: 10,
          hasAlphaChannel: true,
          credit: 'Vegetation Prime (Internal Rendering)',
        });
        
        currentProvider = provider;
        const layer = viewerRef.imageryLayers.addImageryProvider(provider);
        layer.alpha = 0.8;
        currentLayer = layer;
        layerRef.current = layer;
      };

      if (sceneId && indexType) {
        applyNewTemplate(sceneId, indexType);
      }

      return removeCurrentRaster;
    },
    []
  );

  useEffect(() => {
    if (!viewer) return;

    const Cesium = (window as any).Cesium;
    if (!Cesium) return;

    if (layerRef.current) {
      viewer.imageryLayers.remove(layerRef.current, true);
      layerRef.current = null;
    }
    if (dataSourceRef.current) {
      viewer.dataSources.remove(dataSourceRef.current);
      dataSourceRef.current = null;
    }

    if (!selectedSceneId && !selectedIndex) return;

    if (selectedIndex === 'VRA_ZONES' && selectedEntityId) {
      const apiBaseUrl = getApiBaseUrl();
      const geoJsonUrl = `${apiBaseUrl}/api/vegetation/jobs/zoning/${selectedEntityId}/geojson`;
      Cesium.GeoJsonDataSource.load(geoJsonUrl, {
        stroke: Cesium.Color.BLACK,
        fill: Cesium.Color.BLUE.withAlpha(0.5),
        strokeWidth: 3,
      })
        .then((dataSource: any) => {
          if (viewer.isDestroyed()) return;
          const entities = dataSource.entities.values;
          for (let i = 0; i < entities.length; i++) {
            const entity = entities[i];
            const clusterId = entity.properties.cluster_id?.getValue();
            const colors = [
              Cesium.Color.RED.withAlpha(0.6),
              Cesium.Color.ORANGE.withAlpha(0.6),
              Cesium.Color.YELLOW.withAlpha(0.6),
              Cesium.Color.GREEN.withAlpha(0.6),
              Cesium.Color.BLUE.withAlpha(0.6),
            ];
            const color = colors[clusterId % colors.length] || Cesium.Color.GRAY.withAlpha(0.6);
            entity.polygon.material = color;
            entity.polygon.outline = true;
            entity.polygon.outlineColor = Cesium.Color.BLACK;
            entity.polygon.extrudedHeight = 10;
          }
          viewer.dataSources.add(dataSource);
          dataSourceRef.current = dataSource;
          viewer.flyTo(dataSource);
        })
        .catch((err: any) => {
          console.error('[VegetationLayer] Error loading zones:', err);
        });
      return;
    }

    if (selectedIndex && selectedSceneId) {
      addRasterLayer(viewer, selectedSceneId, selectedIndex);
    }

    return () => {
      if (viewer && layerRef.current) {
        viewer.imageryLayers.remove(layerRef.current, true);
        layerRef.current = null;
      }
    };
  }, [viewer, selectedIndex, selectedSceneId, addRasterLayer]);


  return null;
};

export default VegetationLayer;
