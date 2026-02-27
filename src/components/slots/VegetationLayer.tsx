/**
 * Vegetation Layer - Cesium + TiTiler (Phase 4)
 * Raster: GET viewer-url → presigned TiTiler template → UrlTemplateImageryProvider.
 * Vector: VRA zones via GeoJsonDataSource. No Deck.gl in this slot.
 */

import React, { useEffect, useRef, useCallback } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../hooks/useVegetationApi';

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

// Debounce: ensure we don't fire many refresh requests when many tiles fail at once
const REFRESH_DEBOUNCE_MS = 800;

export const VegetationLayer: React.FC<VegetationLayerProps> = ({ viewer }) => {
  const api = useVegetationApi();
  const { selectedIndex, selectedDate, selectedSceneId } = useVegetationContext();
  const layerRef = useRef<any>(null);
  const dataSourceRef = useRef<any>(null);
  const refreshTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshInFlightRef = useRef(false);

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
          } catch (_) {}
          currentProvider = null;
        }
      };

      const applyNewTemplate = (tileUrlTemplate: string) => {
        if (!viewerRef || viewerRef.isDestroyed()) return;
        removeCurrentRaster();
        const provider = new Cesium.UrlTemplateImageryProvider({
          url: tileUrlTemplate,
          maximumLevel: 18,
          minimumLevel: 8,
          hasAlphaChannel: true,
          credit: 'Vegetation Prime (TiTiler)',
        });
        currentProvider = provider;
        const layer = viewerRef.imageryLayers.addImageryProvider(provider);
        layer.alpha = 0.8;
        currentLayer = layer;
        layerRef.current = layer;

        provider.errorEvent.addEventListener(() => {
          if (refreshInFlightRef.current) return;
          if (refreshTimeoutRef.current) return;
          refreshTimeoutRef.current = setTimeout(() => {
            refreshTimeoutRef.current = null;
            refreshInFlightRef.current = true;
            api
              .getViewerUrl(sceneId, indexType)
              .then((res) => {
                if (viewerRef && !viewerRef.isDestroyed()) applyNewTemplate(res.tileUrlTemplate);
              })
              .catch(() => {})
              .finally(() => {
                refreshInFlightRef.current = false;
              });
          }, REFRESH_DEBOUNCE_MS);
        });
      };

      api
        .getViewerUrl(sceneId, indexType)
        .then((res) => applyNewTemplate(res.tileUrlTemplate))
        .catch((err) => {
          console.error('[VegetationLayer] Failed to load viewer URL:', err);
        });

      return removeCurrentRaster;
    },
    [api]
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

    if (selectedIndex === 'VRA_ZONES' && selectedSceneId) {
      const apiBaseUrl = getApiBaseUrl();
      const geoJsonUrl = `${apiBaseUrl}/api/vegetation/jobs/zoning/${selectedSceneId}/geojson`;
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
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current);
        refreshTimeoutRef.current = null;
      }
      if (viewer && layerRef.current) {
        viewer.imageryLayers.remove(layerRef.current, true);
        layerRef.current = null;
      }
    };
  }, [viewer, selectedIndex, selectedDate, selectedSceneId, addRasterLayer]);

  return null;
};

export default VegetationLayer;
