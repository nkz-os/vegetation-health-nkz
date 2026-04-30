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
import { SetupWizard } from './pages/SetupWizard';
import { IndexPillSelector } from './widgets/IndexPillSelector';
import { SmartTimeline } from './widgets/SmartTimeline';
import { useAuth } from '../hooks/useAuth';
import { useTranslation } from '@nekazari/sdk';
import type { VegetationJob, CustomFormula, CropSeason } from '../types';
import {
  Loader2, Calculator, AlertCircle, CheckCircle,
  RefreshCw, BarChart3, Satellite, Leaf,
  Activity, Power, Map, ChevronDown, Beaker, Sprout,
} from 'lucide-react';

// Main indices the user can browse after analysis
const MAIN_INDICES = ['NDVI', 'EVI', 'SAVI', 'GNDVI', 'NDRE'] as const;
const AVAILABLE_BANDS = ['B02', 'B03', 'B04', 'B05', 'B06', 'B07', 'B08', 'B8A', 'B11', 'B12'] as const;

export const VegetationAnalytics: React.FC = () => {
  const { t } = useTranslation();
  const {
    selectedIndex, setSelectedIndex,
    selectedEntityId, setSelectedEntityId,
    selectedSceneId, selectedDate,
    setActiveJobId,
    setActiveRasterPath,
    setSelectedSceneId,
    setSelectedDate,
    indexResults, setIndexResults,
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
  const [expandedIndexKey, setExpandedIndexKey] = useState<string | null>(null);
  const [customFormulas, setCustomFormulas] = useState<CustomFormula[]>([]);
  const [selectedCustomFormulaIds, setSelectedCustomFormulaIds] = useState<string[]>([]);
  const [customName, setCustomName] = useState('');
  const [customFormula, setCustomFormula] = useState('');
  const [customValidationMsg, setCustomValidationMsg] = useState<string | null>(null);
  const [customError, setCustomError] = useState<string | null>(null);
  const [exportingFormat, setExportingFormat] = useState<string | null>(null);
  const [customBusy, setCustomBusy] = useState(false);

  // Monitoring subscription state
  const [subscription, setSubscription] = useState<any | null>(null);
  const [loadingSub, setLoadingSub] = useState(false);
  const [togglingMonitoring, setTogglingMonitoring] = useState(false);
  const [showSetupWizard, setShowSetupWizard] = useState(false);
  const [showCustomCreator, setShowCustomCreator] = useState(false);
  const [cropSeasons, setCropSeasons] = useState<CropSeason[]>([]);
  const [selectedSeasonId, setSelectedSeasonId] = useState<string>('');
  const [loadingSeasons, setLoadingSeasons] = useState(false);

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

  // Load subscription status for this entity
  const loadSubscription = useCallback(async () => {
    if (!selectedEntityId || !isAuthenticated) {
      setSubscription(null);
      return;
    }
    setLoadingSub(true);
    try {
      const sub = await api.getSubscriptionForEntity(selectedEntityId);
      setSubscription(sub);
    } catch {
      setSubscription(null);
    } finally {
      setLoadingSub(false);
    }
  }, [selectedEntityId, isAuthenticated]);

  useEffect(() => {
    loadSubscription();
  }, [loadSubscription]);

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

  // Toggle monitoring active/inactive
  const handleToggleMonitoring = async () => {
    if (!subscription) return;
    setTogglingMonitoring(true);
    try {
      await api.updateSubscription(subscription.id, { is_active: !subscription.is_active });
      await loadSubscription();
    } catch {
      // Silently fail — user sees no change
    } finally {
      setTogglingMonitoring(false);
    }
  };

  // Called when SetupWizard completes
  const handleMonitoringActivated = () => {
    setShowSetupWizard(false);
    loadSubscription();
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

  // Handle crop season selection
  const handleSeasonChange = (seasonId: string) => {
    setSelectedSeasonId(seasonId);
    if (!seasonId) {
      const d = new Date();
      d.setDate(d.getDate() - 30);
      setStartDate(d.toISOString().split('T')[0]);
      setEndDate(new Date().toISOString().split('T')[0]);
    } else {
      const season = cropSeasons.find(s => s.id === seasonId);
      if (season) {
        setStartDate(season.start_date);
        setEndDate(season.end_date || new Date().toISOString().split('T')[0]);
      }
    }
  };

  // Handle date selection from SmartTimeline
  const handleDateSelect = (date: string, sceneId: string) => {
    setSelectedDate(new Date(date));
    setSelectedSceneId(sceneId);
  };

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
        custom_formulas: selectedCustomFormulaIds,
      });
      setDownloadJobId(result.job_id);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || t('errors.serverError');
      setAnalyzeError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      setIsAnalyzing(false);
    }
  };

  const getIndexDisplayName = (indexKey: string, result: any) => {
    if (result?.is_custom) return result?.formula_name || indexKey;
    return indexKey;
  };

  const handleValidateCustomFormula = async () => {
    if (!customFormula.trim()) return;
    setCustomBusy(true);
    setCustomError(null);
    setCustomValidationMsg(null);
    try {
      const response = await api.validateCustomFormula(customFormula.trim());
      setCustomValidationMsg(`${t('analyticsPage.formulaValid')} (${response.bands.join(', ')})`);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || t('errors.serverError');
      setCustomError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setCustomBusy(false);
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
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-800">{parcelShortName}</h2>
          <p className="text-sm text-slate-500">{t('analyticsPage.analyticsDashboard')}</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Monitoring status badge */}
          {!loadingSub && (
            subscription ? (
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium ${
                  subscription.is_active
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-amber-100 text-amber-700'
                }`}>
                  <Activity className="w-3 h-3" />
                  {subscription.is_active
                    ? t('analyticsPage.activeMonitoring')
                    : t('analyticsPage.inactiveMonitoring')}
                </span>
                <button
                  onClick={handleToggleMonitoring}
                  disabled={togglingMonitoring}
                  className={`p-1.5 rounded-lg transition-colors ${
                    subscription.is_active
                      ? 'text-amber-500 hover:bg-amber-50 hover:text-amber-700'
                      : 'text-emerald-500 hover:bg-emerald-50 hover:text-emerald-700'
                  }`}
                  title={subscription.is_active
                    ? t('monitoring.deactivate')
                    : t('monitoring.activate')}
                >
                  <Power className={`w-4 h-4 ${togglingMonitoring ? 'animate-pulse' : ''}`} />
                </button>
              </div>
            ) : (
              <button
                onClick={() => setShowSetupWizard(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
              >
                <Activity className="w-3 h-3" />
                {t('analyticsPage.configureMonitoring')}
              </button>
            )
          )}
          <button
            onClick={() => setSelectedEntityId(null)}
            className="text-sm text-slate-500 hover:text-slate-700 underline"
          >
            {t('analyticsPage.changeParcel')}
          </button>
        </div>
      </div>

      {/* Crop Season Selector */}
      <Card padding="md">
        <div className="flex items-center gap-3 flex-wrap">
          <Sprout className="w-5 h-5 text-emerald-600 shrink-0" />
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <select
              value={selectedSeasonId}
              onChange={(e) => handleSeasonChange(e.target.value)}
              disabled={loadingSeasons}
              className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:ring-emerald-500 focus:border-emerald-500 bg-white min-w-[200px]"
            >
              <option value="">{t('cropSeason.allSeasons')}</option>
              {(Array.isArray(cropSeasons) ? cropSeasons : []).map((season) => {
                const label = `${season.crop_type} ${season.start_date} - ${season.end_date || t('cropSeason.endDateHelp')}`;
                return (
                  <option key={season.id} value={season.id}>
                    {label}
                  </option>
                );
              })}
            </select>
            {loadingSeasons && (
              <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
            )}
          </div>
          <button
            onClick={() => setShowSetupWizard(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 text-white hover:bg-emerald-700 transition-colors shrink-0"
          >
            <Sprout className="w-3.5 h-3.5" />
            {t('cropSeason.newSeason')}
          </button>
        </div>
      </Card>

      {/* Index Pill Selector */}
      <Card padding="md">
        <IndexPillSelector
          selectedIndex={effectiveIndex}
          onIndexChange={(idx) => setSelectedIndex(idx)}
          customIndexOptions={(Array.isArray(customFormulas) ? customFormulas : []).map(f => ({ key: f.id, label: f.name }))}
        />
      </Card>

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

          {/* Custom indices: visible before running analysis (simple flow) */}
          <div className="rounded-xl border border-emerald-100 bg-gradient-to-br from-emerald-50/90 to-slate-50/80 p-4 space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <Beaker className="w-5 h-5 text-emerald-600 shrink-0" />
                <div>
                  <p className="text-sm font-semibold text-slate-800">{t('analyticsPage.customIndex')}</p>
                  <p className="text-xs text-slate-600 mt-0.5 leading-relaxed">{t('analyticsPage.customIndicesHint')}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowCustomCreator((v) => !v)}
                className="text-xs font-semibold text-emerald-700 hover:text-emerald-900 whitespace-nowrap shrink-0"
              >
                {showCustomCreator ? t('common.close') : t('analyticsPage.createNewFormula')}
              </button>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-slate-600">{t('analyticsPage.savedFormulas')}</span>
                {selectedCustomFormulaIds.length > 0 && (
                  <span className="text-xs text-emerald-700 font-medium">
                    {t('analyticsPage.customSelectedCount', { count: selectedCustomFormulaIds.length })}
                  </span>
                )}
              </div>
              {customFormulas.length === 0 ? (
                <p className="text-xs text-slate-500">{t('analyticsPage.noSavedFormulas')}</p>
              ) : (
                <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                  {customFormulas.map((formula) => (
                    <label
                      key={formula.id}
                      className="flex items-center gap-3 px-3 py-2 rounded-lg bg-white border border-slate-200/80 cursor-pointer hover:border-emerald-200"
                    >
                      <input
                        type="checkbox"
                        className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                        checked={selectedCustomFormulaIds.includes(formula.id)}
                        onChange={(e) => {
                          setSelectedCustomFormulaIds((prev) =>
                            e.target.checked
                              ? Array.from(new Set([...prev, formula.id]))
                              : prev.filter((id) => id !== formula.id),
                          );
                        }}
                      />
                      <span className="text-sm text-slate-800 flex-1 truncate" title={formula.formula}>
                        {formula.name}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          handleDeleteCustomFormula(formula.id);
                        }}
                        className="text-xs text-red-500 hover:text-red-700 shrink-0"
                      >
                        {t('common.delete')}
                      </button>
                    </label>
                  ))}
                </div>
              )}
            </div>

            {showCustomCreator && (
              <div className="space-y-3 pt-3 border-t border-emerald-100">
                <p className="text-xs text-slate-600">{t('analyticsPage.customFormulaHelp')}</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-slate-500">{t('analyticsPage.customName')}</label>
                    <input
                      type="text"
                      value={customName}
                      onChange={(e) => setCustomName(e.target.value)}
                      className="mt-1 w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:ring-emerald-500 focus:border-emerald-500 bg-white"
                      placeholder="NDMI"
                    />
                  </div>
                  <div className="sm:col-span-2">
                    <label className="text-xs text-slate-500">{t('analyticsPage.customFormula')}</label>
                    <input
                      type="text"
                      value={customFormula}
                      onChange={(e) => setCustomFormula(e.target.value)}
                      className="mt-1 w-full text-sm border border-slate-200 rounded-lg px-3 py-2 font-mono focus:ring-emerald-500 focus:border-emerald-500 bg-white"
                      placeholder="(B08-B11)/(B08+B11)"
                    />
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {AVAILABLE_BANDS.map((band) => (
                    <button
                      key={band}
                      type="button"
                      onClick={() => setCustomFormula((prev) => (prev ? `${prev}${band}` : band))}
                      className="px-2 py-0.5 text-xs bg-white border border-slate-200 rounded-md text-slate-600 hover:border-emerald-300 hover:text-emerald-800"
                    >
                      {band}
                    </button>
                  ))}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={handleValidateCustomFormula}
                    disabled={customBusy || !customFormula.trim()}
                    className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm hover:bg-white disabled:opacity-50"
                  >
                    {t('analyticsPage.validateFormula')}
                  </button>
                  <button
                    type="button"
                    onClick={handleCreateAndAttachFormula}
                    disabled={customBusy || !customFormula.trim() || !customName.trim() || !selectedEntityId}
                    className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-sm hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {t('analyticsPage.saveAndRunAnalysis')}
                  </button>
                </div>
                {customValidationMsg && <p className="text-xs text-emerald-600">{customValidationMsg}</p>}
                {customError && <p className="text-xs text-red-600">{customError}</p>}
              </div>
            )}
            {customError && !showCustomCreator && (
              <p className="text-xs text-red-600">{customError}</p>
            )}
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

      {/* Smart Timeline */}
      <SmartTimeline
        entityId={selectedEntityId}
        indexType={effectiveIndex}
        selectedDate={selectedDate ? selectedDate.toISOString().split('T')[0] : null}
        onDateSelect={handleDateSelect}
      />

      {/* Unified analysis table */}
      {hasResults && (
        <Card padding="md">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-800">{t('analyticsPage.analysisResults')}</h3>
              <button
                onClick={loadResults}
                disabled={loadingResults}
                className="p-1 text-slate-400 hover:text-emerald-600"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loadingResults ? 'animate-spin' : ''}`} />
              </button>
            </div>
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="min-w-full text-sm">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    <th className="px-3 py-2 text-left">{t('analyticsPage.index')}</th>
                    <th className="px-3 py-2 text-right">{t('analytics.mean')}</th>
                    <th className="px-3 py-2 text-right">{t('analytics.min')}</th>
                    <th className="px-3 py-2 text-right">{t('analytics.max')}</th>
                    <th className="px-3 py-2 text-right">{t('analytics.stdDev')}</th>
                    <th className="px-3 py-2 text-center">{t('analyticsPage.viewMap')}</th>
                    <th className="px-3 py-2 text-center">{t('common.export')}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(indexResults).map(([key, result]) => (
                    <React.Fragment key={key}>
                      <tr
                        className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer"
                        onClick={() => setExpandedIndexKey(prev => (prev === key ? null : key))}
                      >
                        <td className="px-3 py-2 font-medium text-slate-800">
                          {getIndexDisplayName(key, result)}
                          {result?.is_custom ? <span className="ml-1 text-xs text-emerald-600">*</span> : null}
                        </td>
                        <td className="px-3 py-2 text-right">{result.statistics.mean != null ? result.statistics.mean.toFixed(3) : '-'}</td>
                        <td className="px-3 py-2 text-right">{result.statistics.min != null ? result.statistics.min.toFixed(3) : '-'}</td>
                        <td className="px-3 py-2 text-right">{result.statistics.max != null ? result.statistics.max.toFixed(3) : '-'}</td>
                        <td className="px-3 py-2 text-right">{result.statistics.std_dev != null ? result.statistics.std_dev.toFixed(3) : '-'}</td>
                        <td className="px-3 py-2 text-center">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              const mapKey = result.index_key || key;
                              setSelectedIndex(mapKey);
                              setActiveJobId(result.job_id);
                              setActiveRasterPath(result.raster_path ?? null);
                              if (result.scene_id) {
                                setSelectedSceneId(result.scene_id);
                                if (result.sensing_date) {
                                  setSelectedDate(new Date(result.sensing_date));
                                }
                              }
                            }}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded border border-slate-200 text-slate-600 hover:bg-slate-100"
                          >
                            <Map className="w-3 h-3" />
                            {t('analyticsPage.viewMap')}
                          </button>
                        </td>
                        <td className="px-3 py-2 text-center">
                          {result.index_type === 'VRA_ZONES' ? (
                            <div className="relative inline-flex">
                              <select
                                onChange={(e) => {
                                  e.stopPropagation();
                                  const fmt = e.target.value as 'geojson' | 'shapefile' | 'csv';
                                  if (fmt) handleExportResult(fmt);
                                  e.target.value = '';
                                }}
                                disabled={exportingFormat !== null}
                                className="text-xs border border-slate-200 rounded px-1 py-1 text-slate-500 bg-white cursor-pointer disabled:opacity-50"
                                defaultValue=""
                              >
                                <option value="" disabled>{t('prescription.exportFormats')}</option>
                                <option value="geojson">{t('prescription.geojson')}</option>
                                <option value="shapefile">{t('prescription.shapefile')}</option>
                                <option value="csv">{t('prescription.csv')}</option>
                              </select>
                            </div>
                          ) : (
                            <span className="text-xs text-slate-300">—</span>
                          )}
                        </td>
                      </tr>
                      {expandedIndexKey === key && selectedEntityId && (
                        <tr className="border-t border-slate-100 bg-slate-50">
                          <td className="px-3 py-3" colSpan={7}>
                            <div className="flex items-center gap-2 mb-2 text-slate-600">
                              <ChevronDown className="w-4 h-4" />
                              <span className="text-xs">{t('analyticsPage.historicEvolution')}</span>
                            </div>
                            {result.is_custom ? (
                              <div className="p-3 text-xs text-slate-500 bg-white rounded-lg border border-slate-200">
                                {t('analyticsPage.customTimeseriesNotAvailable')}
                              </div>
                            ) : (
                              <div className="h-56 bg-white rounded-lg border border-slate-200">
                                <TimeseriesChart
                                  entityId={selectedEntityId}
                                  indexType={(result.index_type || key) as any}
                                />
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
      )}

      {/* Empty state — no results and not analyzing */}
      {!hasResults && !isAnalyzing && !loadingResults && (
        <Card padding="md">
          <div className="text-center py-8">
            <Leaf className="w-12 h-12 text-slate-200 mx-auto mb-3" />
            <h3 className="text-lg font-medium text-slate-600 mb-1">{t('analytics.noScenes')}</h3>
            <p className="text-sm text-slate-400 max-w-md mx-auto mb-4">
              {t('analyticsPage.inactiveMonitoringDesc')}
            </p>
            {!subscription && (
              <button
                onClick={() => setShowSetupWizard(true)}
                className="inline-flex items-center gap-2 px-5 py-2.5 bg-emerald-600 text-white rounded-lg text-sm font-semibold hover:bg-emerald-700 transition-colors shadow-sm"
              >
                <Activity className="w-4 h-4" />
                {t('analyticsPage.configureMonitoring')}
              </button>
            )}
          </div>
        </Card>
      )}

      {/* Calculation History — flat table */}
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
          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="min-w-full text-xs">
              <thead className="bg-slate-50 text-slate-500 uppercase tracking-wider">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">{t('analyticsPage.date')}</th>
                  <th className="px-3 py-2 text-left font-medium">{t('analyticsPage.index')}</th>
                  <th className="px-3 py-2 text-right font-medium">{t('analytics.mean')}</th>
                  <th className="px-3 py-2 text-right font-medium">{t('timeline.clouds')}</th>
                  <th className="px-3 py-2 text-center font-medium">{t('analyticsPage.status')}</th>
                </tr>
              </thead>
              <tbody>
                {[...(Array.isArray(jobs) ? jobs : [])]
                  .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
                  .map((job) => {
                    const statusClass = job.status === 'completed'
                      ? 'text-emerald-600 bg-emerald-50'
                      : job.status === 'failed'
                        ? 'text-red-600 bg-red-50'
                        : 'text-amber-600 bg-amber-50';

                    const statusLabel = job.status === 'completed'
                      ? t('calculations.status.completed')
                      : job.status === 'failed'
                        ? t('calculations.status.failed')
                        : job.status === 'running'
                          ? t('calculations.status.running')
                          : t('calculations.status.pending');

                    return (
                      <tr
                        key={job.id}
                        className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer transition-colors"
                        onClick={() => {
                          // Select scene + index in the viewer
                          const idxType = job.index_type || job.result?.index_type;
                          if (idxType && job.scene_id) {
                            setSelectedIndex(idxType);
                            setSelectedSceneId(job.scene_id);
                            if (job.completed_at) {
                              setSelectedDate(new Date(job.completed_at));
                            }
                            if (job.result?.raster_path) {
                              setActiveRasterPath(job.result.raster_path);
                            }
                            setActiveJobId(job.id);
                          }
                        }}
                      >
                        <td className="px-3 py-2 text-slate-700 whitespace-nowrap">
                          {new Date(job.created_at).toLocaleDateString('es-ES', {
                            day: '2-digit', month: 'short', year: 'numeric',
                          })}
                        </td>
                        <td className="px-3 py-2 font-medium text-slate-800">
                          {job.index_type || job.result?.index_type || '-'}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-600 font-mono">
                          {job.result_stats?.mean != null ? job.result_stats.mean.toFixed(3) : '-'}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-400">
                          {job.result?.cloud_coverage_pct != null
                            ? `${Number(job.result.cloud_coverage_pct).toFixed(0)}%`
                            : '-'
                          }
                        </td>
                        <td className="px-3 py-2 text-center">
                          <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-medium ${statusClass}`}>
                            {statusLabel}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

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
