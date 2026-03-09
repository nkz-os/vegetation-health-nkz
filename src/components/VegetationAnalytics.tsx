/**
 * VegetationAnalytics - Main analysis view for a selected parcel.
 * Shows available scenes, lets user calculate indices, and displays stats.
 * NO subscription gating — works immediately.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { Card } from '@nekazari/ui-kit';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationApi } from '../services/api';
import { useVegetationScenes } from '../hooks/useVegetationScenes';
import { useIndexCalculation } from '../hooks/useIndexCalculation';
import TimeseriesChart from './widgets/TimeseriesChart';
import { DateSelector } from './widgets/DateSelector';
import { IndexPillSelector } from './widgets/IndexPillSelector';
import { useAuth } from '../hooks/useAuth';
import { useTranslation } from 'react-i18next';
import type { VegetationJob } from '../types';
import {
  Calendar, Loader2, Calculator, AlertCircle, CheckCircle,
  RefreshCw, BarChart3, Clock, Play, XCircle, Trash2,
} from 'lucide-react';

export const VegetationAnalytics: React.FC = () => {
  const { t } = useTranslation();
  const {
    selectedIndex, setSelectedIndex,
    selectedEntityId, setSelectedEntityId,
    selectedSceneId, setSelectedSceneId,
  } = useVegetationContext();
  const { isAuthenticated } = useAuth();
  const api = useVegetationApi();

  // Scenes
  const { scenes, loading: loadingScenes, error: scenesError, refresh: refreshScenes } = useVegetationScenes({
    entityId: selectedEntityId || undefined,
    limit: 100,
    autoRefresh: false,
  });

  // Stats
  const [stats, setStats] = useState<any>(null);
  const [loadingStats, setLoadingStats] = useState(false);

  // Jobs
  const [jobs, setJobs] = useState<VegetationJob[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);

  // Calculation
  const { calculateIndex, isCalculating, error: calcError, success: calcSuccess, resetState } = useIndexCalculation();

  const effectiveIndex = selectedIndex || 'NDVI';

  // Load stats when entity changes
  useEffect(() => {
    if (!selectedEntityId || !isAuthenticated) return;
    setLoadingStats(true);
    api.getSceneStats(selectedEntityId, effectiveIndex, 12)
      .then((data) => {
        if (data?.summary) {
          setStats(data.summary);
        } else if (data?.data_points?.length > 0) {
          // Compute from data_points
          const means = data.data_points.filter((d: any) => d.mean !== null).map((d: any) => d.mean);
          if (means.length > 0) {
            setStats({
              avg: means.reduce((a: number, b: number) => a + b, 0) / means.length,
              min: Math.min(...means),
              max: Math.max(...means),
              count: means.length,
            });
          }
        }
      })
      .catch(() => setStats(null))
      .finally(() => setLoadingStats(false));
  }, [selectedEntityId, effectiveIndex, isAuthenticated]);

  // Load jobs for this entity
  const loadJobs = useCallback(async () => {
    if (!isAuthenticated) return;
    setLoadingJobs(true);
    try {
      const response = await api.listJobs(undefined, 50, 0);
      const entityJobs = selectedEntityId
        ? response.jobs.filter(j => j.entity_id === selectedEntityId)
        : response.jobs;
      setJobs(entityJobs);
    } catch {
      setJobs([]);
    } finally {
      setLoadingJobs(false);
    }
  }, [api, selectedEntityId, isAuthenticated]);

  useEffect(() => { loadJobs(); }, [loadJobs]);

  // Auto-refresh if active jobs
  useEffect(() => {
    const hasActive = jobs.some(j => j.status === 'running' || j.status === 'pending');
    if (!hasActive) return;
    const interval = setInterval(loadJobs, 8000);
    return () => clearInterval(interval);
  }, [jobs, loadJobs]);

  const handleDeleteJob = async (jobId: string) => {
    try {
      await api.deleteJob(jobId);
      setJobs(prev => prev.filter(j => j.id !== jobId));
    } catch { /* ignore */ }
  };

  const handleCalculate = async () => {
    resetState();
    const jobId = await calculateIndex({
      sceneId: selectedSceneId || undefined,
      entityId: selectedEntityId || undefined,
      indexType: effectiveIndex as any,
    });
    if (jobId) {
      loadJobs();
      refreshScenes();
    }
  };

  // --- Auth guard ---
  if (!isAuthenticated) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">{t('analyticsPage.loginRequired')}</p>
      </div>
    );
  }

  // --- No parcel selected ---
  if (!selectedEntityId) {
    return (
      <div className="p-8 max-w-3xl mx-auto">
        <div className="flex items-center justify-center h-32 bg-slate-50 rounded-xl border border-dashed border-slate-300">
          <div className="text-center">
            <p className="text-slate-500 font-medium">{t('analyticsPage.noParcelSelected')}</p>
            <p className="text-xs text-slate-400 mt-1">{t('analyticsPage.noParcelHint')}</p>
          </div>
        </div>
      </div>
    );
  }

  // --- Main analytics view ---
  const parcelShortName = selectedEntityId.split(':').pop() || selectedEntityId;

  const statusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle className="w-3.5 h-3.5 text-emerald-500" />;
      case 'failed': return <XCircle className="w-3.5 h-3.5 text-red-500" />;
      case 'running': return <Play className="w-3.5 h-3.5 text-blue-500 animate-pulse" />;
      case 'pending': return <Clock className="w-3.5 h-3.5 text-amber-500" />;
      default: return <AlertCircle className="w-3.5 h-3.5 text-slate-400" />;
    }
  };

  return (
    <div className="space-y-6 max-w-4xl mx-auto py-6 px-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-800">{parcelShortName}</h2>
          <p className="text-sm text-slate-500">{t('analyticsPage.analyticsDashboard')}</p>
        </div>
        <button
          onClick={() => setSelectedEntityId(null)}
          className="text-sm text-slate-500 hover:text-slate-700 underline"
        >
          {t('analyticsPage.changeParcel')}
        </button>
      </div>

      {/* Index Selector + Calculate */}
      <Card padding="md">
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <label className="text-sm font-semibold text-slate-700">
              {t('analytics.indexSelector')}
            </label>
            <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded">
              {effectiveIndex}
            </span>
          </div>
          <IndexPillSelector
            selectedIndex={effectiveIndex}
            onIndexChange={(idx) => setSelectedIndex(idx as any)}
            showCustom={false}
            className="grid grid-cols-3 sm:grid-cols-5 gap-2"
          />
        </div>
      </Card>

      {/* Sentinel-2 Scenes */}
      <Card padding="md">
        <div className="mb-3">
          <div className="flex items-center gap-2 mb-1">
            <Calendar className="w-4 h-4 text-slate-600" />
            <h3 className="text-sm font-semibold text-slate-800">
              {t('analytics.noScenes', 'Imágenes Sentinel-2')}
            </h3>
            <button onClick={refreshScenes} className="ml-auto p-1 text-slate-400 hover:text-slate-600">
              <RefreshCw className={`w-3.5 h-3.5 ${loadingScenes ? 'animate-spin' : ''}`} />
            </button>
          </div>
          <p className="text-xs text-slate-500">
            {t('configPanel.timelineFilterLabel', 'Selecciona una fecha para calcular el índice')}
          </p>
        </div>

        {loadingScenes ? (
          <div className="h-20 flex items-center justify-center gap-2 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin" />
            <span className="text-sm">{t('common.loading')}</span>
          </div>
        ) : scenesError ? (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
            {t('errors.loadFailed')}: {scenesError}
            <button onClick={refreshScenes} className="ml-2 underline text-xs">{t('common.retry')}</button>
          </div>
        ) : scenes.length === 0 ? (
          <div className="bg-slate-50 rounded-lg p-4 text-center text-sm text-slate-500">
            {t('analytics.noScenes')}
          </div>
        ) : (
          <DateSelector
            scenes={scenes}
            selectedSceneId={selectedSceneId}
            onSelect={(sceneId) => setSelectedSceneId(sceneId)}
          />
        )}

        {/* Calculate button */}
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={handleCalculate}
            disabled={isCalculating || !selectedSceneId}
            className="flex items-center gap-2 px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isCalculating ? (
              <><Loader2 className="w-4 h-4 animate-spin" />{t('common.loading')}</>
            ) : (
              <><Calculator className="w-4 h-4" />{t('calculations.calculateIndex')}</>
            )}
          </button>
          {!selectedSceneId && scenes.length > 0 && (
            <span className="text-xs text-slate-400">{t('calculations.selectScene')}</span>
          )}
        </div>

        {calcError && (
          <div className="mt-2 flex items-center gap-2 text-xs text-red-600 bg-red-50 p-2 rounded">
            <AlertCircle className="w-4 h-4" />
            <span>{calcError}</span>
          </div>
        )}
        {calcSuccess && (
          <div className="mt-2 flex items-center gap-2 text-xs text-emerald-600 bg-emerald-50 p-2 rounded">
            <CheckCircle className="w-4 h-4" />
            <span>{t('calculations.status.completed')}</span>
          </div>
        )}
      </Card>

      {/* Quick Stats */}
      {(stats || loadingStats) && (
        <Card padding="md">
          <h3 className="text-sm font-semibold text-slate-800 mb-3">
            {t('analyticsPage.quickStats')} — {effectiveIndex}
          </h3>
          {loadingStats ? (
            <div className="flex items-center justify-center h-20 text-slate-400">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ) : stats ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="p-3 bg-green-50 rounded-lg border border-green-100">
                <span className="block text-[10px] text-green-600 uppercase font-bold">{t('analytics.max')}</span>
                <span className="text-xl font-bold text-green-700">{(stats.max ?? stats.avg ?? 0).toFixed(2)}</span>
              </div>
              <div className="p-3 bg-blue-50 rounded-lg border border-blue-100">
                <span className="block text-[10px] text-blue-600 uppercase font-bold">{t('analytics.mean')}</span>
                <span className="text-xl font-bold text-blue-700">{(stats.avg ?? stats.mean ?? 0).toFixed(2)}</span>
              </div>
              <div className="p-3 bg-amber-50 rounded-lg border border-amber-100">
                <span className="block text-[10px] text-amber-600 uppercase font-bold">{t('analytics.min')}</span>
                <span className="text-xl font-bold text-amber-700">{(stats.min ?? 0).toFixed(2)}</span>
              </div>
              <div className="p-3 bg-purple-50 rounded-lg border border-purple-100">
                <span className="block text-[10px] text-purple-600 uppercase font-bold">{t('analytics.pixelCount', 'Escenas')}</span>
                <span className="text-xl font-bold text-purple-700">{stats.count ?? 0}</span>
              </div>
            </div>
          ) : null}
        </Card>
      )}

      {/* Timeseries Chart */}
      <Card padding="md">
        <h3 className="text-sm font-semibold text-slate-800 mb-2">
          {t('analyticsPage.vegetationTrends', { index: effectiveIndex })}
        </h3>
        <p className="text-xs text-slate-500 mb-3">{t('analyticsPage.historicEvolution')}</p>
        <div className="h-56 bg-slate-50 rounded-lg">
          <TimeseriesChart entityId={selectedEntityId} indexType={effectiveIndex} />
        </div>
      </Card>

      {/* Jobs for this parcel */}
      <Card padding="md">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-slate-600" />
            <h3 className="text-sm font-semibold text-slate-800">{t('calculations.jobHistory')}</h3>
          </div>
          <button onClick={loadJobs} disabled={loadingJobs} className="p-1 text-slate-400 hover:text-slate-600">
            <RefreshCw className={`w-3.5 h-3.5 ${loadingJobs ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {loadingJobs && jobs.length === 0 ? (
          <div className="h-16 flex items-center justify-center text-slate-400 text-sm">
            <Loader2 className="w-4 h-4 animate-spin mr-2" />{t('common.loading')}
          </div>
        ) : jobs.length === 0 ? (
          <div className="text-center py-4 text-slate-400 text-sm">
            {t('analyticsPage.noHistory')}
          </div>
        ) : (
          <div className="space-y-1.5 max-h-64 overflow-y-auto">
            {jobs.map(job => (
              <div key={job.id} className="flex items-center justify-between py-2 px-3 bg-slate-50 rounded-lg text-sm hover:bg-slate-100">
                <div className="flex items-center gap-2">
                  {statusIcon(job.status)}
                  <span className="text-slate-700 capitalize">{job.job_type === 'calculate_index' ? effectiveIndex : job.job_type}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-slate-400">
                    {new Date(job.created_at).toLocaleDateString('es-ES', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
                  </span>
                  {job.error_message && (
                    <span className="text-xs text-red-500 max-w-[150px] truncate" title={job.error_message}>
                      {job.error_message}
                    </span>
                  )}
                  <button
                    onClick={() => handleDeleteJob(job.id)}
                    className="p-1 text-slate-300 hover:text-red-500 transition-colors"
                    title={t('common.delete')}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};
