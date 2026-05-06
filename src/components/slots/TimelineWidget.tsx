/**
 * Timeline Widget - Slot component for bottom panel.
 * Enhanced with Smart Timeline showing index trends over time.
 */

import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { Calendar, BarChart3 } from 'lucide-react';
import { SlotShell } from '@nekazari/viewer-kit';
import { Stack, Badge } from '@nekazari/ui-kit';
import { useViewer, useTranslation } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { SceneStats } from '../../types';
import { SmartTimeline } from '../widgets/SmartTimeline';
import { IndexPillSelector, CustomIndexOption } from '../widgets/IndexPillSelector';

interface TimelineWidgetProps {
  entityId?: string;
}

const vegetationAccent = { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' };

export const TimelineWidget: React.FC<TimelineWidgetProps> = ({ entityId }) => {
  const { t } = useTranslation();
  const { currentDate, setCurrentDate } = useViewer();
  const {
    selectedIndex,
    selectedDate,
    selectedEntityId,
    selectedSeasonId,
    setSelectedIndex,
    setSelectedDate,
    setSelectedSceneId,
    setActiveRasterPath,
    dateRange,
    indexResults,
    entityDataStatus,
  } = useVegetationContext();

  const api = useVegetationApi();
  const [stats, setStats] = useState<SceneStats[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showChart, setShowChart] = useState(true);

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

  // Load timeline from availability API (§12.8.1) — sparse ticks, mean_value for heatmap, local_cloud_pct for tooltips
  // Ref to track if we already auto-selected a date for this entity+index
  const autoSelectedRef = React.useRef<string | null>(null);

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
      const mapped: SceneStats[] = timeline.map((item: any) => ({
        scene_id: item.scene_id || item.id,
        sensing_date: item.date,
        mean_value: item.mean_value ?? null,
        min_value: null,
        max_value: null,
        std_dev: null,
        cloud_coverage: item.local_cloud_pct != null ? Number(item.local_cloud_pct) : null,
        raster_path: item.raster_path || null,
      }));
      // Ensure ascending chronological order (oldest first)
      mapped.sort((a, b) => a.sensing_date.localeCompare(b.sensing_date));
      setStats(mapped);

      // Auto-select most recent date only once per entity+index
      const autoKey = `${effectiveEntityId}:${selectedIndex}`;
      if (autoSelectedRef.current !== autoKey && mapped.length > 0) {
        autoSelectedRef.current = autoKey;
        const mostRecent = mapped[mapped.length - 1];
        setSelectedDate(new Date(mostRecent.sensing_date));
        setSelectedSceneId(mostRecent.scene_id);
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
  }, [effectiveEntityId, selectedIndex, selectedSeasonId, api, dateRange?.startDate, dateRange?.endDate, setSelectedDate, setSelectedSceneId, entityDataStatus?.active_crop_seasons, entityDataStatus?.date_range]);

  // Initial load
  useEffect(() => {
    loadStats();
  }, [loadStats]);

  // Handle date selection from chart
  const handleDateSelect = useCallback((dateStr: string, sceneId: string) => {
    setSelectedDate(new Date(dateStr));
    setSelectedSceneId(sceneId);

    // Find raster_path for this scene and update it
    const scene = stats.find(s => s.scene_id === sceneId);
    if (scene?.raster_path) {
      setActiveRasterPath(scene.raster_path);
    }

    // Update viewer's currentDate
    if (setCurrentDate) {
      setCurrentDate(new Date(dateStr));
    }
  }, [setSelectedDate, setSelectedSceneId, setActiveRasterPath, setCurrentDate, stats]);

  // Sync with viewer's currentDate changes — use ref to avoid re-render loop
  const lastViewerDateRef = React.useRef<number>(0);
  useEffect(() => {
    if (!currentDate || stats.length === 0) return;
    const ts = currentDate.getTime();
    if (ts === lastViewerDateRef.current) return;
    lastViewerDateRef.current = ts;

    const currentDateStr = currentDate.toISOString().split('T')[0];
    const closestScene = stats.find(s => s.sensing_date === currentDateStr);
    const selectedDateStr = selectedDate ? selectedDate.toISOString().split('T')[0] : null;

    if (closestScene && closestScene.sensing_date !== selectedDateStr) {
      setSelectedDate(new Date(closestScene.sensing_date));
      setSelectedSceneId(closestScene.scene_id);
    }
  }, [currentDate, stats, selectedDate, setSelectedDate, setSelectedSceneId]);

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
      <div className="space-y-nkz-inline">
        {/* Compact index pill selector above the timeline */}
        <IndexPillSelector
          selectedIndex={selectedIndex || 'NDVI'}
          onIndexChange={(idx: string) => setSelectedIndex(idx)}
          customIndexOptions={customIndexOptions}
          compact
        />

        <div className="flex items-center justify-between px-nkz-inline">
          <div className="flex items-center gap-nkz-inline">
            <button
              onClick={() => setShowChart(!showChart)}
              className={`flex items-center gap-nkz-tight px-nkz-inline py-nkz-tight rounded-nkz-md text-nkz-xs transition-colors ${
                showChart
                  ? 'bg-nkz-accent-base text-nkz-text-on-accent'
                  : 'bg-nkz-surface-sunken text-nkz-text-secondary hover:bg-nkz-surface'
              }`}
            >
              <BarChart3 className="w-3.5 h-3.5" />
              {t('timelineWidget.chart')}
            </button>
          </div>

          <div className="text-nkz-xs text-nkz-text-muted">
            {t('timelineWidget.scenesAvailable', { count: stats.length })}
          </div>
        </div>

        {showChart && (
          <>
            <SmartTimeline
              stats={stats}
              selectedDate={selectedDate ? selectedDate.toISOString().split('T')[0] : null}
              onDateSelect={handleDateSelect}
              indexType={selectedIndex || 'NDVI'}
              isLoading={loading}
            />

            {/* Subtle compact legend bar below the timeline */}
            <div className="flex items-center justify-center gap-nkz-inline mt-nkz-inline text-[10px] text-nkz-text-muted">
              <span className="flex items-center gap-nkz-tight">
                <span className="w-3 h-1.5 rounded-full bg-red-400 inline-block" />
                {t('legend.low')}
              </span>
              <span className="flex items-center gap-nkz-tight">
                <span className="w-3 h-1.5 rounded-full bg-amber-400 inline-block" />
                {t('legend.moderate')}
              </span>
              <span className="flex items-center gap-nkz-tight">
                <span className="w-3 h-1.5 rounded-full bg-green-500 inline-block" />
                {t('legend.high')}
              </span>
            </div>
          </>
        )}
      </div>
    </SlotShell>
  );
};

export default TimelineWidget;
