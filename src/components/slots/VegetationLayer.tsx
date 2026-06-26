/**
 * Vegetation Layer - Cesium raster overlay from job results.
 *
 * Supports two scopes:
 *   - 'selected': single ImageryLayer for the currently-selected parcel
 *   - 'all': one ImageryLayer per parcel with latest data (fetched from /results/latest)
 *
 * layerVisible defaults to false — user opts in from the Capas menu.
 */

import React, { useEffect, useRef, useState, useMemo } from 'react';
import { createPortal } from 'react-dom';

import { useViewerOptional } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';

export const VegetationLayer: React.FC = () => {
  const viewerCtx = useViewerOptional();
  const viewer = viewerCtx?.cesiumViewer;
  const {
    selectedIndex, setSelectedIndex,
    activeJobId, setActiveJobId,
    activeRasterPath, setActiveRasterPath,
    selectedEntityId, selectedSceneId,
    indexResults, setIndexResults,
    selectedDate, setSelectedSceneId, setSelectedDate,
    layerOpacity,
    layerVisible,
    layerScope,
    entityDataStatus,
  } = useVegetationContext();
  const api = useVegetationApi();
  const layerRefs = useRef<any[]>([]);
  const dataSourceRef = useRef<any>(null);

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

        // Fallback: a stale selectedSceneId from a previous session can
        // scope the query to a scene that no longer matches anything,
        // returning indices={}. Drop the scene filter and re-fetch the
        // latest-per-index so the slot recovers without forcing the user
        // to deselect/reselect the parcel.
        if (sceneToUse && keys.length === 0) {
          setSelectedSceneId(null);
          sceneToUse = null;
          data = await api.getEntityResults(selectedEntityId);
          if (cancelled) return;
          keys = Object.keys(data.indices || {});
        }

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

    // Cleanup all prior imagery layers and any GeoJSON data source
    layerRefs.current.forEach(l => {
      try { viewer.imageryLayers.remove(l, true); } catch { /* destroyed */ }
    });
    layerRefs.current = [];
    if (dataSourceRef.current) {
      try { viewer.dataSources.remove(dataSourceRef.current); } catch { /* destroyed */ }
      dataSourceRef.current = null;
    }

    // VRA_ZONES branch — keep the existing behavior verbatim
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

    if (!layerVisible || !selectedIndex) return;

    const apiBase = window.location.origin;

    const addImagery = (
      rasterPath: string | null,
      jobId: string | null,
      tileToken: string | null | undefined,
      bounds: [number, number, number, number] | null,
      minzoom: number | null,
      maxzoom: number | null,
    ) => {
      let tileUrl: string;
      let boundsUrl: string;
      if (jobId && tileToken) {
        const token = encodeURIComponent(tileToken);
        tileUrl = `${apiBase}/api/vegetation/tiles/${jobId}/{z}/{x}/{y}.png?index=${selectedIndex}&token=${token}`;
        boundsUrl = `${apiBase}/api/vegetation/tiles/${jobId}/bounds`;
      } else if (jobId) {
        // Legacy fallback — will 401 without token; prefer fixing API to always return tile_token
        tileUrl = `${apiBase}/api/vegetation/tiles/${jobId}/{z}/{x}/{y}.png?index=${selectedIndex}`;
        boundsUrl = `${apiBase}/api/vegetation/tiles/${jobId}/bounds`;
      } else if (rasterPath) {
        const encoded = encodeURIComponent(rasterPath);
        tileUrl = `${apiBase}/api/vegetation/tiles/render/{z}/{x}/{y}.png?raster_path=${encoded}&index=${selectedIndex}`;
        boundsUrl = `${apiBase}/api/vegetation/tiles/bounds?raster_path=${encoded}`;
      } else {
        return;
      }

      const mountWithBounds = (b: [number, number, number, number], mz: number, Mz: number) => {
        try {
          const provider = new Cesium.UrlTemplateImageryProvider({
            url: tileUrl,
            rectangle: Cesium.Rectangle.fromDegrees(b[0], b[1], b[2], b[3]),
            minimumLevel: mz,
            maximumLevel: Mz,
            credit: 'Vegetation Prime',
          });
          const layer = viewer.imageryLayers.addImageryProvider(provider);
          layer.alpha = (layerOpacity ?? 75) / 100;
          layerRefs.current.push(layer);
        } catch (err) {
          console.error('[VegetationLayer] Error creating imagery layer:', err);
        }
      };

      if (bounds && minzoom != null && maxzoom != null) {
        mountWithBounds(bounds, minzoom, maxzoom);
      } else {
        fetch(boundsUrl)
          .then(r => r.ok ? r.json() : null)
          .then(data => {
            if (data?.bounds) mountWithBounds(data.bounds, data.minzoom ?? 10, data.maxzoom ?? 18);
          })
          .catch(() => { /* skip this raster */ });
      }
    };

    if (layerScope === 'selected') {
      const rasterPath = activeRasterPath;
      const jobId = activeJobId || (selectedIndex && indexResults?.[selectedIndex]?.job_id) || null;
      const tileToken = (selectedIndex && indexResults?.[selectedIndex]?.tile_token) || null;
      if (!rasterPath && !jobId) return;
      if (!selectedEntityId) return;
      addImagery(rasterPath, jobId, tileToken, null, null, null);
      return;
    }

    // layerScope === 'all'
    (async () => {
      try {
        const items = await api.getLatestResultsAllEntities(selectedIndex);
        items.forEach(it => addImagery(
          it.raster_path,
          it.job_id,
          it.tile_token,
          it.bounds as any,
          it.minzoom,
          it.maxzoom,
        ));
      } catch (err) {
        console.error('[VegetationLayer] Error loading all-entity results:', err);
      }
    })();

    return () => {
      layerRefs.current.forEach(l => {
        try {
          if (viewer && !viewer.isDestroyed()) viewer.imageryLayers.remove(l, true);
        } catch { /* ok */ }
      });
      layerRefs.current = [];
    };
  }, [viewer, selectedIndex, activeJobId, activeRasterPath, selectedEntityId, indexResults, layerScope, layerVisible]);

  // Update opacity and visibility without recreating layers
  useEffect(() => {
    layerRefs.current.forEach(l => {
      l.alpha = layerVisible ? (layerOpacity ?? 75) / 100 : 0;
      l.show = layerVisible;
    });
    if (dataSourceRef.current) dataSourceRef.current.show = layerVisible;
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
  const hasLayer = !!(layerRefs.current.length || dataSourceRef.current);
  const dateLabel = useMemo(() => {
    if (!selectedDate || !hasLayer) return null;
    return selectedDate.toLocaleDateString(undefined, {
      day: '2-digit', month: 'short', year: 'numeric',
    });
  }, [selectedDate, hasLayer]);

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
      <div className="bg-slate-900 text-white px-4 py-1.5 rounded-full text-sm font-medium flex items-center gap-2 shadow-lg border border-slate-700">
        <span className={`w-2 h-2 rounded-full ${isTransitioning ? 'bg-emerald-400 animate-pulse' : 'bg-emerald-500'}`} />
        <span>{selectedIndex}</span>
        <span className="text-slate-400">·</span>
        <span>{dateLabel}</span>
      </div>
    </div>,
    cesiumContainer,
  );
};

export default VegetationLayer;
