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
import { syncZoningPendingFlag, useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { API_ORIGIN } from '../../services/apiOrigin';
import { useAuth } from '../../hooks/useAuth';
import { ZoneCard, canEditZoning, type ZoneCardLevel, type ZoneCardZone } from '../zoning/ZoneCard';
import { PendingPanel } from '../zoning/PendingPanel';
import type { PendingZoning } from '../../types';

/** Card data of a zone feature drawn by the zoning layer (Cesium property bag). */
function zoneFromProps(props: any): ZoneCardZone | null {
  const get = (k: string) => props?.[k]?.getValue?.();
  const level = get('zone_level');
  if (!level) return null;
  const entityId = get('entity_id') ?? null;
  const runId = get('run_id') ?? null;
  return {
    // Zonings from before versions carry no ids: their card is read-only.
    entityId: entityId ?? `legacy:${get('zone_id')}`,
    runId: runId ?? '',
    editable: !!entityId && !!runId,
    level,
    rank: get('zone_id'),
    pct: get('relative_pct') ?? 0,
    area: get('area_ha') ?? null,
    confirming: get('confirming_dates') ?? null,
    total: get('total_dates') ?? null,
    accepted: get('accepted') === true,
    acceptedAt: get('accepted_at') ?? null,
    cause: get('technician_cause') ?? null,
  };
}

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
  const [zoneCard, setZoneCard] = useState<ZoneCardZone | null>(null);
  const [zoneLevels, setZoneLevels] = useState<ZoneCardLevel[]>([]);
  const [zoningPending, setZoningPending] = useState(false);
  // Bumped after an edit so the zoning layer is drawn again from the server.
  const [zoningReload, setZoningReload] = useState(0);
  // Zone whose card reopens once the layer is drawn again (an edit that keeps the zone ids).
  const reopenZoneRef = useRef<string | null>(null);
  const zonesFlownToRef = useRef<string | null>(null);
  const [pendingOpen, setPendingOpen] = useState(false);
  // Shown when an edit found the zoning changed underneath (409/404): the layer is redrawn.
  const [zoningNotice, setZoningNotice] = useState<string | null>(null);
  const [proposalOutline, setProposalOutline] = useState<PendingZoning['geojson'] | null>(null);
  const outlineRef = useRef<any>(null);
  const { roles } = useAuth();
  const canEdit = canEditZoning(roles);

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

  // The pending panel belongs to the zoning of one parcel.
  useEffect(() => {
    setPendingOpen(false);
    setProposalOutline(null);
  }, [selectedEntityId, selectedIndex]);

  // Outline of a pending proposal over the zoning in force: borders only, never picked as a zone.
  useEffect(() => {
    if (!viewer) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    let cancelled = false;
    const drop = () => {
      if (outlineRef.current && !viewer.isDestroyed()) {
        try { viewer.dataSources.remove(outlineRef.current, true); } catch { /* destroyed */ }
      }
      outlineRef.current = null;
    };
    drop();
    if (proposalOutline && proposalOutline.features.length > 0) {
      const outline = new Cesium.CustomDataSource('zoning-proposal-outline');
      Cesium.GeoJsonDataSource.load(proposalOutline, { clampToGround: true })
        .then((loaded: any) => {
          if (cancelled || viewer.isDestroyed()) return;
          for (const entity of loaded.entities.values) {
            const ring = entity.polygon?.hierarchy?.getValue?.(Cesium.JulianDate.now())?.positions;
            if (!ring || ring.length < 2) continue;
            outline.entities.add({
              polyline: {
                positions: [...ring, ring[0]],
                clampToGround: true,
                width: 3,
                material: new Cesium.PolylineDashMaterialProperty({
                  color: Cesium.Color.YELLOW, gapColor: Cesium.Color.BLACK.withAlpha(0.6), dashLength: 12,
                }),
              },
            });
          }
          viewer.dataSources.add(outline);
          outlineRef.current = outline;
        })
        .catch((err: any) => console.error('[VegetationLayer] Error drawing the proposal:', err));
    }
    return () => { cancelled = true; drop(); };
  }, [viewer, proposalOutline]);

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
      // A late answer for another parcel or index must not draw, flag or open anything.
      let cancelled = false;
      const geoJsonUrl = `${API_ORIGIN}/api/vegetation/jobs/zoning/${encodeURIComponent(selectedEntityId)}/geojson`;
      fetch(geoJsonUrl, { credentials: 'include' })
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((geojson: any) => {
          if (cancelled) return null;
          setZoningPending(geojson?.has_pending === true);
          syncZoningPendingFlag(geojson?.has_pending === true);
          return Cesium.GeoJsonDataSource.load(geojson, { clampToGround: true });
        })
        .then((dataSource: any) => {
          if (cancelled || !dataSource || viewer.isDestroyed()) return;
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
          const zones = entities
            .map((e: any) => zoneFromProps(e.properties))
            .filter((z: ZoneCardZone | null): z is ZoneCardZone => z !== null);
          setZoneLevels(zones.map((z: ZoneCardZone) => ({ rank: z.rank, level: z.level, area: z.area })));
          const reopen = reopenZoneRef.current;
          reopenZoneRef.current = null;
          if (reopen) setZoneCard(zones.find((z: ZoneCardZone) => z.entityId === reopen) ?? null);
          const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
          handler.setInputAction((click: any) => {
            const picked = viewer.scene.pick(click.position);
            if (!picked?.id || !dataSource.entities.contains(picked.id)) return;
            const zone = zoneFromProps(picked.id.properties);
            if (zone) { setZoningNotice(null); setZoneCard(zone); }
          }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
          clickHandlerRef.current = handler;
          // Fly once per parcel: redraws (an edit, another date) keep the camera where it is.
          if (zonesFlownToRef.current !== selectedEntityId) {
            zonesFlownToRef.current = selectedEntityId;
            viewer.flyTo(dataSource);
          }
        })
        .catch((err: any) => {
          if (!cancelled) console.error('[VegetationLayer] Error loading zones:', err);
        });
      return () => { cancelled = true; };
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
  }, [viewer, selectedIndex, activeJobId, activeRasterPath, selectedEntityId, indexResults, layerScope, layerVisible, t, zoningReload]);

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

  const staleZoning = (message: string) => {
    setZoningNotice(message);
    setZoneCard(null);
    setPendingOpen(false);
    setProposalOutline(null);
    setZoningReload((n) => n + 1);
  };

  const notice = zoningNotice && !zoneCard && typeof document !== 'undefined' ? createPortal(
    <div className="fixed top-24 right-4 z-nkz-modal w-80 bg-nkz-surface rounded-nkz-md shadow-lg border border-nkz-border p-3 text-nkz-xs text-nkz-text-primary flex items-start justify-between gap-2">
      <span>{zoningNotice}</span>
      <button
        type="button"
        onClick={() => setZoningNotice(null)}
        className="text-nkz-text-muted hover:text-nkz-text-primary"
        aria-label={t('common.close', 'Close')}
      >
        ×
      </button>
    </div>,
    document.body,
  ) : null;

  const card = zoneCard && selectedEntityId && typeof document !== 'undefined' ? createPortal(
    <ZoneCard
      key={zoneCard.entityId}
      parcelId={selectedEntityId}
      zone={zoneCard}
      levels={zoneLevels}
      hasPending={zoningPending}
      canEdit={canEdit && zoneCard.editable}
      onChanged={(idsChanged) => {
        reopenZoneRef.current = idsChanged ? null : zoneCard.entityId;
        if (idsChanged) {
          // New zone ids (merge): the card and any pending proposal shown are gone.
          setZoneCard(null);
          setPendingOpen(false);
          setProposalOutline(null);
        }
        setZoningReload((n) => n + 1);
      }}
      onStale={staleZoning}
      onOpenPending={() => setPendingOpen(true)}
      onClose={() => setZoneCard(null)}
    />,
    document.body,
  ) : null;

  const panel = pendingOpen && selectedEntityId && typeof document !== 'undefined' ? createPortal(
    <PendingPanel
      parcelId={selectedEntityId}
      canEdit={canEdit}
      onPreview={setProposalOutline}
      onStale={staleZoning}
      onDone={() => {
        setPendingOpen(false);
        setProposalOutline(null);
        setZoneCard(null);
        setZoningReload((n) => n + 1);
      }}
      onClose={() => { setPendingOpen(false); setProposalOutline(null); }}
    />,
    document.body,
  ) : null;

  if (!hasLayer || !dateLabel || !cesiumContainer) return <>{card}{panel}{notice}</>;

  return (
    <>
      {card}
      {panel}
      {notice}
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
