/**
 * Vegetation Layer - Cesium raster overlay from job results.
 *
 * Uses activeJobId from context to render tiles via the backend tile endpoint.
 * The tile endpoint looks up the job's COG path in the database.
 *
 * If no indexResults are loaded (e.g. unified viewer), auto-fetches them once.
 */

import React, { useEffect, useRef, useState, useMemo } from 'react';
import { createPortal } from 'react-dom';

import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';

interface TileBounds {
  bounds: [number, number, number, number]; // [west, south, east, north]
  minzoom: number;
  maxzoom: number;
}

interface VegetationLayerProps {
  viewer?: any; // Injected by CesiumMap
}

export const VegetationLayer: React.FC<VegetationLayerProps> = ({ viewer }) => {
  const {
    selectedIndex, setSelectedIndex,
    activeJobId, setActiveJobId,
    activeRasterPath, setActiveRasterPath,
    selectedEntityId, selectedSceneId,
    indexResults, setIndexResults,
    selectedDate, setSelectedSceneId, setSelectedDate,
    layerOpacity,
    layerVisible,
    entityDataStatus,
  } = useVegetationContext();
  const api = useVegetationApi();
  const layerRef = useRef<any>(null);
  const dataSourceRef = useRef<any>(null);
  const [tileBounds, setTileBounds] = useState<Record<string, TileBounds>>({});

  // Resolve rasters per selected scene (DateSelector). If no scene yet, infer default from NDVI job metadata.
  useEffect(() => {
    if (!selectedEntityId) return;
    let cancelled = false;

    (async () => {
      try {
        let sceneToUse = selectedSceneId;
        let data = await api.getEntityResults(
          selectedEntityId,
          sceneToUse ? { sceneId: sceneToUse } : undefined,
        );
        if (cancelled) return;

        let keys = Object.keys(data.indices || {});

        if (!sceneToUse && keys.length > 0) {
          const preferredKey = entityDataStatus?.available_indices?.[0] || 'NDVI';
          const pivotKey = keys.includes(preferredKey) ? preferredKey
            : keys.includes('NDVI') ? 'NDVI'
            : keys[0];
          const pivot = data.indices![pivotKey];
          if (pivot?.scene_id && pivot.sensing_date) {
            sceneToUse = pivot.scene_id;
            setSelectedSceneId(pivot.scene_id);
            setSelectedDate(new Date(pivot.sensing_date));
            data = await api.getEntityResults(selectedEntityId, { sceneId: sceneToUse });
            if (cancelled) return;
            keys = Object.keys(data.indices || {});
          }
        }

        setIndexResults(data.indices || {});
        if (keys.length === 0) {
          setActiveJobId(null);
          setActiveRasterPath(null);
          return;
        }

        const idx =
          selectedIndex && data.indices![selectedIndex] ? selectedIndex : keys[0];
        if (!selectedIndex || !data.indices![selectedIndex]) {
          setSelectedIndex(idx);
        }
        setActiveJobId(data.indices![idx].job_id);
        setActiveRasterPath(data.indices![idx].raster_path ?? null);
      } catch {
        /* parcel may have no analysis yet */
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedEntityId, selectedSceneId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Switch raster when user picks another index (same scene scope)
  useEffect(() => {
    if (!selectedIndex || !indexResults[selectedIndex]) return;
    setActiveJobId(indexResults[selectedIndex].job_id);
    setActiveRasterPath(indexResults[selectedIndex].raster_path ?? null);
  }, [selectedIndex, indexResults, setActiveJobId, setActiveRasterPath]);

  // Cesium layer management
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

    // Raster tile mode — prefer raster_path (scene-specific), fall back to job_id
    const rasterPath = activeRasterPath;
    const jobId = activeJobId
      || (selectedIndex && indexResults?.[selectedIndex]?.job_id)
      || null;

    if ((!rasterPath && !jobId) || !selectedIndex || !selectedEntityId) return;

    // Build tile URL and bounds key
    const apiBase = window.location.origin;
    const boundsKey = rasterPath || jobId!;
    let tileUrl: string;
    let boundsUrl: string;

    if (rasterPath) {
      const encodedPath = encodeURIComponent(rasterPath);
      tileUrl = `${apiBase}/api/vegetation/tiles/render/{z}/{x}/{y}.png?raster_path=${encodedPath}&index=${selectedIndex}`;
      boundsUrl = `${apiBase}/api/vegetation/tiles/bounds?raster_path=${encodedPath}`;
    } else {
      tileUrl = `${apiBase}/api/vegetation/tiles/${jobId}/{z}/{x}/{y}.png?index=${selectedIndex}`;
      boundsUrl = `${apiBase}/api/vegetation/tiles/${jobId}/bounds`;
    }

    // Fetch COG bounds if not cached
    const cached = tileBounds[boundsKey];
    if (!cached) {
      fetch(boundsUrl)
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data?.bounds) {
            setTileBounds(prev => ({ ...prev, [boundsKey]: data }));
          }
        })
        .catch(() => {
          setTileBounds(prev => ({ ...prev, [boundsKey]: { bounds: [-180, -90, 180, 90], minzoom: 10, maxzoom: 18 } }));
        });
      return;
    }

    const [west, south, east, north] = cached.bounds;

    try {
      const provider = new Cesium.UrlTemplateImageryProvider({
        url: tileUrl,
        rectangle: Cesium.Rectangle.fromDegrees(west, south, east, north),
        minimumLevel: cached.minzoom || 10,
        maximumLevel: cached.maxzoom || 18,
        credit: 'Vegetation Prime',
      });

      const layer = viewer.imageryLayers.addImageryProvider(provider);
      layer.alpha = (layerOpacity ?? 75) / 100;
      layerRef.current = layer;

      // Fly the camera to the parcel extent so the user actually SEES the
      // newly-loaded layer instead of staring at a globe view with no clue
      // a layer exists somewhere in Spain.
      try {
        const rect = Cesium.Rectangle.fromDegrees(west, south, east, north);
        viewer.camera.flyTo({
          destination: rect,
          duration: 1.2,
        });
      } catch (_) {
        /* viewer destroyed mid-flight */
      }
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
  }, [viewer, selectedIndex, activeJobId, activeRasterPath, selectedEntityId, indexResults, tileBounds]);

  // Update opacity and visibility without recreating the layer
  useEffect(() => {
    if (layerRef.current) {
      layerRef.current.alpha = layerVisible ? (layerOpacity ?? 75) / 100 : 0;
      layerRef.current.show = layerVisible;
    }
    if (dataSourceRef.current) {
      dataSourceRef.current.show = layerVisible;
    }
  }, [layerOpacity, layerVisible]);

  // Fade flash when layer changes (brief opacity pulse)
  const [isTransitioning, setIsTransitioning] = useState(false);
  const prevRasterRef = useRef<string | null>(null);
  useEffect(() => {
    const key = activeRasterPath || activeJobId || null;
    if (key && key !== prevRasterRef.current) {
      prevRasterRef.current = key;
      setIsTransitioning(true);
      const timer = setTimeout(() => setIsTransitioning(false), 600);
      return () => clearTimeout(timer);
    }
  }, [activeRasterPath, activeJobId]);

  // Date badge — render over the Cesium canvas via portal
  const dateLabel = useMemo(() => {
    if (!selectedDate || !layerRef.current) return null;
    return selectedDate.toLocaleDateString('es-ES', {
      day: '2-digit', month: 'short', year: 'numeric',
    });
  }, [selectedDate, layerRef.current]);

  const hasLayer = !!(layerRef.current || dataSourceRef.current);
  const cesiumContainer = typeof document !== 'undefined'
    ? document.querySelector('.cesium-viewer') || document.getElementById('cesiumContainer')
    : null;

  if (!hasLayer || !dateLabel || !cesiumContainer) return null;

  return createPortal(
    <div
      className={`absolute bottom-16 left-1/2 -translate-x-1/2 z-50 pointer-events-none transition-all duration-500 ${
        isTransitioning ? 'scale-110 opacity-100' : 'scale-100 opacity-80'
      }`}
    >
      <div className="bg-black/70 text-white px-4 py-1.5 rounded-full text-sm font-medium backdrop-blur-sm flex items-center gap-2 shadow-lg">
        <span className={`w-2 h-2 rounded-full ${isTransitioning ? 'bg-emerald-400 animate-pulse' : 'bg-emerald-500'}`} />
        <span>{selectedIndex}</span>
        <span className="text-white/50">·</span>
        <span>{dateLabel}</span>
      </div>
    </div>,
    cesiumContainer,
  );
};

export default VegetationLayer;
