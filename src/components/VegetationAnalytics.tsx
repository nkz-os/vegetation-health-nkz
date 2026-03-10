/**
 * VegetationAnalytics - Main analysis view for a selected parcel.
 *
 * Simplified flow:
 *  1. User selects parcel (from dashboard)
 *  2. Clicks "Analyze" → backend downloads best scene + calculates ALL indices
 *  3. After completion, user switches between indices to see stats + map layer
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Card } from '@nekazari/ui-kit';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationApi } from '../services/api';
import TimeseriesChart from './widgets/TimeseriesChart';
import { useAuth } from '../hooks/useAuth';
import { useTranslation } from 'react-i18next';
import type { VegetationJob } from '../types';
import {
  Loader2, Calculator, AlertCircle, CheckCircle,
  RefreshCw, BarChart3, Satellite, Leaf,
  Trash2, Clock, Play, XCircle,
} from 'lucide-react';

// Main indices the user can browse after analysis
const MAIN_INDICES = ['NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE'] as const;

const INDEX_LABELS: Record<string, { name: string; description: string; color: string }> = {
  NDVI: { name: 'NDVI', description: 'Salud general', color: 'emerald' },
  EVI: { name: 'EVI', description: 'Vegetación densa', color: 'green' },
  SAVI: { name: 'SAVI', description: 'Suelo expuesto', color: 'lime' },
  GNDVI: { name: 'GNDVI', description: 'Clorofila', color: 'teal' },
  NDRE: { name: 'NDRE', description: 'Estrés temprano', color: 'cyan' },
};

export const VegetationAnalytics: React.FC = () => {
  const { t } = useTranslation();
  const {
    selectedIndex, setSelectedIndex,
    selectedEntityId, setSelectedEntityId,
    setActiveJobId, indexResults, setIndexResults,
  } = useVegetationContext();
  const { isAuthenticated } = useAuth();
  const api = useVegetationApi();

  // Local state
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [downloadJobId, setDownloadJobId] = useState<string | null>(null);
  const [analyzeProgress, setAnalyzeProgress] = useState<string>('');
  const [jobs, setJobs] = useState<VegetationJob[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [loadingResults, setLoadingResults] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Date range for analysis
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().split('T')[0];
  });
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split('T')[0]);

  const effectiveIndex = selectedIndex || 'NDVI';

  // Load existing results when entity changes
  const loadResults = useCallback(async () => {
    if (!selectedEntityId || !isAuthenticated) return;
    setLoadingResults(true);
    try {
      const data = await api.getEntityResults(selectedEntityId);
      if (data.indices) {
        setIndexResults(data.indices);
        // Auto-select first available index
        const availableIndices = Object.keys(data.indices);
        if (availableIndices.length > 0) {
          const idx = availableIndices.includes(effectiveIndex)
            ? effectiveIndex
            : availableIndices[0];
          setSelectedIndex(idx as any);
          const result = data.indices[idx];
          if (result) {
            setActiveJobId(result.job_id);
          }
        }
      }
    } catch {
      // No results yet — that's fine
    } finally {
      setLoadingResults(false);
    }
  }, [selectedEntityId, isAuthenticated]);

  useEffect(() => {
    loadResults();
  }, [loadResults]);

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

  // Poll for job completion during analysis
  useEffect(() => {
    if (!downloadJobId) return;

    const poll = async () => {
      try {
        const details = await api.getJobDetails(downloadJobId);
        const status = details?.job?.status;

        if (status === 'completed') {
          setAnalyzeProgress(t('calculations.status.completed'));
          // Download done — now wait for calculation jobs to finish
          // Poll for results
          if (selectedEntityId) {
            const data = await api.getEntityResults(selectedEntityId);
            const activeCount = data.active_jobs || 0;

            if (activeCount === 0 && data.has_results) {
              // All done!
              setIndexResults(data.indices);
              setIsAnalyzing(false);
              setDownloadJobId(null);
              setAnalyzeProgress('');
              // Select NDVI by default
              if (data.indices['NDVI']) {
                setSelectedIndex('NDVI');
                setActiveJobId(data.indices['NDVI'].job_id);
              } else {
                const first = Object.keys(data.indices)[0];
                if (first) {
                  setSelectedIndex(first as any);
                  setActiveJobId(data.indices[first].job_id);
                }
              }
              loadJobs();
              return;
            }

            if (data.has_results) {
              // Some results available, show partial
              setIndexResults(data.indices);
              setAnalyzeProgress(`${Object.keys(data.indices).length}/${MAIN_INDICES.length} ${t('analytics.indexSelector').toLowerCase()}...`);
            } else {
              setAnalyzeProgress(t('analyticsPage.processingHistoric'));
            }
          }
        } else if (status === 'failed') {
          setAnalyzeError(details?.job?.error_message || t('errors.calculationFailed'));
          setIsAnalyzing(false);
          setDownloadJobId(null);
          loadJobs();
          return;
        } else {
          setAnalyzeProgress(details?.job?.progress_message || t('common.loading'));
        }
      } catch {
        // Transient error, keep polling
      }
    };

    pollRef.current = setInterval(poll, 3000);
    poll(); // First poll immediately

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [downloadJobId, selectedEntityId]);

  // Handle "Analyze" button click
  const handleAnalyze = async () => {
    if (!selectedEntityId) return;
    setIsAnalyzing(true);
    setAnalyzeError(null);
    setAnalyzeProgress(t('analyticsPage.processingHistoricDesc'));

    try {
      const result = await api.analyzeParcel({
        entity_id: selectedEntityId,
        start_date: startDate,
        end_date: endDate,
      });
      setDownloadJobId(result.job_id);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || t('errors.serverError');
      setAnalyzeError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      setIsAnalyzing(false);
    }
  };

  // When user switches index, update active job
  const handleIndexSwitch = (idx: string) => {
    setSelectedIndex(idx as any);
    const result = indexResults[idx];
    if (result) {
      setActiveJobId(result.job_id);
    }
  };

  const handleDeleteJob = async (jobId: string) => {
    try {
      await api.deleteJob(jobId);
      setJobs(prev => prev.filter(j => j.id !== jobId));
    } catch { /* ignore */ }
  };

  // Auth guard
  if (!isAuthenticated) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">{t('analyticsPage.loginRequired')}</p>
      </div>
    );
  }

  // No parcel selected
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

  const parcelShortName = selectedEntityId.split(':').pop() || selectedEntityId;
  const hasResults = Object.keys(indexResults).length > 0;
  const currentResult = indexResults[effectiveIndex];

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

      {/* Analyze Section */}
      <Card padding="md">
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Satellite className="w-5 h-5 text-emerald-600" />
            <h3 className="text-sm font-semibold text-slate-800">
              {t('configPanel.vegetationAnalysis')}
            </h3>
          </div>

          {/* Date range */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2">
              <label className="text-xs text-slate-500">{t('analyticsPage.from')}</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:ring-emerald-500 focus:border-emerald-500"
              />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs text-slate-500">{t('analyticsPage.to')}</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:ring-emerald-500 focus:border-emerald-500"
              />
            </div>
          </div>

          {/* Analyze button */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleAnalyze}
              disabled={isAnalyzing}
              className="flex items-center gap-2 px-5 py-2.5 bg-emerald-600 text-white rounded-lg text-sm font-semibold hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
            >
              {isAnalyzing ? (
                <><Loader2 className="w-4 h-4 animate-spin" />{t('analyticsPage.analyzing')}</>
              ) : (
                <><Calculator className="w-4 h-4" />{t('dashboard.analyze')}</>
              )}
            </button>
            {isAnalyzing && analyzeProgress && (
              <span className="text-xs text-slate-500 animate-pulse">{analyzeProgress}</span>
            )}
          </div>

          {/* Error */}
          {analyzeError && (
            <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 p-3 rounded-lg">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{analyzeError}</span>
            </div>
          )}

          {/* Partial results during analysis */}
          {isAnalyzing && hasResults && (
            <div className="flex items-center gap-2 text-xs text-emerald-600 bg-emerald-50 p-2 rounded-lg">
              <CheckCircle className="w-4 h-4" />
              <span>{Object.keys(indexResults).length} {t('analytics.indexSelector').toLowerCase()} {t('calculations.status.completed').toLowerCase()}</span>
            </div>
          )}
        </div>
      </Card>

      {/* Index Results Browser (shown when results available) */}
      {hasResults && (
        <>
          {/* Index pills */}
          <Card padding="md">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-800">{t('analytics.indexSelector')}</h3>
                <button
                  onClick={loadResults}
                  disabled={loadingResults}
                  className="p-1 text-slate-400 hover:text-emerald-600"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingResults ? 'animate-spin' : ''}`} />
                </button>
              </div>
              <div className="flex flex-wrap gap-2">
                {MAIN_INDICES.map((idx) => {
                  const result = indexResults[idx];
                  const isAvailable = !!result;
                  const isActive = effectiveIndex === idx;
                  const info = INDEX_LABELS[idx];

                  return (
                    <button
                      key={idx}
                      onClick={() => isAvailable && handleIndexSwitch(idx)}
                      disabled={!isAvailable}
                      className={`
                        px-3 py-2 rounded-lg text-sm font-medium transition-all border
                        ${isActive
                          ? 'bg-emerald-600 text-white border-emerald-600 shadow-md'
                          : isAvailable
                            ? 'bg-white text-slate-700 border-slate-200 hover:border-emerald-300 hover:bg-emerald-50'
                            : 'bg-slate-50 text-slate-300 border-slate-100 cursor-not-allowed'
                        }
                      `}
                    >
                      <span className="block font-bold">{info.name}</span>
                      <span className={`block text-[10px] ${isActive ? 'text-emerald-100' : 'text-slate-400'}`}>
                        {info.description}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </Card>

          {/* Stats for selected index */}
          {currentResult && currentResult.statistics && (
            <Card padding="md">
              <h3 className="text-sm font-semibold text-slate-800 mb-3">
                {t('analyticsPage.quickStats')} — {effectiveIndex}
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="p-3 bg-green-50 rounded-lg border border-green-100">
                  <span className="block text-[10px] text-green-600 uppercase font-bold">{t('analytics.max')}</span>
                  <span className="text-xl font-bold text-green-700">
                    {currentResult.statistics.max != null ? currentResult.statistics.max.toFixed(3) : '-'}
                  </span>
                </div>
                <div className="p-3 bg-blue-50 rounded-lg border border-blue-100">
                  <span className="block text-[10px] text-blue-600 uppercase font-bold">{t('analytics.mean')}</span>
                  <span className="text-xl font-bold text-blue-700">
                    {currentResult.statistics.mean != null ? currentResult.statistics.mean.toFixed(3) : '-'}
                  </span>
                </div>
                <div className="p-3 bg-amber-50 rounded-lg border border-amber-100">
                  <span className="block text-[10px] text-amber-600 uppercase font-bold">{t('analytics.min')}</span>
                  <span className="text-xl font-bold text-amber-700">
                    {currentResult.statistics.min != null ? currentResult.statistics.min.toFixed(3) : '-'}
                  </span>
                </div>
                <div className="p-3 bg-purple-50 rounded-lg border border-purple-100">
                  <span className="block text-[10px] text-purple-600 uppercase font-bold">{t('analytics.stdDev')}</span>
                  <span className="text-xl font-bold text-purple-700">
                    {currentResult.statistics.std_dev != null ? currentResult.statistics.std_dev.toFixed(3) : '-'}
                  </span>
                </div>
              </div>
              {currentResult.created_at && (
                <p className="text-[10px] text-slate-400 mt-2">
                  {t('analyticsPage.lastUpdate')} {new Date(currentResult.created_at).toLocaleString('es-ES')}
                </p>
              )}
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
        </>
      )}

      {/* Empty state — no results and not analyzing */}
      {!hasResults && !isAnalyzing && !loadingResults && (
        <Card padding="md">
          <div className="text-center py-8">
            <Leaf className="w-12 h-12 text-slate-200 mx-auto mb-3" />
            <h3 className="text-lg font-medium text-slate-600 mb-1">{t('analytics.noScenes')}</h3>
            <p className="text-sm text-slate-400 max-w-md mx-auto">
              {t('analyticsPage.inactiveMonitoringDesc')}
            </p>
          </div>
        </Card>
      )}

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
                  <span className="text-slate-700 capitalize">
                    {job.job_type === 'download' ? 'Download' : job.job_type === 'calculate_index' ? (job.result?.index_type || effectiveIndex) : job.job_type}
                  </span>
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
