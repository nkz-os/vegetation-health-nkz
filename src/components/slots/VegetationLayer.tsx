/**
 * Vegetation Layer - Cesium raster overlay from job results.
 *
 * Uses activeJobId from context to render tiles via the backend tile endpoint.
 * The tile endpoint looks up the job's COG path in the database.
 */

import React, { useEffect, useRef } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';

interface VegetationLayerProps {
  viewer?: any; // Injected by CesiumMap
}

export const VegetationLayer: React.FC<VegetationLayerProps> = ({ viewer }) => {
  const { selectedIndex, activeJobId, selectedEntityId, indexResults } = useVegetationContext();
  const layerRef = useRef<any>(null);
  const dataSourceRef = useRef<any>(null);

  useEffect(() => {
    if (!viewer) return;

    const Cesium = (window as any).Cesium;
    if (!Cesium) return;

    // Clean up previous layers
    if (layerRef.current) {
      try {
        viewer.imageryLayers.remove(layerRef.current, true);
      } catch (_) { /* viewer may be destroyed */ }
      layerRef.current = null;
    }
    if (dataSourceRef.current) {
      try {
        viewer.dataSources.remove(dataSourceRef.current);
      } catch (_) { /* viewer may be destroyed */ }
      dataSourceRef.current = null;
    }

    // VRA Zones mode (GeoJSON overlay)
    if (selectedIndex === 'VRA_ZONES' && selectedEntityId) {
      const geoJsonUrl = `/api/vegetation/jobs/zoning/${encodeURIComponent(selectedEntityId)}/geojson`;
      Cesium.GeoJsonDataSource.load(geoJsonUrl, {
        stroke: Cesium.Color.BLACK,
        fill: Cesium.Color.BLUE.withAlpha(0.5),
        strokeWidth: 3,
      })
        .then((dataSource: any) => {
          if (viewer.isDestroyed()) return;
          const entities = dataSource.entities.values;
          const colors = [
            Cesium.Color.RED.withAlpha(0.6),
            Cesium.Color.ORANGE.withAlpha(0.6),
            Cesium.Color.YELLOW.withAlpha(0.6),
            Cesium.Color.GREEN.withAlpha(0.6),
            Cesium.Color.BLUE.withAlpha(0.6),
          ];
          for (let i = 0; i < entities.length; i++) {
            const entity = entities[i];
            const clusterId = entity.properties?.cluster_id?.getValue() || 0;
            entity.polygon.material = colors[clusterId % colors.length] || Cesium.Color.GRAY.withAlpha(0.6);
            entity.polygon.outline = true;
            entity.polygon.outlineColor = Cesium.Color.BLACK;
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

    // Raster tile mode — COG tiles served by backend /api/vegetation/tiles/{job_id}/{z}/{x}/{y}.png
    // Resolve jobId: prefer activeJobId, fall back to indexResults for the selected index
    const jobId = activeJobId
      || (selectedIndex && indexResults?.[selectedIndex]?.job_id)
      || null;

    if (!jobId || !selectedIndex || !selectedEntityId) return;

    const apiBase = window.location.origin;
    const tileUrl = `${apiBase}/api/vegetation/tiles/${jobId}/{z}/{x}/{y}.png`;

    try {
      const provider = new Cesium.UrlTemplateImageryProvider({
        url: tileUrl,
        minimumLevel: 10,
        maximumLevel: 18,
        credit: 'Vegetation Prime',
      });

      const layer = viewer.imageryLayers.addImageryProvider(provider);
      layer.alpha = 0.75;
      layerRef.current = layer;

      console.log('[VegetationLayer] Raster overlay added for', selectedIndex, 'job', jobId.substring(0, 8));
    } catch (err) {
      console.error('[VegetationLayer] Error creating imagery layer:', err);
    }

    return () => {
      if (viewer && !viewer.isDestroyed() && layerRef.current) {
        try {
          viewer.imageryLayers.remove(layerRef.current, true);
        } catch (_) { /* ok */ }
        layerRef.current = null;
      }
    };
  }, [viewer, selectedIndex, activeJobId, selectedEntityId, indexResults]);

  return null;
};

export default VegetationLayer;
