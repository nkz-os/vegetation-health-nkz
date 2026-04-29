/**
 * Timeline Widget - Slot component for bottom panel.
 * Enhanced with Smart Timeline showing index trends over time.
 */

import React, { useEffect, useState, useCallback } from 'react';
import { Calendar, BarChart3, Eye, EyeOff } from 'lucide-react';
import { useViewer, useTranslation } from '@nekazari/sdk';
import { useUIKit } from '../../hooks/useUIKit';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { SceneStats } from '../../types';
import { SmartTimeline } from '../widgets/SmartTimeline';

interface TimelineWidgetProps {
  entityId?: string;
}

export const TimelineWidget: React.FC<TimelineWidgetProps> = ({ entityId }) => {
  // Get UI components safely from Host
  const { t } = useTranslation();
  const { Card } = useUIKit();
  const { currentDate, setCurrentDate } = useViewer();
  const {
    selectedIndex,
    selectedDate,
    selectedEntityId,
    setSelectedDate,
    setSelectedSceneId,
    setActiveRasterPath,
    dateRange,
  } = useVegetationContext();

  const api = useVegetationApi();
  const [stats, setStats] = useState<SceneStats[]>([]);
  const [previousYearStats, setPreviousYearStats] = useState<SceneStats[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showComparison, setShowComparison] = useState(false);
  const [showChart, setShowChart] = useState(true);

  const effectiveEntityId = entityId || selectedEntityId;

  // Load timeline from availability API (§12.8.1) — sparse ticks, mean_value for heatmap, local_cloud_pct for tooltips
  // Ref to track if we already auto-selected a date for this entity+index
  const autoSelectedRef = React.useRef<string | null>(null);

  const loadStats = useCallback(async () => {
    if (!effectiveEntityId) return;

    setLoading(true);
    setError(null);

    try {
      const startStr = dateRange?.startDate?.toISOString().split('T')[0];
      const endStr = dateRange?.endDate?.toISOString().split('T')[0];
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
  }, [effectiveEntityId, selectedIndex, api, dateRange?.startDate, dateRange?.endDate, setSelectedDate, setSelectedSceneId]);

  // Load comparison data when enabled
  const loadComparison = useCallback(async () => {
    if (!effectiveEntityId || !showComparison) return;

    try {
      const response = await api.compareYears(effectiveEntityId, selectedIndex || 'NDVI');
      const prevYearSceneStats: SceneStats[] = response.previous_year.stats.map(s => ({
        scene_id: '',
        sensing_date: s.sensing_date,
        mean_value: s.mean_value,
        min_value: null,
        max_value: null,
        std_dev: null,
        cloud_coverage: null,
      }));
      setPreviousYearStats(prevYearSceneStats);
    } catch (err) {
      console.error('[TimelineWidget] Error fetching comparison:', err);
    }
  }, [effectiveEntityId, showComparison, selectedIndex, api]);

  // Initial load
  useEffect(() => {
    loadStats();
  }, [loadStats]);

  // Load comparison when toggled
  useEffect(() => {
    if (showComparison) {
      loadComparison();
    }
  }, [showComparison, loadComparison]);

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
      <Card padding="md" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl">
        <div className="flex items-center justify-center gap-2 py-6 text-slate-500">
          <Calendar className="w-5 h-5" />
          <p>{t('timelineWidget.selectParcel')}</p>
        </div>
      </Card>
    );
  }

  if (error) {
    return (
      <Card padding="md" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl">
        <div className="text-center text-red-600 py-4">
          <p>{error}</p>
          <button 
            onClick={loadStats}
            className="mt-2 text-sm text-blue-600 hover:underline"
          >
            {t('timelineWidget.retry')}
          </button>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowChart(!showChart)}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs transition-colors ${
              showChart 
                ? 'bg-slate-800 text-white' 
                : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
            }`}
          >
            <BarChart3 className="w-3.5 h-3.5" />
            {t('timelineWidget.chart')}
          </button>
          
          <button
            onClick={() => setShowComparison(!showComparison)}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs transition-colors ${
              showComparison 
                ? 'bg-amber-500 text-white' 
                : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
            }`}
          >
            {showComparison ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
            {t('timelineWidget.previousYear')}
          </button>
        </div>
        
        <div className="text-xs text-slate-500">
          {t('timelineWidget.scenesAvailable', { count: stats.length })}
        </div>
      </div>

      {showChart && (
        <SmartTimeline
          stats={stats}
          selectedDate={selectedDate ? selectedDate.toISOString().split('T')[0] : null} 
          onDateSelect={handleDateSelect}
          indexType={selectedIndex || 'NDVI'} 
          previousYearStats={showComparison ? previousYearStats : undefined}
          showComparison={showComparison}
          isLoading={loading}
        />
      )}
    </div>
  );
};

export default TimelineWidget;
