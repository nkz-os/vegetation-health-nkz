/**
 * Timeline Widget - Slot component for bottom panel.
 * Enhanced with Smart Timeline showing index trends over time.
 */

import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { Calendar } from 'lucide-react';
import { SlotShell } from '@nekazari/viewer-kit';
import { Stack, Badge } from '@nekazari/ui-kit';
import { useViewer, useTranslation } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { SmartTimeline, TickData } from '../widgets/SmartTimeline';
import { IndexPillSelector, CustomIndexOption } from '../widgets/IndexPillSelector';

interface TimelineWidgetProps {
  entityId?: string;
}

type TimelineTick = TickData & { raster_path: string | null };

const vegetationAccent = { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' };

export const TimelineWidget: React.FC<TimelineWidgetProps> = ({ entityId }) => {
  const { t } = useTranslation();
  const { currentDate, setCurrentDate } = useViewer();
  const {
    selectedIndex,
    selectedEntityId,
    selectedSeasonId,
    selectedSensingDate,
    setSelectedIndex,
    setSelectedDate,
    setSelectedSceneId,
    setSelectedSensingDate,
    setActiveRasterPath,
    setLayerScope,
    dateRange,
    indexResults,
    entityDataStatus,
  } = useVegetationContext();

  const api = useVegetationApi();
  const [stats, setStats] = useState<TimelineTick[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveEntityId = entityId || selectedEntityId;

  // Derive custom index options from indexResults (same pattern as VegetationLayerControl)
  const customIndexOptions: CustomIndexOption[] = useMemo(() => {
    return Object.values(indexResults)
      .filter((r: any) => r.is_custom && r.formula_id)
      .map((r: any) => ({
        key: `custom:${r.formula_id}`,
        label: r.formula_name || r.index_type,
      }));
  }, [indexResults]);

  // Dim the index pills that have no data for this parcel. The custom formulas
  // come from indexResults, so they count as available.
  const availableIndices = useMemo(() => {
    const base = entityDataStatus?.available_indices;
    if (!base || base.length === 0) return undefined;
    return [...base, ...customIndexOptions.map(o => o.key)];
  }, [entityDataStatus?.available_indices, customIndexOptions]);

  // Load timeline from availability API (§12.8.1) — sparse ticks, mean_value for heatmap, local_cloud_pct for tooltips
  // Ref to track if we already auto-selected a date for this entity+index
  const autoSelectedRef = React.useRef<string | null>(null);
  // Mirrors the selected acquisition so loadStats can read it without depending
  // on it: adding it to the deps would refetch the whole timeline on every click.
  const selectedSensingDateRef = React.useRef(selectedSensingDate);
  React.useEffect(() => { selectedSensingDateRef.current = selectedSensingDate; }, [selectedSensingDate]);

  const loadStats = useCallback(async () => {
    if (!effectiveEntityId) return;

    setLoading(true);
    setError(null);

    try {
      // Prefer the selected crop season window when set; otherwise fall
      // back to the parcel's full data range, then to the context dateRange.
      // This keeps the timeline focused on what the user actually picked.
      const activeSeason = selectedSeasonId
        ? entityDataStatus?.active_crop_seasons?.find((s) => s.id === selectedSeasonId)
        : null;
      const dataDateRange = entityDataStatus?.date_range;
      const startStr = activeSeason?.start_date
        || dataDateRange?.first
        || dateRange?.startDate?.toISOString().split('T')[0];
      const endStr = activeSeason?.end_date
        || dataDateRange?.last
        || dateRange?.endDate?.toISOString().split('T')[0];
      const response = await api.getScenesAvailable(
        effectiveEntityId,
        selectedIndex || 'NDVI',
        startStr,
        endStr
      );
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
      setStats(mapped);

      // The selected date is shared across indices, but each index has its own
      // dates: NDRE runs on the local engine and NDVI/EVI/SAVI/GNDVI on
      // Copernicus, so their timelines rarely overlap. Auto-selecting only once
      // per index left the shared date on whatever another index had picked, and
      // this index then rendered "no data for the selected range" on a date it
      // never had. Re-select whenever the current date is not one of ours.
      const autoKey = `${effectiveEntityId}:${selectedIndex}`;
      const currentIso = selectedSensingDateRef.current;
      const currentIsAvailable =
        currentIso != null && mapped.some((s) => s.sensing_date === currentIso);

      if (mapped.length > 0 && (autoSelectedRef.current !== autoKey || !currentIsAvailable)) {
        autoSelectedRef.current = autoKey;
        const mostRecent = mapped[mapped.length - 1];
        setSelectedDate(new Date(mostRecent.sensing_date));
        setSelectedSceneId(mostRecent.scene_id);
        setSelectedSensingDate(mostRecent.sensing_date);
        if (mostRecent.raster_path) {
          setActiveRasterPath(mostRecent.raster_path);
        }
      }
    } catch (err) {
      console.error('[TimelineWidget] Error fetching availability:', err);
      setError(err instanceof Error ? err.message : 'Failed to load timeline data');
    } finally {
      setLoading(false);
    }
  }, [effectiveEntityId, selectedIndex, selectedSeasonId, api, dateRange?.startDate, dateRange?.endDate, setSelectedDate, setSelectedSceneId, setSelectedSensingDate, entityDataStatus?.active_crop_seasons, entityDataStatus?.date_range]);

  // Initial load
  useEffect(() => {
    loadStats();
  }, [loadStats]);

  // Picking an acquisition on the timeline means "show this parcel on this
  // date", so the map switches to the selected-parcel scope: in the all-parcels
  // scope it shows each parcel's latest raster and would ignore the click.
  const handleDateSelect = useCallback((dateStr: string, sceneId: string | null) => {
    setSelectedDate(new Date(dateStr));
    setSelectedSceneId(sceneId);
    setSelectedSensingDate(dateStr);
    setLayerScope('selected');

    const tick = stats.find(s => s.sensing_date === dateStr);
    if (tick?.raster_path) {
      setActiveRasterPath(tick.raster_path);
    }

    if (setCurrentDate) {
      setCurrentDate(new Date(dateStr));
    }
  }, [setSelectedDate, setSelectedSceneId, setSelectedSensingDate, setLayerScope, setActiveRasterPath, setCurrentDate, stats]);

  // Sync with viewer's currentDate changes — use ref to avoid re-render loop
  const lastViewerDateRef = React.useRef<number>(0);
  useEffect(() => {
    if (!currentDate || stats.length === 0) return;
    const ts = currentDate.getTime();
    if (ts === lastViewerDateRef.current) return;
    lastViewerDateRef.current = ts;

    const currentDateStr = currentDate.toISOString().split('T')[0];
    const closestScene = stats.find(s => s.sensing_date === currentDateStr);

    if (closestScene && closestScene.sensing_date !== selectedSensingDate) {
      setSelectedDate(new Date(closestScene.sensing_date));
      setSelectedSceneId(closestScene.scene_id);
      setSelectedSensingDate(closestScene.sensing_date);
    }
  }, [currentDate, stats, selectedSensingDate, setSelectedDate, setSelectedSceneId, setSelectedSensingDate]);

  if (!effectiveEntityId) {
    return (
      <SlotShell moduleId="vegetation-prime" accent={vegetationAccent}>
        <div className="flex items-center justify-center gap-nkz-inline py-nkz-section text-nkz-text-muted">
          <Calendar className="w-5 h-5" />
          <p className="text-nkz-sm">{t('timelineWidget.selectParcel')}</p>
        </div>
      </SlotShell>
    );
  }

  if (error) {
    return (
      <SlotShell moduleId="vegetation-prime" accent={vegetationAccent}>
        <Stack gap="inline">
          <Badge intent="negative">{error}</Badge>
          <button
            onClick={loadStats}
            className="text-nkz-sm text-nkz-accent-base hover:underline"
          >
            {t('timelineWidget.retry')}
          </button>
        </Stack>
      </SlotShell>
    );
  }

  return (
    <SlotShell moduleId="vegetation-prime" accent={vegetationAccent}>
      <div className="flex flex-col gap-nkz-tight">
        <div className="flex flex-wrap items-center justify-between gap-nkz-inline">
          <IndexPillSelector
            selectedIndex={selectedIndex || 'NDVI'}
            onIndexChange={(idx: string) => setSelectedIndex(idx)}
            customIndexOptions={customIndexOptions}
            availableIndices={availableIndices}
            compact
          />
          <div className="flex items-center gap-nkz-inline text-nkz-xs text-nkz-text-muted">
            {!(selectedIndex || 'NDVI').startsWith('SAR') && (
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
            <span>{t('timelineWidget.scenesAvailable', { count: stats.length })}</span>
          </div>
        </div>

        <SmartTimeline
          stats={stats}
          selectedDate={selectedSensingDate}
          onDateSelect={handleDateSelect}
          indexType={selectedIndex || 'NDVI'}
          isLoading={loading}
        />
      </div>
    </SlotShell>
  );
};

export default TimelineWidget;
