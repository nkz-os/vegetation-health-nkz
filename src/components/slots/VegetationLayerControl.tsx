/**
 * Vegetation Layer Control - Simplified context-panel slot component.
 * Displays: header, opacity, crop season, quick stats, monitoring indicator, action buttons.
 */

import React, { useEffect, useState } from 'react';
import { Leaf, Download, Map, Beaker } from 'lucide-react';
import { SlotShell } from '@nekazari/viewer-kit';
import { Stack, Slider, Button, Badge, Spinner } from '@nekazari/ui-kit';
import { useTranslation, useViewer } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';

const vegetationAccent = { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' };

const VegetationLayerControl: React.FC = () => {
  const { t } = useTranslation();
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
      <SlotShell moduleId="vegetation-prime" accent={vegetationAccent}>
        <div className="flex items-center justify-center gap-nkz-inline py-nkz-section text-nkz-text-muted">
          <p className="text-nkz-sm">{t('layerControl.selectParcel', 'Selecciona una parcela para ver capas')}</p>
        </div>
      </SlotShell>
    );
  }

  return (
    <SlotShell
      title="Vegetación"
      icon={<Leaf className="w-4 h-4" />}
      collapsible
      accent={vegetationAccent}
    >
      <Stack gap="stack">
        {/* Header info bar */}
        <div className="flex items-center justify-between">
          <span className="text-nkz-xs text-nkz-text-muted font-mono">
            {selectedEntityId.split(':').pop()}
          </span>
        </div>

        {/* Opacity */}
        <Slider
          value={opacity}
          onChange={setOpacity}
          min={0}
          max={100}
          step={1}
          label={t('layerControl.opacity', 'Opacidad')}
          unit="%"
        />

        {/* Crop Season */}
        <div className="space-y-nkz-tight pt-nkz-stack border-t border-nkz-border">
          <label className="text-nkz-xs font-semibold uppercase tracking-wider text-nkz-text-muted">
            {t('cropSeason.title', 'Campaña')}
          </label>
          {loadingSeason ? (
            <Spinner size="sm" />
          ) : cropSeason ? (
            <p className="text-nkz-sm text-nkz-text-primary">
              {t(`cropSeason.${cropSeason.crop_type}`, cropSeason.crop_type)}
              {' '}{cropSeason.start_date} – {cropSeason.end_date || '...'}
            </p>
          ) : (
            <p className="text-nkz-sm text-nkz-text-muted">
              {t('layerControl.noCropSeason', 'Sin campaña asignada')}
            </p>
          )}
        </div>

        {/* Quick Stats */}
        {activeStats && Object.keys(indexResults).length > 0 && (
          <div className="space-y-nkz-tight pt-nkz-stack border-t border-nkz-border">
            <label className="text-nkz-xs font-semibold uppercase tracking-wider text-nkz-text-muted">
              {t('analyticsPage.quickStats', 'Estadísticas rápidas')}
            </label>
            <div className="grid grid-cols-3 gap-nkz-inline">
              <div className="bg-nkz-surface-sunken rounded-nkz-md p-nkz-inline text-center">
                <div className="text-nkz-xs text-nkz-text-muted">{t('analytics.mean', 'Media')}</div>
                <div className="text-nkz-sm font-semibold text-nkz-text-primary">
                  {activeStats.mean?.toFixed(3) ?? '—'}
                </div>
              </div>
              <div className="bg-nkz-surface-sunken rounded-nkz-md p-nkz-inline text-center">
                <div className="text-nkz-xs text-nkz-text-muted">{t('analytics.min', 'Mínimo')}</div>
                <div className="text-nkz-sm font-semibold text-nkz-text-primary">
                  {activeStats.min?.toFixed(3) ?? '—'}
                </div>
              </div>
              <div className="bg-nkz-surface-sunken rounded-nkz-md p-nkz-inline text-center">
                <div className="text-nkz-xs text-nkz-text-muted">{t('analytics.max', 'Máximo')}</div>
                <div className="text-nkz-sm font-semibold text-nkz-text-primary">
                  {activeStats.max?.toFixed(3) ?? '—'}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Monitoring Indicator */}
        <div className="pt-nkz-stack border-t border-nkz-border">
          <Badge intent={monitoringActive ? 'positive' : 'default'}>
            {monitoringActive
              ? t('layerControl.active', 'Activa')
              : t('layerControl.inactive', 'Inactiva')}
          </Badge>
        </div>

        {/* Action Buttons */}
        <div className="flex gap-nkz-inline pt-nkz-stack border-t border-nkz-border">
          <Button
            variant="primary"
            size="sm"
            onClick={handleAnalyze}
            disabled={analyzing}
            leadingIcon={analyzing ? <Spinner size="sm" /> : <Beaker className="w-4 h-4" />}
          >
            {t('dashboard.analyze', 'Analizar')}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleVraZoning}
            disabled={zoningBusy}
            leadingIcon={zoningBusy ? <Spinner size="sm" /> : <Map className="w-4 h-4" />}
          >
            {t('layerControl.vra', 'VRA')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleExport}
            disabled={exporting}
            leadingIcon={<Download className="w-4 h-4" />}
          >
            {t('common.export', 'Exportar')}
          </Button>
        </div>

        {ctrlError && (
          <Badge intent="negative" className="flex items-center gap-nkz-tight">
            <span className="text-nkz-xs">{ctrlError}</span>
          </Badge>
        )}
      </Stack>
    </SlotShell>
  );
};

export default VegetationLayerControl;
