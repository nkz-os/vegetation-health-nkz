/**
 * Vegetation timeline track - the vegetation row of the unified viewer's shared
 * time axis (`timeline-track` slot).
 *
 * One marker per acquisition of the selected index, placed on the host axis at
 * the height of its mean value and joined by a line (a value series).
 * The host owns the cursor (`cursor` / ViewerContext.currentDate); this track
 * follows it and writes it only on an explicit click or key press.
 *
 * Positioning uses viewer-kit's row and inline styles: the host's Tailwind
 * build never scans module sources, so only utilities the host already ships
 * can be used as classes.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CloudOff } from 'lucide-react';
import {
  TimelineTrackRow,
  TimelineMarkers,
  nearestTime,
  isInRange,
  isoToUtcMs,
  DAY_MS,
  type TimelineMarker,
} from '@nekazari/viewer-kit';
import { useTranslation, useViewer, type TimelineTrackProps } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { IndexPillSelector, type CustomIndexOption } from '../widgets/IndexPillSelector';

interface TimelineTick {
  /** Scene UUID; null for acquisitions without a scene (Copernicus). */
  scene_id: string | null;
  sensing_date: string;
  mean_value: number | null;
  cloud_coverage: number | null;
  raster_path: string | null;
}

/** An acquisition follows the cursor only when it lies within this distance. */
const CURSOR_SNAP_MS = 15 * DAY_MS;
/**
 * Wait for the cursor (or a held key) to settle before showing an acquisition. Each selection makes the
 * backend materialize that date's rasters (a Copernicus call per index plus conversion and upload), and
 * dragging the axis crosses one acquisition after another. A click is never delayed.
 */
const SELECTION_DEBOUNCE_MS = 250;
/** Tall enough to read the series; the two label lines (track name + date shown on the map) fit with room. */
const TRACK_HEIGHT = 64;
/** Indices drawn on a fixed 0-1 scale. Everything else (SAR backscatter in dB, custom formulas) has no fixed scale. */
const FIXED_SCALE_INDICES: ReadonlySet<string> = new Set(['NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE']);
/** Autoscale margin kept above the maximum and below the minimum, as a fraction of the data span. */
const AUTOSCALE_PAD = 0.1;

function getTickColor(meanValue: number | null, indexType: string): string {
  if (indexType.startsWith('SAR')) return '#818cf8'; // backscatter (dB): no vigour scale
  if (meanValue == null) return '#94a3b8';
  if (meanValue >= 0.6) return '#22c55e';
  if (meanValue >= 0.3) return '#eab308';
  return '#ef4444';
}

const hasValue = (v: number | null): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * Height of each acquisition in the row, 0 = bottom, 1 = top, in the order given; undefined where there is no
 * value to place (the marker then stays centred).
 * Fixed-scale indices: the value itself, clamped to 0-1. Any other index: scaled to the min/max of the acquisitions
 * given (the ones inside the visible window) plus a margin; when they are all equal, mid-row.
 */
function seriesHeights(ticks: TimelineTick[], index: string): Array<number | undefined> {
  if (FIXED_SCALE_INDICES.has(index)) {
    return ticks.map((tk) => (hasValue(tk.mean_value) ? Math.min(1, Math.max(0, tk.mean_value)) : undefined));
  }
  let min = Infinity;
  let max = -Infinity;
  for (const tk of ticks) {
    if (!hasValue(tk.mean_value)) continue;
    if (tk.mean_value < min) min = tk.mean_value;
    if (tk.mean_value > max) max = tk.mean_value;
  }
  const span = max - min;
  return ticks.map((tk) => {
    if (!hasValue(tk.mean_value)) return undefined;
    if (span === 0) return 0.5;
    return (tk.mean_value - (min - span * AUTOSCALE_PAD)) / (span * (1 + 2 * AUTOSCALE_PAD));
  });
}

const formatDay = (iso: string, locale?: string): string =>
  new Date(isoToUtcMs(iso)).toLocaleDateString(locale, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });

/** Acquisitions are identified by scene, or by date when they have no scene. */
const tickId = (tick: TimelineTick): string => tick.scene_id ?? tick.sensing_date;

export const VegetationTimelineTrack: React.FC<TimelineTrackProps> = ({ entityId, range, cursor }) => {
  const { t, i18n } = useTranslation();
  const { setCurrentDate } = useViewer();
  const {
    selectedIndex,
    selectedSensingDate,
    setSelectedIndex,
    setSelectedDate,
    setSelectedSceneId,
    setSelectedSensingDate,
    setActiveRasterPath,
    setLayerScope,
    indexResults,
    entityDataStatus,
  } = useVegetationContext();

  const api = useVegetationApi();
  const [ticks, setTicks] = useState<TimelineTick[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  const index = selectedIndex || 'NDVI';

  // Derive custom index options from indexResults (same pattern as VegetationLayerControl)
  const customIndexOptions: CustomIndexOption[] = useMemo(() => {
    return Object.values(indexResults)
      .filter((r: any) => r.is_custom && r.formula_id)
      .map((r: any) => ({
        key: `custom:${r.formula_id}`,
        label: r.formula_name || r.index_type,
      }));
  }, [indexResults]);

  const indexLabel = customIndexOptions.find((o) => o.key === index)?.label ?? index;

  // Dim the index pills that have no data for this parcel. The custom formulas
  // come from indexResults, so they count as available.
  const availableIndices = useMemo(() => {
    const base = entityDataStatus?.available_indices;
    if (!base || base.length === 0) return undefined;
    return [...base, ...customIndexOptions.map(o => o.key)];
  }, [entityDataStatus?.available_indices, customIndexOptions]);

  // Mirrors the selected acquisition so the fetch can read it without depending
  // on it: adding it to the deps would refetch the whole timeline on every click.
  const selectedSensingDateRef = useRef(selectedSensingDate);
  useEffect(() => { selectedSensingDateRef.current = selectedSensingDate; }, [selectedSensingDate]);
  // Same for the visible window: the fetch must not re-run when it changes.
  const rangeRef = useRef(range);
  useEffect(() => { rangeRef.current = range; }, [range.start, range.end]);
  // Cursor -> selection bookkeeping (see the cursor effect below). Declared here
  // because the fetch effect resets the baseline.
  const lastCursorRef = useRef<number | null>(null);
  const baselineKeyRef = useRef<string | null>(null);
  // The one pending debounced selection (from the cursor or from a key press) and, for key presses, the
  // visible index it is heading to, so that presses made while it waits keep stepping from there.
  const pendingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingKeyIdxRef = useRef<number | null>(null);
  const cancelPending = useCallback(() => {
    if (pendingTimerRef.current !== null) {
      clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
    pendingKeyIdxRef.current = null;
  }, []);
  const scheduleSelection = useCallback((run: () => void) => {
    cancelPending();
    pendingTimerRef.current = setTimeout(() => {
      pendingTimerRef.current = null;
      pendingKeyIdxRef.current = null;
      run();
    }, SELECTION_DEBOUNCE_MS);
  }, [cancelPending]);
  // Nothing may fire after the track is gone.
  useEffect(() => cancelPending, [cancelPending]);

  // Load the acquisitions of this parcel and index. Keyed by the entityId prop
  // (the host's selection), not the context's selectedEntityId, which can lag.
  // The backend takes only index_type: the track filters by range on the client.
  useEffect(() => {
    let cancelled = false;

    // Every load starts a new baseline: the first cursor value after it is never
    // a user action. Without this, leaving a parcel (or index) with no ticks and
    // coming back to a key that was already baselined skipped the re-baseline,
    // and a cursor moved in between was taken for a click.
    baselineKeyRef.current = null;
    // A selection waiting for the cursor to settle belongs to the previous parcel or index.
    cancelPending();

    // Ticks of another parcel or index must not linger while this one loads.
    setTicks((prev) => (prev.length > 0 ? [] : prev));
    setError(null);
    setLoading(true);

    (async () => {
      try {
        const response = await api.getScenesAvailable(entityId, index);
        if (cancelled) return;

        const timeline = response?.timeline || [];
        // scene_id is null for Copernicus acquisitions: they are identified by date.
        const mapped: TimelineTick[] = timeline
          .filter((item: any) => item.date)
          .map((item: any) => ({
            scene_id: item.scene_id ?? null,
            sensing_date: item.date,
            mean_value: item.mean_value ?? null,
            cloud_coverage: item.local_cloud_pct != null ? Number(item.local_cloud_pct) : null,
            raster_path: item.raster_path || null,
          }));
        // Ensure ascending chronological order (oldest first)
        mapped.sort((a, b) => a.sensing_date.localeCompare(b.sensing_date));
        setTicks(mapped);

        // The selected date is shared across indices, but each index has its own
        // dates: NDRE runs on the local engine and NDVI/EVI/SAVI/GNDVI on
        // Copernicus, so their timelines rarely overlap. Select the latest
        // acquisition only when the stored one is not one of this parcel and
        // index's acquisitions.
        const currentIso = selectedSensingDateRef.current;
        const storedMatch =
          currentIso != null ? mapped.find((s) => s.sensing_date === currentIso) : undefined;

        // Only acquisitions inside the visible window are candidates: the layer
        // control moves the shared cursor to the selected date, and a date off
        // the axis would take the cursor out of view. With none in the window,
        // select nothing: the layer keeps its own latest and the cursor stays.
        const inWindow = mapped.filter((s) => isInRange(isoToUtcMs(s.sensing_date), rangeRef.current));

        if (storedMatch) {
          // Keep the stored date (moving it would move the shared cursor), but
          // take this index's scene and raster: the same date can exist under
          // both engines (no scene for Copernicus, a UUID for the local one), and
          // a scene left over from the previous index scopes the layer's query to
          // the other engine, which then lacks this index. Same values on a
          // remount for the same index, so nothing changes there.
          setSelectedSceneId(storedMatch.scene_id);
          if (storedMatch.raster_path) {
            setActiveRasterPath(storedMatch.raster_path);
          }
        } else if (inWindow.length > 0) {
          const mostRecent = inWindow[inWindow.length - 1];
          setSelectedDate(new Date(mostRecent.sensing_date));
          setSelectedSceneId(mostRecent.scene_id);
          setSelectedSensingDate(mostRecent.sensing_date);
          if (mostRecent.raster_path) {
            setActiveRasterPath(mostRecent.raster_path);
          }
        }
      } catch (err) {
        if (cancelled) return;
        console.error('[VegetationTimelineTrack] Error fetching availability:', err);
        setError(err instanceof Error ? err.message : 'Failed to load timeline data');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [entityId, index, reloadNonce, api, setSelectedDate, setSelectedSceneId, setSelectedSensingDate, setActiveRasterPath, cancelPending]);

  // Acquisitions inside the visible window, oldest first.
  const visibleTicks = useMemo(
    () => ticks.filter((tk) => isInRange(isoToUtcMs(tk.sensing_date), range)),
    [ticks, range.start, range.end],
  );

  // Show this acquisition on the map. Picking an acquisition means "show this
  // parcel on this date", so the map switches to the selected-parcel scope: in
  // the all-parcels scope it shows each parcel's latest raster and would ignore
  // the pick.
  // `selectedDate` is the shared date the layer control writes to the host cursor.
  // A selection that follows the cursor leaves it alone: the provider keeps it on
  // the cursor, and setting it to the acquisition would make the layer control
  // move the cursor onto the acquisition once the (debounced) selection lands.
  const applyAcquisition = useCallback((tick: TimelineTick, followsCursor = false) => {
    if (!followsCursor) setSelectedDate(new Date(tick.sensing_date));
    setSelectedSceneId(tick.scene_id);
    setSelectedSensingDate(tick.sensing_date);
    setLayerScope('selected');
    if (tick.raster_path) {
      setActiveRasterPath(tick.raster_path);
    }
  }, [setSelectedDate, setSelectedSceneId, setSelectedSensingDate, setLayerScope, setActiveRasterPath]);

  // A click or key press on this track: select the acquisition and move the
  // shared cursor to it. The cursor effect below then finds the cursor on the
  // selected acquisition and does nothing, so the two cannot loop.
  const handleDateSelect = useCallback((tick: TimelineTick) => {
    applyAcquisition(tick);
    if (setCurrentDate) {
      setCurrentDate(new Date(tick.sensing_date));
    }
  }, [applyAcquisition, setCurrentDate]);

  // What a debounced selection reads when it fires: the values of the latest render, not of the one that
  // scheduled it.
  const latestRef = useRef({ visibleTicks, selectedSensingDate, applyAcquisition, handleDateSelect });
  useEffect(() => {
    latestRef.current = { visibleTicks, selectedSensingDate, applyAcquisition, handleDateSelect };
  });

  // Cursor -> selection. Another widget (or the host axis) moved the cursor:
  // show the acquisition nearest to it, without writing the cursor back.
  useEffect(() => {
    if (ticks.length === 0) return;

    // The first cursor value after a load is never a user action: it is
    // whatever the host or the layer control had already put there.
    const key = `${entityId}:${index}`;
    if (baselineKeyRef.current !== key) {
      baselineKeyRef.current = key;
      lastCursorRef.current = cursor;
      return;
    }

    // Only a cursor that really moved counts (ticks changing alone does not).
    if (cursor === lastCursorRef.current) return;
    lastCursorRef.current = cursor;

    // Choosing the acquisition waits until the cursor stops moving: every cursor change restarts the wait.
    scheduleSelection(() => {
      const { visibleTicks: visible, selectedSensingDate: shown, applyAcquisition: apply } = latestRef.current;
      const times = visible.map((tk) => isoToUtcMs(tk.sensing_date));
      const nearest = nearestTime(times, cursor, CURSOR_SNAP_MS);
      if (nearest === null) return;

      const target = visible[times.indexOf(nearest)];
      if (target.sensing_date === shown) return;

      apply(target, true);
    });
  }, [cursor, ticks]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedIdx = visibleTicks.findIndex((tk) => tk.sensing_date === selectedSensingDate);

  const markers: TimelineMarker[] = useMemo(() => {
    const heights = seriesHeights(visibleTicks, index);
    return visibleTicks.map((tk, i) => ({
      id: tickId(tk),
      time: isoToUtcMs(tk.sensing_date),
      color: getTickColor(tk.mean_value, index),
      selected: tk.sensing_date === selectedSensingDate,
      title: `${formatDay(tk.sensing_date, i18n?.language)} · ${indexLabel} ${tk.mean_value != null ? tk.mean_value.toFixed(3) : '–'}`,
      ...(heights[i] !== undefined && { y: heights[i] }),
    }));
  }, [visibleTicks, index, indexLabel, selectedSensingDate, i18n?.language]);

  // A click is a decision, not a drag: it applies at once and supersedes any selection still waiting.
  const handleMarkerSelect = useCallback((id: string) => {
    const tick = visibleTicks.find((tk) => tickId(tk) === id);
    if (!tick) return;
    cancelPending();
    handleDateSelect(tick);
  }, [visibleTicks, handleDateSelect, cancelPending]);

  // Arrow keys jump to the previous/next acquisition. Holding a key repeats quickly, so the selection is
  // debounced like the cursor's: presses made while one waits keep stepping from where it is heading, and
  // only the acquisition the user stops on is selected.
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    if (visibleTicks.length === 0) return;
    e.preventDefault();
    const pending = pendingKeyIdxRef.current;
    const from = pending ?? (selectedIdx < 0 ? visibleTicks.length - 1 : selectedIdx);
    const to = e.key === 'ArrowLeft'
      ? Math.max(0, from - 1)
      : Math.min(visibleTicks.length - 1, from + 1);
    if (to === selectedIdx) {
      // Back on the acquisition already shown: whatever was waiting is moot.
      if (pending !== null) cancelPending();
      return;
    }

    // Every effective press restarts the wait, including one that only repeats the end of the axis.
    const target = visibleTicks[to];
    scheduleSelection(() => latestRef.current.handleDateSelect(target));
    pendingKeyIdxRef.current = to; // after scheduleSelection, which clears it
  }, [visibleTicks, selectedIdx, cancelPending, scheduleSelection]);

  const trackLabel = t('timeline.trackLabel', { index: indexLabel });
  const shownOnMap = selectedSensingDate ? formatDay(selectedSensingDate, i18n?.language) : null;

  const label = (
    <div title={shownOnMap ? `${trackLabel} · ${shownOnMap}` : trackLabel}>
      <div className="truncate">{trackLabel}</div>
      {shownOnMap && <div className="truncate text-nkz-text-muted">{shownOnMap}</div>}
    </div>
  );

  const statusStyle: React.CSSProperties = {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  };

  let status: React.ReactNode = null;
  if (error) {
    status = (
      <div style={statusStyle} className="text-nkz-xs text-nkz-text-muted">
        <span>{t('timelineWidget.loadError')}</span>
        <button
          type="button"
          onClick={() => setReloadNonce((n) => n + 1)}
          className="text-nkz-accent-base"
          style={{ textDecoration: 'underline' }}
        >
          {t('timelineWidget.retry')}
        </button>
      </div>
    );
  } else if (visibleTicks.length === 0) {
    status = (
      <div style={statusStyle} className="text-nkz-xs text-nkz-text-muted">
        {loading ? (
          <span>{t('timeline.loadingHistory')}</span>
        ) : (
          <>
            <CloudOff className="w-4 h-4" />
            <span>{t('timeline.noDataAvailable')}</span>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-nkz-tight">
      <TimelineTrackRow label={label} range={range} cursor={cursor} height={TRACK_HEIGHT}>
        <div
          tabIndex={0}
          role="group"
          aria-label={t('timeline.evolution', { index: indexLabel })}
          onKeyDown={handleKeyDown}
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
          }}
        >
          {status ?? <TimelineMarkers range={range} markers={markers} onSelect={handleMarkerSelect} connect />}
        </div>
      </TimelineTrackRow>

      {/* Controls go under the track at full width: they do not fit in the label column. */}
      <div className="flex flex-wrap items-center justify-between gap-nkz-inline">
        <IndexPillSelector
          selectedIndex={index}
          onIndexChange={(idx: string) => setSelectedIndex(idx)}
          customIndexOptions={customIndexOptions}
          availableIndices={availableIndices}
          compact
        />
        <div className="flex items-center gap-nkz-inline text-nkz-xs text-nkz-text-muted">
          {!index.startsWith('SAR') && (
            <>
              <span className="flex items-center gap-nkz-tight">
                <span className="rounded-full inline-block" style={{ width: 8, height: 8, backgroundColor: '#ef4444' }} />
                {t('legend.low')}
              </span>
              <span className="flex items-center gap-nkz-tight">
                <span className="rounded-full inline-block" style={{ width: 8, height: 8, backgroundColor: '#eab308' }} />
                {t('legend.moderate')}
              </span>
              <span className="flex items-center gap-nkz-tight">
                <span className="rounded-full inline-block" style={{ width: 8, height: 8, backgroundColor: '#22c55e' }} />
                {t('legend.high')}
              </span>
            </>
          )}
          <span>{t('timelineWidget.scenesAvailable', { count: visibleTicks.length })}</span>
        </div>
      </div>
    </div>
  );
};

export default VegetationTimelineTrack;
