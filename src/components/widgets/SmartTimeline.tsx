/**
 * SmartTimeline - Sparse timeline from availability API.
 *
 * Fetches scene availability from GET /entities/{entity_id}/scenes/available
 * and renders colored tick marks for dates with actual data.
 *
 * Supports both self-fetching (via entityId + indexType) and pre-fed (via stats).
 */

import React, { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { Calendar, CloudOff, AlertCircle } from 'lucide-react';
import { useTranslation } from '@nekazari/sdk';
import { useVegetationApi } from '../../services/api';

/** Flexible tick data — accepts both SceneStats (sensing_date) and API timeline (date) */
interface TickData {
  scene_id: string;
  date?: string;
  sensing_date?: string;
  mean_value: number | null;
  cloud_coverage?: number | null;
}

interface SmartTimelineProps {
  /** Entity ID for self-fetching (when stats not pre-fed) */
  entityId?: string;
  /** Pre-fed stats (from TimelineWidget slot) */
  stats?: TickData[];
  /** Currently selected date string (YYYY-MM-DD) */
  selectedDate?: string | null;
  /** Called when user clicks a tick mark */
  onDateSelect?: (date: string, sceneId: string) => void;
  /** Index type for fetch and viewer URL */
  indexType?: string;
  /** External loading indicator */
  isLoading?: boolean;
  // Backward-compat props (kept for TimelineWidget slot; unused in sparse rendering)
  previousYearStats?: any[];
  showComparison?: boolean;
}

/** Get the date string from a tick, handling both date and sensing_date fields */
function tickDate(tick: TickData | null | undefined): string {
  if (!tick) return '';
  const d = tick.date || tick.sensing_date;
  return d || '';
}

function getTickColor(meanValue: number | null): string {
  if (meanValue == null) return '#cbd5e1'; // gray for no data
  if (meanValue >= 0.6) return '#22c55e';  // green
  if (meanValue >= 0.3) return '#eab308';  // yellow
  return '#ef4444';                          // red
}

const formatDateShort = (dateStr: string): string => {
  const d = new Date(dateStr);
  return d.toLocaleDateString('es-ES', { day: 'numeric', month: 'short' });
};

const formatDateFull = (dateStr: string): string => {
  const d = new Date(dateStr);
  return d.toLocaleDateString('es-ES', { day: 'numeric', month: 'long', year: 'numeric' });
};

export const SmartTimeline: React.FC<SmartTimelineProps> = ({
  entityId,
  stats: externalStats,
  selectedDate,
  onDateSelect,
  indexType = 'NDVI',
  isLoading: externalLoading = false,
}) => {
  const { t } = useTranslation();
  const api = useVegetationApi();

  // Internal fetch state (when entityId is provided and stats are not)
  const [internalStats, setInternalStats] = useState<TickData[]>([]);
  const [internalLoading, setInternalLoading] = useState(false);
  const [internalError, setInternalError] = useState<string | null>(null);

  // Tooltip state
  const [tooltipItem, setTooltipItem] = useState<TickData | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null);

  // Viewer URL loading
  const [loadingSceneId, setLoadingSceneId] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);

  // Decide which stats to use
  const stats = useMemo(() => {
    if (externalStats && externalStats.length > 0) return externalStats;
    return internalStats;
  }, [externalStats, internalStats]);

  const isLoading = externalLoading || internalLoading;
  const hasError = internalError && !externalLoading;

  // Sort stats chronologically
  const sortedStats = useMemo(() => {
    return [...stats].sort((a, b) => tickDate(a).localeCompare(tickDate(b)));
  }, [stats]);

  // Fetch internally when entityId + indexType provided and no external stats
  useEffect(() => {
    if (externalStats && externalStats.length > 0) {
      // External data provided — don't fetch
      setInternalLoading(false);
      setInternalError(null);
      return;
    }

    if (!entityId) {
      setInternalStats([]);
      return;
    }

    let cancelled = false;
    setInternalLoading(true);
    setInternalError(null);

    api.getScenesAvailable(entityId, indexType)
      .then(response => {
        if (cancelled) return;
        const timeline = response?.timeline || [];
        const mapped: TickData[] = timeline.map((item: any) => ({
          scene_id: item.scene_id || item.id,
          date: item.date,
          mean_value: item.mean_value != null ? Number(item.mean_value) : null,
          cloud_coverage: item.local_cloud_pct != null ? Number(item.local_cloud_pct) : null,
        }));
        mapped.sort((a, b) => tickDate(a).localeCompare(tickDate(b)));
        setInternalStats(mapped);
      })
      .catch(err => {
        if (cancelled) return;
        console.error('[SmartTimeline] fetch error:', err);
        setInternalError(err instanceof Error ? err.message : t('timeline.errorLoadingData'));
        setInternalStats([]);
      })
      .finally(() => {
        if (!cancelled) setInternalLoading(false);
      });

    return () => { cancelled = true; };
  }, [entityId, indexType, api, externalStats, t]);

  // Click handler: calls onDateSelect and loads viewer URL
  const handleTickClick = useCallback(async (tick: TickData) => {
    if (!onDateSelect) return;
    onDateSelect(tickDate(tick), tick.scene_id);

    // Load viewer URL for the map layer
    setLoadingSceneId(tick.scene_id);
    try {
      await api.getViewerUrl(tick.scene_id, indexType);
      // The response tileUrlTemplate would be used by the map layer.
      // The context handles updating the active raster path.
    } catch (err) {
      console.error('[SmartTimeline] viewer URL error:', err);
    } finally {
      setLoadingSceneId(null);
    }
  }, [api, indexType, onDateSelect]);

  // Tooltip handlers
  const handleMouseEnter = useCallback((tick: TickData, e: React.MouseEvent) => {
    setTooltipItem(tick);
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setTooltipPos({ x: rect.left + rect.width / 2, y: rect.top - 8 });
  }, []);

  const handleMouseLeave = useCallback(() => {
    setTooltipItem(null);
    setTooltipPos(null);
  }, []);

  // Loading state
  if (isLoading && sortedStats.length === 0) {
    return (
      <div className="bg-white/95 backdrop-blur-sm rounded-xl border border-slate-200/50 p-6">
        <div className="flex items-center justify-center h-20">
          <div className="animate-pulse flex items-center gap-2 text-slate-500">
            <Calendar className="w-5 h-5" />
            <span>{t('timeline.loadingHistory')}</span>
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (hasError && sortedStats.length === 0) {
    return (
      <div className="bg-white/95 backdrop-blur-sm rounded-xl border border-slate-200/50 p-6">
        <div className="flex items-center justify-center h-20 text-red-500">
          <AlertCircle className="w-5 h-5 mr-2" />
          <span className="text-sm">{internalError}</span>
        </div>
      </div>
    );
  }

  // Empty state
  if (sortedStats.length === 0) {
    return (
      <div className="bg-white/95 backdrop-blur-sm rounded-xl border border-slate-200/50 p-6">
        <div className="flex items-center justify-center h-20 text-slate-500">
          <CloudOff className="w-5 h-5 mr-2" />
          <span>{t('timeline.noDataAvailable')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white/95 backdrop-blur-sm rounded-xl border border-slate-200/50 shadow-lg overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-slate-100">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4 text-slate-500" />
            <h3 className="text-sm font-semibold text-slate-700">
              {t('timeline.evolution', { index: indexType })}
            </h3>
          </div>
          <span className="text-xs text-slate-400">
            {t('timelineWidget.scenesAvailable', { count: sortedStats.length })}
          </span>
        </div>
      </div>

      {/* Sparse timeline */}
      <div
        ref={containerRef}
        className="relative px-4 py-6"
      >
        {/* Baseline */}
        <div className="absolute left-4 right-4 top-1/2 h-0.5 bg-slate-200 -translate-y-1/2" />

        {/* Tick marks */}
        <div className="flex items-center justify-between relative">
          {sortedStats.map((tick) => {
            const isSelected = tickDate(tick) === selectedDate;
            const color = getTickColor(tick.mean_value);
            const isLoadingTick = loadingSceneId === tick.scene_id;

            return (
              <div
                key={tick.scene_id}
                className="relative flex flex-col items-center"
                style={{ flex: '0 0 auto' }}
              >
                <button
                  type="button"
                  onClick={() => handleTickClick(tick)}
                  onMouseEnter={(e) => handleMouseEnter(tick, e)}
                  onMouseLeave={handleMouseLeave}
                  className={`
                    relative z-10 w-4 h-4 rounded-full transition-all cursor-pointer
                    hover:scale-150 focus:outline-none focus:ring-2 focus:ring-emerald-400
                    ${isSelected ? 'ring-2 ring-white scale-150 shadow-md' : ''}
                    ${isLoadingTick ? 'animate-pulse' : ''}
                  `}
                  style={{
                    backgroundColor: color,
                    boxShadow: isSelected ? `0 0 0 3px ${color}` : 'none',
                  }}
                  title={`${formatDateShort(tickDate(tick))}: ${tick.mean_value?.toFixed(3) ?? '-'}`}
                />

                {/* Date label below tick */}
                <span
                  className={`
                    mt-2 text-[10px] whitespace-nowrap transition-colors
                    ${isSelected ? 'text-slate-800 font-semibold' : 'text-slate-400'}
                  `}
                >
                  {formatDateShort(tickDate(tick))}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Tooltip */}
      {tooltipItem && tooltipPos && (
        <div
          className="absolute z-50 bg-white shadow-lg rounded-lg border border-slate-200 p-3 text-sm pointer-events-none"
          style={{
            left: Math.min(tooltipPos.x, (containerRef.current?.offsetWidth ?? 400) - 180),
            top: tooltipPos.y - 10,
            transform: 'translate(-50%, -100%)',
            minWidth: 160,
          }}
        >
          <p className="font-semibold text-slate-800 mb-1">
            {formatDateFull(tickDate(tooltipItem)!)}
          </p>
          <div className="space-y-0.5 text-xs text-slate-600">
            <p>
              <span className="font-medium">{indexType}:</span>{' '}
              {tooltipItem.mean_value != null ? tooltipItem.mean_value.toFixed(4) : '-'}
            </p>
            <p>
              <span className="font-medium">{t('timeline.clouds')}:</span>{' '}
              {tooltipItem.cloud_coverage != null ? `${tooltipItem.cloud_coverage.toFixed(1)}%` : '-'}
            </p>
            <p className="text-[10px] text-slate-400 truncate max-w-[200px]">
              ID: {tooltipItem.scene_id}
            </p>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="px-4 py-2 border-t border-slate-100 bg-slate-50/50 flex items-center gap-4 text-[10px] text-slate-500">
        <div className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full bg-green-500" />
          <span>{t('legend.high')} (&ge;0.6)</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full bg-yellow-500" />
          <span>{t('legend.moderate')} (0.3-0.6)</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
          <span>{t('legend.low')} (&lt;0.3)</span>
        </div>
      </div>
    </div>
  );
};

export default SmartTimeline;
