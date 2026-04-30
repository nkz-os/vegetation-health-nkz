/**
 * Vegetation Layer Control - Simplified context-panel slot component.
 * Displays: header, opacity, crop season, quick stats, monitoring indicator, action buttons.
 */

import React, { useEffect, useState } from 'react';
import { useTranslation, useViewer } from '@nekazari/sdk';
import { useUIKit } from '../../hooks/useUIKit';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';

const VegetationLayerControl: React.FC = () => {
  const { t } = useTranslation();
  const { Card } = useUIKit();
  const { setCurrentDate } = useViewer();

  const {
    selectedIndex,
    selectedDate,
    selectedEntityId,
    indexResults,
    layerOpacity,
    setSelectedIndex,
    setLayerOpacity,
    setIndexResults,
  } = useVegetationContext();

  const api = useVegetationApi();
  const [analyzing, setAnalyzing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [ctrlError, setCtrlError] = useState<string | null>(null);
  const [pollTimer, setPollTimer] = useState<ReturnType<typeof setInterval> | null>(null);
  const [cropSeason, setCropSeason] = useState<any>(null);
  const [loadingSeason, setLoadingSeason] = useState(false);
  const [monitoringActive, setMonitoringActive] = useState(false);
  const [zoningBusy, setZoningBusy] = useState(false);

  const opacity = layerOpacity;
  const setOpacity = setLayerOpacity;

  const handleAnalyze = async () => {
    if (!selectedEntityId) return;
    setAnalyzing(true);
    setCtrlError(null);
    try {
      const result = await api.analyzeParcel({ entity_id: selectedEntityId });
      const jobId = result.job_id;
      const timer = setInterval(async () => {
        try {
          const data = await api.getJobDetails(jobId);
          if (data?.job?.status === 'failed') {
            clearInterval(timer);
            setPollTimer(null);
            setCtrlError(data.job.error_message || 'Job failed');
            setTimeout(() => setCtrlError(null), 5000);
            setAnalyzing(false);
            return;
          }
          const results = await api.getEntityResults(selectedEntityId);
          if (results.active_jobs === 0 && results.has_results) {
            clearInterval(timer);
            setPollTimer(null);
            setIndexResults(results.indices || {});
            if (results.indices && results.indices['NDVI']) {
              setSelectedIndex('NDVI');
            }
            setAnalyzing(false);
          }
        } catch {
          // transient — keep polling
        }
      }, 3000);
      setPollTimer(timer);
    } catch (err: any) {
      setCtrlError(err?.response?.data?.detail || err?.message || 'Error');
      setTimeout(() => setCtrlError(null), 5000);
      setAnalyzing(false);
    }
  };

  const handleVraZoning = async () => {
    if (!selectedEntityId) return;
    if (indexResults['VRA_ZONES']) {
      setSelectedIndex('VRA_ZONES');
      return;
    }
    setZoningBusy(true);
    setCtrlError(null);
    try {
      const result = await api.calculateIndex({
        entity_id: selectedEntityId,
        index_type: 'VRA_ZONES' as any,
      });
      const timer = setInterval(async () => {
        try {
          const details = await api.getJobDetails(result.job_id);
          if (details?.job?.status === 'completed') {
            clearInterval(timer);
            setZoningBusy(false);
            const data = await api.getEntityResults(selectedEntityId);
            if (data.indices) {
              setIndexResults(data.indices);
            }
            setSelectedIndex('VRA_ZONES');
          } else if (details?.job?.status === 'failed') {
            clearInterval(timer);
            setZoningBusy(false);
            setCtrlError(t('zoning.failed', 'Zonificación fallida'));
            setTimeout(() => setCtrlError(null), 5000);
          }
        } catch { /* keep polling */ }
      }, 3000);
    } catch (err: any) {
      setZoningBusy(false);
      setCtrlError(err?.message || String(err));
      setTimeout(() => setCtrlError(null), 5000);
    }
  };

  const handleExport = async () => {
    if (!selectedEntityId) return;
    if (!indexResults['VRA_ZONES']) {
      setCtrlError(t('prescription.noVraData', 'No hay datos VRA para exportar'));
      setTimeout(() => setCtrlError(null), 5000);
      return;
    }
    setExporting(true);
    setCtrlError(null);
    try {
      const blob = await api.exportPrescriptionGeojson(selectedEntityId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `prescription_${selectedEntityId.split(':').pop()}.geojson`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setCtrlError(err?.response?.data?.detail || err?.message || 'Error');
      setTimeout(() => setCtrlError(null), 5000);
    } finally {
      setExporting(false);
    }
  };

  // Current index statistics for quick display
  const activeStats = selectedIndex && indexResults[selectedIndex]
    ? indexResults[selectedIndex].statistics
    : null;

  // Sync viewer date — compare by timestamp to avoid infinite re-render loop
  const lastSyncedDateRef = React.useRef<number>(0);
  useEffect(() => {
    if (!selectedDate || !setCurrentDate) return;
    const ts = selectedDate.getTime();
    if (ts === lastSyncedDateRef.current) return;
    lastSyncedDateRef.current = ts;
    setCurrentDate(selectedDate);
  }, [selectedDate, setCurrentDate]);

  // Cleanup poll timer on unmount
  useEffect(() => {
    return () => {
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [pollTimer]);

  // Fetch crop seasons and subscription status
  useEffect(() => {
    if (!selectedEntityId) { setCropSeason(null); return; }
    let cancelled = false;
    setLoadingSeason(true);
    api.listCropSeasons(selectedEntityId)
      .then(seasons => {
        if (cancelled) return;
        const arr = Array.isArray(seasons) ? seasons : [];
        const active = arr.find((s: any) => s.is_active) || arr[0] || null;
        setCropSeason(active);
      })
      .catch(() => setCropSeason(null))
      .finally(() => { if (!cancelled) setLoadingSeason(false); });
    api.getSubscriptionForEntity(selectedEntityId)
      .then((sub: any) => {
        if (cancelled) return;
        setMonitoringActive(!!sub?.is_active);
      })
      .catch(() => { if (!cancelled) setMonitoringActive(false); });
    return () => { cancelled = true; };
  }, [selectedEntityId, api]);

  if (!selectedEntityId) {
    return (
      <Card padding="md" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl w-full">
        <div className="flex items-center justify-center gap-2 py-4 text-slate-500">
          <p>{t('layerControl.selectParcel', 'Selecciona una parcela para ver capas')}</p>
        </div>
      </Card>
    );
  }

  return (
    <Card padding="md" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl w-full max-w-[320px] shadow-lg pointer-events-auto">
      <div className="space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 pb-2">
          <h3 className="font-semibold text-slate-800 flex items-center gap-2">
            🌿 {t('layerControl.header', 'Vegetación')}
          </h3>
          <span className="text-xs text-slate-500 font-mono">
            {selectedEntityId.split(':').pop()}
          </span>
        </div>

        {/* Opacity */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-slate-600">
            <span>{t('layerControl.opacity', 'Opacidad')}</span>
            <span>{opacity}%</span>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            value={opacity}
            onChange={(e) => setOpacity(parseInt(e.target.value))}
            className="w-full h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-green-600"
          />
        </div>

        {/* Crop Season */}
        <div className="space-y-2 pt-2 border-t border-slate-100">
          <label className="text-xs font-medium text-slate-600 uppercase tracking-wider">
            {t('cropSeason.title', 'Campaña')}
          </label>
          {loadingSeason ? (
            <p className="text-sm text-slate-400">...</p>
          ) : cropSeason ? (
            <p className="text-sm text-slate-700">
              {t(`cropSeason.${cropSeason.crop_type}`, cropSeason.crop_type)}
              {' '}{cropSeason.start_date} – {cropSeason.end_date || '...'}
            </p>
          ) : (
            <p className="text-sm text-slate-500">
              {t('layerControl.noCropSeason', 'Sin campaña asignada')}
            </p>
          )}
        </div>

        {/* Quick Stats */}
        {activeStats && Object.keys(indexResults).length > 0 && (
          <div className="space-y-2 pt-2 border-t border-slate-100">
            <label className="text-xs font-medium text-slate-600 uppercase tracking-wider">
              {t('analyticsPage.quickStats', 'Estadísticas rápidas')}
            </label>
            <div className="grid grid-cols-3 gap-2">
              <div className="bg-slate-50 rounded-lg p-2 text-center">
                <div className="text-xs text-slate-500">{t('analytics.mean', 'Media')}</div>
                <div className="text-sm font-semibold text-slate-800">
                  {activeStats.mean?.toFixed(3) ?? '—'}
                </div>
              </div>
              <div className="bg-slate-50 rounded-lg p-2 text-center">
                <div className="text-xs text-slate-500">{t('analytics.min', 'Mínimo')}</div>
                <div className="text-sm font-semibold text-slate-800">
                  {activeStats.min?.toFixed(3) ?? '—'}
                </div>
              </div>
              <div className="bg-slate-50 rounded-lg p-2 text-center">
                <div className="text-xs text-slate-500">{t('analytics.max', 'Máximo')}</div>
                <div className="text-sm font-semibold text-slate-800">
                  {activeStats.max?.toFixed(3) ?? '—'}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Monitoring Indicator */}
        <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
          <span className={`w-2.5 h-2.5 rounded-full ${monitoringActive ? 'bg-emerald-500' : 'bg-slate-300'}`} />
          <span className="text-sm text-slate-500">
            {monitoringActive
              ? t('layerControl.active', 'Activa')
              : t('layerControl.inactive', 'Inactiva')}
          </span>
        </div>

        {/* Action Buttons */}
        <div className="flex gap-2 pt-2 border-t border-slate-100">
          <button
            className="flex-1 px-3 py-2 text-sm font-medium text-white bg-green-600 hover:bg-green-700 rounded-lg transition-colors disabled:opacity-50"
            onClick={handleAnalyze}
            disabled={analyzing}
          >
            {analyzing ? '...' : '🔄'} {t('dashboard.analyze', 'Analizar')}
          </button>
          <button
            className="flex-1 px-3 py-2 text-sm font-medium text-green-700 bg-green-50 hover:bg-green-100 rounded-lg transition-colors disabled:opacity-50"
            onClick={handleVraZoning}
            disabled={zoningBusy}
          >
            {zoningBusy ? '...' : '🗺'} {t('layerControl.vra', 'VRA')}
          </button>
          <button
            className="flex-1 px-3 py-2 text-sm font-medium text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors disabled:opacity-50"
            onClick={handleExport}
            disabled={exporting}
          >
            📥 {t('common.export', 'Exportar')}
          </button>
        </div>
        {ctrlError && (
          <p className="text-xs text-red-600">{ctrlError}</p>
        )}
      </div>
    </Card>
  );
};

export default VegetationLayerControl;
