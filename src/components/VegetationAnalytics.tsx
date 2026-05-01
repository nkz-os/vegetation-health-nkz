/**
 * VegetationAnalytics - Main analysis view for a selected parcel.
 *
 * Simplified flow:
 *  1. User selects parcel (from dashboard)
 *  2. Clicks "Analyze" → backend downloads best scene + calculates ALL indices
 *  3. After completion, user switches between indices to see stats + map layer
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationApi } from '../services/api';
import { SetupWizard } from './pages/SetupWizard';
import { IndexPillSelector } from './widgets/IndexPillSelector';
import { useAuth } from '../hooks/useAuth';
import { useTranslation } from '@nekazari/sdk';
import type { VegetationJob, CustomFormula, CropSeason } from '../types';
import {
  Loader2, AlertCircle, CheckCircle,
  RefreshCw, Satellite, Sprout,
} from 'lucide-react';

// Main indices the user can browse after analysis
const MAIN_INDICES = ['NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE'] as const;
const AVAILABLE_BANDS = ['B02', 'B03', 'B04', 'B05', 'B06', 'B07', 'B08', 'B8A', 'B11', 'B12'] as const;

export const VegetationAnalytics: React.FC = () => {
  const { t } = useTranslation();
  const {
    selectedIndex, setSelectedIndex,
    selectedEntityId, setSelectedEntityId,
    selectedSceneId,
    setActiveJobId,
    setActiveRasterPath,
    indexResults, setIndexResults,
  } = useVegetationContext();
  const { isAuthenticated } = useAuth();
  const api = useVegetationApi();

  // Local state
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [analysisJobId, setAnalysisJobId] = useState<string | null>(null);
  const [analyzeProgress, setAnalyzeProgress] = useState<string>('');
  const [jobs, setJobs] = useState<VegetationJob[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [loadingResults, setLoadingResults] = useState(false);
  const [customFormulas, setCustomFormulas] = useState<CustomFormula[]>([]);
  const [selectedCustomFormulaIds, setSelectedCustomFormulaIds] = useState<string[]>([]);
  const [customName, setCustomName] = useState('');
  const [customFormula, setCustomFormula] = useState('');
  const [customValidationMsg, setCustomValidationMsg] = useState<string | null>(null);
  const [customError, setCustomError] = useState<string | null>(null);
  const [exportingFormat, setExportingFormat] = useState<string | null>(null);
  const [deletingJobId, setDeletingJobId] = useState<string | null>(null);
  const [customBusy, setCustomBusy] = useState(false);

  // Monitoring state
  const [monitoringActive, setMonitoringActive] = useState(false);
  const [showSetupWizard, setShowSetupWizard] = useState(false);
  const [showCustomCreator, setShowCustomCreator] = useState(false);
  const [cropSeasons, setCropSeasons] = useState<CropSeason[]>([]);
  const [selectedSeasonId, setSelectedSeasonId] = useState<string>('');
  const [loadingSeasons, setLoadingSeasons] = useState(false);

  const effectiveIndex = selectedIndex || 'NDVI';

  // Load existing results when entity changes
  const loadResults = useCallback(async () => {
    if (!selectedEntityId || !isAuthenticated) return;
    setLoadingResults(true);
    try {
      const data = await api.getEntityResults(
        selectedEntityId,
        selectedSceneId ? { sceneId: selectedSceneId } : undefined,
      );
      if (data.indices) {
        setIndexResults(data.indices);
        const availableIndices = Object.keys(data.indices);
        if (availableIndices.length > 0) {
          const idx = availableIndices.includes(effectiveIndex)
            ? effectiveIndex
            : availableIndices[0];
          if (idx !== selectedIndex) {
            setSelectedIndex(idx);
          }
          const result = data.indices[idx];
          if (result) {
            setActiveJobId(result.job_id);
            setActiveRasterPath(result.raster_path ?? null);
          }
        }
      }
    } catch {
      // No results yet — that's fine
    } finally {
      setLoadingResults(false);
    }
  }, [selectedEntityId, selectedSceneId, isAuthenticated, effectiveIndex, selectedIndex]);

  useEffect(() => {
    loadResults();
  }, [loadResults]);

  // Check if monitoring is active for this entity
  useEffect(() => {
    if (!selectedEntityId || !isAuthenticated) {
      setMonitoringActive(false);
      return;
    }
    api.getSubscriptionForEntity(selectedEntityId)
      .then(sub => setMonitoringActive(!!sub?.is_active))
      .catch(() => setMonitoringActive(false));
  }, [selectedEntityId, isAuthenticated]);

  // Load existing crop seasons for this entity
  const loadCropSeasons = useCallback(async () => {
    if (!selectedEntityId || !isAuthenticated) {
      setCropSeasons([]);
      return;
    }
    setLoadingSeasons(true);
    try {
      const seasons = await api.listCropSeasons(selectedEntityId);
      setCropSeasons(seasons);
    } catch {
      setCropSeasons([]);
    } finally {
      setLoadingSeasons(false);
    }
  }, [selectedEntityId, isAuthenticated, api]);

  useEffect(() => {
    loadCropSeasons();
  }, [loadCropSeasons]);

  const loadCustomFormulas = useCallback(async () => {
    if (!isAuthenticated) return;
    try {
      const response = await api.listCustomFormulas();
      setCustomFormulas(response.items || []);
    } catch {
      setCustomFormulas([]);
    }
  }, [api, isAuthenticated]);

  useEffect(() => {
    loadCustomFormulas();
  }, [loadCustomFormulas]);

  // Called when SetupWizard completes
  const handleMonitoringActivated = () => {
    setShowSetupWizard(false);
    setMonitoringActive(true);
    loadResults();
  };

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
    if (!analysisJobId || !selectedEntityId) return;

    const poll = async () => {
      try {
        const details = await api.getJobDetails(analysisJobId);
        const status = details?.job?.status;

        if (status === 'completed') {
          setAnalyzeProgress(t('calculations.status.completed'));
          // Download done — now wait for calculation jobs to finish
          // Poll for results
          const data = await api.getEntityResults(selectedEntityId);
          const activeCount = data.active_jobs || 0;

          if (activeCount === 0 && data.has_results) {
            // All done!
            setIndexResults(data.indices);
            setIsAnalyzing(false);
            setAnalysisJobId(null);
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
        } else if (status === 'failed') {
          setAnalyzeError(details?.job?.error_message || t('errors.calculationFailed'));
          setIsAnalyzing(false);
          setAnalysisJobId(null);
          loadJobs();
          return;
        } else {
          setAnalyzeProgress(details?.job?.progress_message || t('common.loading'));
        }
      } catch {
        // Transient error, keep polling
      }
    };

    const intervalId = setInterval(poll, 3000);
    poll(); // First poll immediately

    return () => clearInterval(intervalId);
  }, [analysisJobId, selectedEntityId]);

  // Handle crop season selection
  const handleSeasonChange = (seasonId: string) => {
    setSelectedSeasonId(seasonId);
  };

  // Handle "Analyze" button click
  const handleAnalyze = async () => {
    if (!selectedEntityId) return;
    setIsAnalyzing(true);
    setAnalyzeError(null);
    setAnalyzeProgress(t('analyticsPage.processingHistoricDesc'));

    // Derive date range from selected crop season
    let start_date: string;
    let end_date: string;
    if (selectedSeasonId) {
      const season = cropSeasons.find(s => s.id === selectedSeasonId);
      start_date = season?.start_date || '';
      const d = new Date();
      end_date = season?.end_date || d.toISOString().split('T')[0];
    } else {
      const d = new Date();
      d.setDate(d.getDate() - 30);
      start_date = d.toISOString().split('T')[0];
      end_date = new Date().toISOString().split('T')[0];
    }

    try {
      const result = await api.analyzeParcel({
        entity_id: selectedEntityId,
        start_date,
        end_date,
        custom_formulas: selectedCustomFormulaIds,
      });
      setAnalysisJobId(result.job_id);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || t('errors.serverError');
      setAnalyzeError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      setIsAnalyzing(false);
    }
  };

  const handleCreateAndAttachFormula = async () => {
    if (!customName.trim() || !customFormula.trim() || !selectedEntityId) return;
    setCustomBusy(true);
    setCustomError(null);
    setCustomValidationMsg(null);
    try {
      const created = await api.createCustomFormula({
        name: customName.trim(),
        formula: customFormula.trim(),
      });
      setSelectedCustomFormulaIds(prev => Array.from(new Set([...prev, created.id])));
      setCustomName('');
      setCustomFormula('');
      await loadCustomFormulas();
      await handleAnalyze();
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || t('errors.serverError');
      setCustomError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setCustomBusy(false);
    }
  };

  const handleDeleteCustomFormula = async (id: string) => {
    setCustomBusy(true);
    setCustomError(null);
    try {
      await api.deleteCustomFormula(id);
      setSelectedCustomFormulaIds(prev => prev.filter(item => item !== id));
      await loadCustomFormulas();
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || t('errors.serverError');
      setCustomError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setCustomBusy(false);
    }
  };

  const handleDeleteJob = async (jobId: string) => {
    setDeletingJobId(jobId);
    try {
      await api.deleteJob(jobId);
      setJobs(prev => prev.filter(j => j.id !== jobId));
    } catch {
      // Silently fail — row stays
    } finally {
      setDeletingJobId(null);
    }
  };

  const handleExportResult = async (format: 'geojson' | 'shapefile' | 'csv') => {
    if (!selectedEntityId) return;
    setExportingFormat(format);
    try {
      let blob: Blob;
      let filename: string;
      switch (format) {
        case 'geojson':
          blob = await api.exportPrescriptionGeojson(selectedEntityId);
          filename = `prescription_${selectedEntityId}.geojson`;
          break;
        case 'shapefile':
          blob = await api.exportPrescriptionShapefile(selectedEntityId);
          filename = `prescription_${selectedEntityId}.zip`;
          break;
        case 'csv':
          blob = await api.exportPrescriptionCsv(selectedEntityId);
          filename = `prescription_${selectedEntityId}.csv`;
          break;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('[Vegetation] Export failed:', err);
      setAnalyzeError(t('prescription.exportError'));
      setTimeout(() => setAnalyzeError(null), 5000);
    } finally { setExportingFormat(null); }
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

  return (
    <div className="space-y-6 max-w-4xl mx-auto py-6 px-4">
      {/* Header: parcel name + back */}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-slate-800">{parcelShortName}</h2>
        <div className="flex items-center gap-3">
          <span className={`inline-flex items-center gap-1.5 text-xs ${monitoringActive ? 'text-emerald-600' : 'text-slate-400'}`}>
            <span className={`w-2 h-2 rounded-full ${monitoringActive ? 'bg-emerald-500' : 'bg-slate-300'}`} />
            {monitoringActive ? t('analyticsPage.activeMonitoring') : t('analyticsPage.inactiveMonitoring')}
          </span>
          <button onClick={() => setSelectedEntityId(null)} className="text-sm text-slate-400 hover:text-slate-600">
            {t('analyticsPage.changeParcel')}
          </button>
        </div>
      </div>

      {/* Crop Season + Monitoring config */}
      <div className="flex items-center gap-3 flex-wrap bg-white rounded-xl border border-slate-200 p-3">
        <Sprout className="w-5 h-5 text-emerald-600 shrink-0" />
        <select
          value={selectedSeasonId}
          onChange={(e) => handleSeasonChange(e.target.value)}
          disabled={loadingSeasons}
          className="text-sm border-0 bg-transparent focus:ring-0 font-medium text-slate-700 min-w-[180px]"
        >
          <option value="">{t('cropSeason.allSeasons')}</option>
          {(Array.isArray(cropSeasons) ? cropSeasons : []).map((season) => {
            const label = `${t(`cropSeason.${season.crop_type}`, season.crop_type)} ${season.start_date} – ${season.end_date || '···'}`;
            return <option key={season.id} value={season.id}>{label}</option>;
          })}
        </select>
        {loadingSeasons && <Loader2 className="w-4 h-4 animate-spin text-slate-400" />}
        <div className="flex-1" />
        <button
          onClick={() => setShowSetupWizard(true)}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
        >
          <Sprout className="w-3 h-3" />
          {t('cropSeason.newSeason')}
        </button>
      </div>

      {/* Analyze + Index pills */}
      <div className="bg-white rounded-xl border border-slate-200 p-4 space-y-3">
        <div className="flex items-start gap-3 flex-wrap">
          <div className="flex-1 min-w-0">
            <p className="text-xs text-slate-500 mb-2">{t('layerControl.spectralIndex')}</p>
            <IndexPillSelector
              selectedIndex={effectiveIndex}
              onIndexChange={(idx) => setSelectedIndex(idx)}
              customIndexOptions={(Array.isArray(customFormulas) ? customFormulas : []).map(f => ({ key: f.id, label: f.name }))}
            />
          </div>
          <button
            onClick={handleAnalyze}
            disabled={isAnalyzing}
            className="flex items-center gap-2 px-5 py-2.5 bg-emerald-600 text-white rounded-lg text-sm font-semibold hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm shrink-0"
          >
            {isAnalyzing ? (
              <><Loader2 className="w-4 h-4 animate-spin" />{t('analyticsPage.processingHistoric')}</>
            ) : hasResults ? (
              <><RefreshCw className="w-4 h-4" />{t('configPanel.forceLatestScene')}</>
            ) : (
              <><Satellite className="w-4 h-4" />{t('configPanel.analyzeFirstTime')}</>
            )}
          </button>
        </div>

        {/* Custom formulas — always visible, compact */}
        <div className="border-t border-slate-100 pt-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-slate-500">{t('analyticsPage.customIndex')}</span>
            <button
              onClick={() => setShowCustomCreator(!showCustomCreator)}
              className="text-xs text-emerald-600 hover:text-emerald-800"
            >
              {showCustomCreator ? t('common.close') : '+ ' + t('analyticsPage.createNewFormula')}
            </button>
          </div>
          {customFormulas.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {customFormulas.map(f => (
                <label key={f.id} className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs cursor-pointer border transition-colors ${
                  selectedCustomFormulaIds.includes(f.id)
                    ? 'bg-emerald-50 border-emerald-300 text-emerald-700'
                    : 'bg-white border-slate-200 text-slate-600 hover:border-emerald-200'
                }`}>
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={selectedCustomFormulaIds.includes(f.id)}
                    onChange={(e) => {
                      setSelectedCustomFormulaIds(prev => e.target.checked
                        ? Array.from(new Set([...prev, f.id]))
                        : prev.filter(id => id !== f.id));
                    }}
                  />
                  {f.name}
                  <button
                    onClick={(e) => { e.preventDefault(); handleDeleteCustomFormula(f.id); }}
                    className="text-slate-400 hover:text-red-500 ml-0.5"
                    title={t('common.delete')}
                  >×</button>
                </label>
              ))}
            </div>
          )}
          {showCustomCreator && (
            <div className="space-y-2 pt-2 border-t border-slate-100">
              <div className="flex gap-2 flex-wrap">
                <input type="text" value={customName} onChange={(e) => setCustomName(e.target.value)}
                  className="flex-1 text-xs border border-slate-200 rounded px-2 py-1.5 min-w-[100px]" placeholder={t('analyticsPage.customName')} />
                <input type="text" value={customFormula} onChange={(e) => setCustomFormula(e.target.value)}
                  className="flex-[2] text-xs border border-slate-200 rounded px-2 py-1.5 font-mono" placeholder="(B08-B11)/(B08+B11)" />
                <button onClick={handleCreateAndAttachFormula} disabled={customBusy || !customFormula.trim() || !customName.trim()}
                  className="px-3 py-1.5 bg-emerald-600 text-white rounded text-xs hover:bg-emerald-700 disabled:opacity-50">
                  {t('analyticsPage.saveAndRunAnalysis')}
                </button>
              </div>
              <div className="flex flex-wrap gap-1">
                {AVAILABLE_BANDS.map(band => (
                  <button key={band} onClick={() => setCustomFormula(prev => prev ? `${prev}${band}` : band)}
                    className="px-1.5 py-0.5 text-[10px] bg-slate-100 rounded text-slate-500 hover:bg-emerald-100 hover:text-emerald-700">{band}</button>
                ))}
              </div>
              {customValidationMsg && <p className="text-xs text-emerald-600">{customValidationMsg}</p>}
              {customError && <p className="text-xs text-red-600">{customError}</p>}
            </div>
          )}
        </div>
      </div>

      {/* Analyze error / progress */}
      {analyzeError && (
        <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 p-3 rounded-lg">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span>{analyzeError}</span>
        </div>
      )}
      {isAnalyzing && analyzeProgress && (
        <div className="flex items-center gap-2 text-xs text-emerald-600 bg-emerald-50 p-3 rounded-lg">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>{analyzeProgress}</span>
        </div>
      )}
      {isAnalyzing && hasResults && (
        <div className="flex items-center gap-2 text-xs text-emerald-600 bg-emerald-50 p-2 rounded-lg">
          <CheckCircle className="w-4 h-4" />
          <span>{Object.keys(indexResults).length} {t('analytics.indexSelector').toLowerCase()} {t('calculations.status.completed').toLowerCase()}</span>
        </div>
      )}

      {/* No data yet — show why and what to do */}
      {!hasResults && !isAnalyzing && !loadingResults && (
        <div className="text-center py-10 bg-slate-50 rounded-xl border border-dashed border-slate-300">
          <Satellite className="w-12 h-12 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500 text-sm mb-4">
            {cropSeasons.length === 0
              ? t('analyticsPage.firstStepDesc')
              : t('analyticsPage.readyToAnalyzeDesc')}
          </p>
          {cropSeasons.length === 0 ? (
            <button
              onClick={() => setShowSetupWizard(true)}
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-emerald-600 text-white rounded-lg text-sm font-semibold hover:bg-emerald-700 transition-colors shadow-sm"
            >
              <Sprout className="w-4 h-4" />
              {t('cropSeason.newSeason')}
            </button>
          ) : (
            <button
              onClick={handleAnalyze}
              disabled={isAnalyzing}
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-emerald-600 text-white rounded-lg text-sm font-semibold hover:bg-emerald-700 disabled:opacity-50 transition-colors shadow-sm"
            >
              {isAnalyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Satellite className="w-4 h-4" />}
              {t('configPanel.analyzeFirstTime')}
            </button>
          )}
        </div>
      )}

      {/* Link to unified viewer */}
      {hasResults && (
        <a
          href={`/module/vegetation?entityId=${encodeURIComponent(selectedEntityId)}&tab=analysis`}
          className="flex items-center justify-center gap-2 w-full py-3 bg-slate-800 text-white rounded-xl text-sm font-medium hover:bg-slate-900 transition-colors"
        >
          🗺 {t('analyticsPage.openInViewer')}
        </a>
      )}

      {/* Recent history — last 5 jobs, compact */}
      {jobs.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 bg-slate-50 border-b border-slate-100">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{t('calculations.jobHistory')}</h3>
            <button onClick={loadJobs} disabled={loadingJobs} className="p-0.5 text-slate-400 hover:text-slate-600">
              <RefreshCw className={`w-3 h-3 ${loadingJobs ? 'animate-spin' : ''}`} />
            </button>
          </div>
          <div className="divide-y divide-slate-50">
            {[...(Array.isArray(jobs) ? jobs : [])]
              .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
              .slice(0, 5)
              .map((job) => {
                const statusColor = job.status === 'completed' ? 'text-emerald-500' : job.status === 'failed' ? 'text-red-400' : 'text-amber-400';
                return (
                  <div key={job.id} className="flex items-center gap-3 px-4 py-2 text-xs hover:bg-slate-50 group">
                    <span className="text-slate-400 w-16 shrink-0">
                      {(job.result?.sensing_date || job.parameters?.date || job.created_at)
                        ? new Date(job.result?.sensing_date || job.parameters?.date || job.created_at).toLocaleDateString('es-ES', { day: '2-digit', month: 'short' })
                        : '-'}
                    </span>
                    <span className="font-medium text-slate-700 w-12 shrink-0">{job.index_type || '-'}</span>
                    <span className="text-slate-500 font-mono w-14 text-right shrink-0">{job.result_stats?.mean != null ? job.result_stats.mean.toFixed(3) : '-'}</span>
                    <span className="text-slate-400 w-10 text-right shrink-0">{job.result?.cloud_coverage_pct != null ? `${Number(job.result.cloud_coverage_pct).toFixed(0)}%` : '-'}</span>
                    <span className={`${statusColor} shrink-0`}>{job.status === 'completed' ? '✓' : job.status === 'failed' ? '✗' : '·'}</span>
                    <div className="flex-1" />
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeleteJob(job.id); }}
                      disabled={deletingJobId === job.id}
                      className="text-slate-300 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                    >×</button>
                  </div>
                );
              })}
          </div>
        </div>
      )}

      {/* VRA + Export — visible only when data exists */}
      {hasResults && (
        <div className="flex items-center gap-2 justify-end flex-wrap">
          <a
            href={`/module/gis-routing?entityId=${encodeURIComponent(selectedEntityId)}`}
            className="text-xs text-emerald-600 hover:text-emerald-800 font-medium"
            title={t('analyticsPage.useInGuidance')}
          >
            🚜 {t('analyticsPage.useInGuidance')}
          </a>
          <span className="text-slate-200">|</span>
          <button
            onClick={() => setSelectedIndex('VRA_ZONES')}
            className="text-xs text-slate-500 hover:text-emerald-600"
          >
            🗺 {t('layerControl.vra')}
          </button>
          <span className="text-slate-200">|</span>
          <select
            onChange={(e) => { const fmt = e.target.value as 'geojson' | 'shapefile' | 'csv'; if (fmt) handleExportResult(fmt); e.target.value = ''; }}
            disabled={exportingFormat !== null}
            className="text-xs border-0 bg-transparent text-slate-500 hover:text-emerald-600 cursor-pointer disabled:opacity-50"
            defaultValue=""
          >
            <option value="" disabled>{t('prescription.exportFormats')}</option>
            <option value="geojson">{t('prescription.geojson')}</option>
            <option value="shapefile">{t('prescription.shapefile')}</option>
            <option value="csv">{t('prescription.csv')}</option>
          </select>
        </div>
      )}

      {/* Setup Wizard Modal */}
      {selectedEntityId && (
        <SetupWizard
          open={showSetupWizard}
          onClose={() => setShowSetupWizard(false)}
          entityId={selectedEntityId}
          entityName={parcelShortName}
          onComplete={handleMonitoringActivated}
        />
      )}
    </div>
  );
};
