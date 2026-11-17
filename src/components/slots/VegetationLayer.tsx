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

import { useViewerOptional, useTranslation } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { API_ORIGIN } from '../../services/apiOrigin';

export const VegetationLayer: React.FC = () => {
  const viewerCtx = useViewerOptional();
  const { t } = useTranslation();
  const viewer = viewerCtx?.cesiumViewer;
  const {
    selectedIndex, setSelectedIndex,
    activeJobId, setActiveJobId,
    activeRasterPath, setActiveRasterPath,
    selectedEntityId, selectedSceneId, selectedSensingDate,
    indexResults, setIndexResults,
    selectedDate, setSelectedSceneId, setSelectedSensingDate, setSelectedDate,
    layerOpacity,
    layerVisible,
    layerScope,
    entityDataStatus,
  } = useVegetationContext();
  const api = useVegetationApi();
  const layerRefs = useRef<any[]>([]);
  const dataSourceRef = useRef<any>(null);
  const clickHandlerRef = useRef<any>(null);
  // Card of the clicked zoning zone (the host viewer has no info box).
  const [zoneCard, setZoneCard] = useState<null | {
    level: string; pct: number; area: number | null; confirming: number | null; total: number | null;
  }>(null);

  // Resolve rasters for the selected acquisition: a scene (local engine) or,
  // for Copernicus acquisitions that have no scene, a sensing date. Without a
  // selection, infer the default from the latest NDVI job metadata.
  useEffect(() => {
    if (!selectedEntityId) return;
    let cancelled = false;

    (async () => {
      try {
        let sceneToUse = selectedSceneId;
        let dateToUse = sceneToUse ? null : selectedSensingDate;
        let data = await api.getEntityResults(
          selectedEntityId,
          sceneToUse ? { sceneId: sceneToUse } : dateToUse ? { sensingDate: dateToUse } : undefined,
        );
        if (cancelled) return;

        let keys = Object.keys(data.indices || {});

        // Fallback: a stale selection (or a Copernicus raster that failed to
        // materialize) can scope the query to nothing, returning indices={}.
        // Drop the filter and re-fetch the latest-per-index so the slot
        // recovers without forcing the user to deselect/reselect the parcel.
        if ((sceneToUse || dateToUse) && keys.length === 0) {
          setSelectedSceneId(null);
          setSelectedSensingDate(null);
          sceneToUse = null;
          dateToUse = null;
          data = await api.getEntityResults(selectedEntityId);
          if (cancelled) return;
          keys = Object.keys(data.indices || {});
        }

        if (!sceneToUse && !dateToUse && keys.length > 0) {
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
        // Zones are not a per-date raster, so they never appear in the results:
        // keep them selected across dates (data-status drops them if absent).
        if (selectedIndex !== 'VRA_ZONES' && (!selectedIndex || !data.indices![selectedIndex])) {
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
  }, [selectedEntityId, selectedSceneId, selectedSensingDate]); // eslint-disable-line react-hooks/exhaustive-deps

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

    if (clickHandlerRef.current) {
      try { clickHandlerRef.current.destroy(); } catch { /* destroyed */ }
      clickHandlerRef.current = null;
    }
    setZoneCard(null);

    // VRA_ZONES branch: zones of the latest zoning job, coloured by level.
    if (selectedIndex === 'VRA_ZONES' && selectedEntityId) {
      const geoJsonUrl = `${API_ORIGIN}/api/vegetation/jobs/zoning/${encodeURIComponent(selectedEntityId)}/geojson`;
      fetch(geoJsonUrl, { credentials: 'include' })
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((geojson: any) => Cesium.GeoJsonDataSource.load(geojson, {
          clampToGround: true,
        }))
        .then((dataSource: any) => {
          if (viewer.isDestroyed()) return;
          // Multi-date proposal: one zone per level (high/medium/low/critical).
          const LEVEL_COLORS: Record<string, string> = {
            high: '#1b5e20', medium: '#81c784', low: '#c8a24a', critical: '#c62828',
          };
          const LEGACY_COLORS = ['#c62828', '#fb8c00', '#fdd835', '#43a047', '#1e88e5'];
          const entities = [...dataSource.entities.values];
          for (const entity of entities) {
            if (!entity.polygon) continue;
            const prop = (k: string) => entity.properties?.[k]?.getValue?.();
            const level: string | undefined = prop('zone_level');
            const css = level ? LEVEL_COLORS[level] : LEGACY_COLORS[(prop('cluster_id') || 0) % LEGACY_COLORS.length];
            const color = Cesium.Color.fromCssColorString(css || '#9e9e9e');
            entity.polygon.material = color.withAlpha(0.45);
            entity.polygon.outline = false;
            const hierarchy = entity.polygon.hierarchy?.getValue?.(Cesium.JulianDate.now());
            const ring = hierarchy?.positions;
            if (ring && ring.length > 1) {
              // Terrain-clamped polygons draw no outline: a ground polyline does.
              dataSource.entities.add({
                polyline: {
                  positions: [...ring, ring[0]],
                  clampToGround: true,
                  width: level === 'critical' ? 4 : 1.5,
                  material: level === 'critical' ? color : Cesium.Color.WHITE.withAlpha(0.8),
                },
              });
            }
            if (!level) continue;
            const pct: number = prop('relative_pct') ?? 0;
            const pctText = `${pct > 0 ? '+' : ''}${pct} %`;
            const levelText = t(`zoning.level.${level}`, level);
            entity.name = `${levelText} ${pctText}`;
            const anchor = prop('label_point');
            if (ring && ring.length > 2) {
              // The backend sends a point inside the zone; a ring centre can fall in another zone.
              const centre = Array.isArray(anchor)
                ? Cesium.Cartesian3.fromDegrees(anchor[0], anchor[1])
                : Cesium.BoundingSphere.fromPoints(ring).center;
              dataSource.entities.add({
                position: centre,
                label: {
                  text: `${levelText} ${pctText}`,
                  font: '13px sans-serif',
                  fillColor: Cesium.Color.WHITE,
                  outlineColor: Cesium.Color.BLACK,
                  outlineWidth: 2,
                  style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                  heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
                  disableDepthTestDistance: Number.POSITIVE_INFINITY,
                },
              });
            }
          }
          viewer.dataSources.add(dataSource);
          dataSourceRef.current = dataSource;
          const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
          handler.setInputAction((click: any) => {
            const picked = viewer.scene.pick(click.position);
            const props = picked?.id?.properties;
            const level = props?.zone_level?.getValue?.();
            if (!level || !dataSource.entities.contains(picked.id)) return;
            setZoneCard({
              level,
              pct: props.relative_pct?.getValue?.() ?? 0,
              area: props.area_ha?.getValue?.() ?? null,
              confirming: props.confirming_dates?.getValue?.() ?? null,
              total: props.total_dates?.getValue?.() ?? null,
            });
          }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
          clickHandlerRef.current = handler;
          viewer.flyTo(dataSource);
        })
        .catch((err: any) => {
          console.error('[VegetationLayer] Error loading zones:', err);
        });
      return;
    }

    // Tenant-wide view with no parcel picked yet: default to NDVI.
    const index = selectedIndex ?? (layerScope === 'all' ? 'NDVI' : null);
    if (!layerVisible || !index) return;

    const apiBase = API_ORIGIN;

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
        fetch(boundsUrl, { credentials: 'include' })
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
        const items = await api.getLatestResultsAllEntities(index);
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
  }, [viewer, selectedIndex, activeJobId, activeRasterPath, selectedEntityId, indexResults, layerScope, layerVisible, t]);

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

  // Date badge — render over the Cesium canvas via portal. It names the acquisition
  // the raster belongs to, not the timeline cursor: `selectedDate` follows the
  // cursor, which can sit up to two weeks away from the nearest acquisition.
  // Acquisition days are UTC calendar days, so they are formatted in UTC.
  const hasLayer = !!(layerRefs.current.length || dataSourceRef.current);
  const dateLabel = useMemo(() => {
    const shown = selectedSensingDate ? new Date(selectedSensingDate) : selectedDate;
    if (!shown || !hasLayer) return null;
    return shown.toLocaleDateString(undefined, {
      day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC',
    });
  }, [selectedSensingDate, selectedDate, hasLayer]);

  const cesiumContainer = typeof document !== 'undefined'
    ? document.querySelector('.cesium-viewer') || document.getElementById('cesiumContainer')
    : null;

  const pctText = zoneCard ? `${zoneCard.pct > 0 ? '+' : ''}${zoneCard.pct} %` : '';
  const card = zoneCard && typeof document !== 'undefined' ? createPortal(
    <div className="fixed bottom-24 right-6 z-50 w-64 bg-white rounded-lg shadow-lg border border-slate-200 p-3 text-xs text-slate-700">
      <div className="flex items-start justify-between gap-2 mb-1">
        <span className="text-sm font-semibold">
          {t(`zoning.level.${zoneCard.level}`, zoneCard.level)} {pctText}
        </span>
        <button
          type="button"
          onClick={() => setZoneCard(null)}
          className="text-slate-500 hover:text-slate-700"
          aria-label={t('common.close', 'Close')}
        >
          ×
        </button>
      </div>
      {zoneCard.area != null && <p>{zoneCard.area} ha</p>}
      <p>{t('zoning.relativeVigour', 'Vigour {{pct}} vs parcel mean', { pct: pctText })}</p>
      {zoneCard.total != null && (
        <p>{t('zoning.confirmedDates', 'Consistent with its level on {{n}} of {{total}} dates', {
          n: zoneCard.confirming, total: zoneCard.total,
        })}</p>
      )}
      {zoneCard.level === 'critical' && (
        <p className="mt-1 font-semibold text-amber-700">{t('zoning.reviewInField', 'Review in the field')}</p>
      )}
    </div>,
    document.body,
  ) : null;

  if (!hasLayer || !dateLabel || !cesiumContainer) return card;

  return (
    <>
      {card}
      {createPortal(
    <div
      className={`absolute bottom-16 left-1/2 -translate-x-1/2 z-50 pointer-events-none transition-all duration-500 ${
        isTransitioning ? 'scale-110 opacity-100' : 'scale-100 opacity-80'
      }`}
    >
      <div className="bg-slate-900 text-white px-4 py-1.5 rounded-full text-sm font-medium flex items-center gap-2 shadow-lg border border-slate-700">
        <span className={`w-2 h-2 rounded-full ${isTransitioning ? 'bg-nkz-success animate-pulse' : 'bg-emerald-500'}`} />
        <span>{selectedIndex}</span>
        <span className="text-slate-400">·</span>
        <span>{dateLabel}</span>
      </div>
    </div>,
        cesiumContainer,
      )}
    </>
  );
};

export default VegetationLayer;
