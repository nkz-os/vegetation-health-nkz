/**
 * Vegetation Layer Control — context-panel slot component.
 *
 * Phase 2.3 rewrite: data-aware panel that shows entity status immediately
 * on selection, with progressive disclosure of analysis controls.
 */
import React, { useEffect, useState, useMemo } from 'react';
import { Leaf, Download, Map, Beaker, Satellite, X } from 'lucide-react';
import { SlotShell } from '@nekazari/viewer-kit';
import { Stack, Slider, Button, Badge, Spinner } from '@nekazari/ui-kit';
import { useTranslation, useViewer } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { useJobPolling } from '../../hooks/useJobPolling';
import { SetupWizard } from '../pages/SetupWizard';
import { IndexPillSelector, type CustomIndexOption } from '../widgets/IndexPillSelector';

const vegetationAccent = { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' };

const VegetationLayerControl: React.FC = () => {
  const { t } = useTranslation();
  const { setCurrentDate } = useViewer();

  const {
    selectedIndex,
    selectedDate,
    selectedEntityId,
    selectedSceneId,
    indexResults,
    entityDataStatus,
    entityDataStatusLoading,
    entityName,
    layerOpacity,
    activeJobId,
    activeRasterPath,
    setSelectedIndex,
    setLayerOpacity,
    setIndexResults,
    setSelectedEntityId,
  } = useVegetationContext();

  const api = useVegetationApi();
  const {
    startAnalysis,
    cancelAnalysis,
    isAnalyzing,
    analysisError,
    analysisProgress,
    usageToday,
    usageLimit,
  } = useJobPolling();

  const [showSetupWizard, setShowSetupWizard] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [ctrlError, setCtrlError] = useState<string | null>(null);
  const [zoningBusy, setZoningBusy] = useState(false);

  const opacity = layerOpacity;
  const setOpacity = setLayerOpacity;
  const hasLayer = !!(activeJobId || activeRasterPath);

  // Derive custom index options from indexResults for pill selector
  const customIndexOptions: CustomIndexOption[] = useMemo(() => {
    return Object.values(indexResults)
      .filter((r: any) => r.is_custom && r.formula_id)
      .map((r: any) => ({
        key: `custom:${r.formula_id}`,
        label: r.formula_name || r.index_type,
      }));
  }, [indexResults]);

  // Current index statistics for quick display
  const activeStats = selectedIndex && indexResults[selectedIndex]
    ? indexResults[selectedIndex].statistics
    : null;

  // Sync viewer date
  const lastSyncedDateRef = React.useRef<number>(0);
  useEffect(() => {
    if (!selectedDate || !setCurrentDate) return;
    const ts = selectedDate.getTime();
    if (ts === lastSyncedDateRef.current) return;
    lastSyncedDateRef.current = ts;
    setCurrentDate(selectedDate);
  }, [selectedDate, setCurrentDate]);

  // Re-fetch results scoped to selected scene (Phase 3.2)
  useEffect(() => {
    if (!selectedEntityId || !selectedSceneId) return;
    api.getEntityResults(selectedEntityId, { sceneId: selectedSceneId })
      .then(data => {
        if (data.indices && Object.keys(data.indices).length > 0) {
          setIndexResults(data.indices);
        }
      })
      .catch(() => { /* scene may lack results for some indices */ });
  }, [selectedEntityId, selectedSceneId]);

  // --- Action handlers ---

  const handleAnalyze = async () => {
    if (!selectedEntityId) return;
    setCtrlError(null);
    await startAnalysis();
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
            if (data.indices) setIndexResults(data.indices);
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

  const handleSetupComplete = () => {
    setShowSetupWizard(false);
    // Re-trigger data-status load by briefly toggling entity
    if (selectedEntityId) {
      api.getEntityDataStatus(selectedEntityId).then(() => { /* context will update */ });
    }
  };

  // Display name: prefer entityName from data-status, fall back to URN fragment
  const displayName = entityName || (selectedEntityId ? selectedEntityId.split(':').pop() : '');

  const hasResults = Object.keys(indexResults).length > 0;
  const hasAnyData = entityDataStatus?.has_any_data || hasResults;
  const cropSeasons = entityDataStatus?.active_crop_seasons || [];
  const hasCropSeason = cropSeasons.length > 0;

  // ==========================================================================
  // Render: No entity selected
  // ==========================================================================
  if (!selectedEntityId) {
    return (
      <SlotShell moduleId="vegetation-prime" accent={vegetationAccent}>
        <div className="flex items-center justify-center gap-nkz-inline py-nkz-section text-nkz-text-muted">
          <Leaf className="w-5 h-5 opacity-50" />
          <p className="text-nkz-sm">{t('layerControl.selectParcel', 'Selecciona una parcela para ver capas')}</p>
        </div>
      </SlotShell>
    );
  }

  // ==========================================================================
  // Render: Loading data-status
  // ==========================================================================
  if (entityDataStatusLoading && !entityDataStatus) {
    return (
      <SlotShell moduleId="vegetation-prime" title="Vegetación" icon={<Leaf className="w-4 h-4" />} collapsible accent={vegetationAccent}>
        <div className="flex items-center justify-center py-nkz-section">
          <Spinner size="sm" />
        </div>
      </SlotShell>
    );
  }

  // ==========================================================================
  // Render: Entity selected — show data-aware panel
  // ==========================================================================
  return (
    <SlotShell
      moduleId="vegetation-prime"
      title="Vegetación"
      icon={<Leaf className="w-4 h-4" />}
      collapsible
      accent={vegetationAccent}
    >
      <Stack gap="stack">
        {/* Header: entity name */}
        <div className="flex items-center justify-between">
          <span className="text-nkz-sm font-medium text-nkz-text-primary truncate" title={displayName}>
            {displayName}
          </span>
          <button
            onClick={() => setSelectedEntityId(null)}
            className="text-nkz-xs text-nkz-text-muted hover:text-nkz-text-primary"
            title={t('analyticsPage.changeParcel', 'Cambiar parcela')}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Index selector with availability */}
        <div className="space-y-nkz-tight">
          <label className="text-nkz-xs font-semibold uppercase tracking-wider text-nkz-text-muted">
            {t('layerControl.spectralIndex', 'Índice espectral')}
          </label>
          <IndexPillSelector
            selectedIndex={selectedIndex || 'NDVI'}
            onIndexChange={(idx) => setSelectedIndex(idx)}
            customIndexOptions={customIndexOptions}
            availableIndices={entityDataStatus?.available_indices}
          />
        </div>

        {/* Opacity — only when layer is loaded */}
        {hasLayer ? (
          <Slider
            value={opacity}
            onChange={setOpacity}
            min={0}
            max={100}
            step={1}
            label={t('layerControl.opacity', 'Opacidad')}
            unit="%"
          />
        ) : (
          <div className="pt-nkz-stack border-t border-nkz-border">
            <p className="text-nkz-xs text-nkz-text-muted italic">
              {hasAnyData
                ? t('layerControl.selectIndex', 'Selecciona un índice para ver datos')
                : t('layerControl.noLayerLoaded', 'Sin capa cargada — ejecuta un análisis')}
            </p>
          </div>
        )}

        {/* Crop Season */}
        <div className="space-y-nkz-tight pt-nkz-stack border-t border-nkz-border">
          <div className="flex items-center justify-between">
            <label className="text-nkz-xs font-semibold uppercase tracking-wider text-nkz-text-muted">
              {t('cropSeason.title', 'Campaña')}
            </label>
            <button
              onClick={() => setShowSetupWizard(true)}
              className="text-nkz-xs text-nkz-accent-base hover:text-nkz-accent-strong font-medium"
            >
              + {t('cropSeason.newSeason', 'Nueva')}
            </button>
          </div>
          {hasCropSeason ? (
            <p className="text-nkz-sm text-nkz-text-primary">
              {t(`cropSeason.${cropSeasons[0].crop_type}`, cropSeasons[0].crop_type)}
              {' '}{cropSeasons[0].start_date} – {cropSeasons[0].end_date || '...'}
            </p>
          ) : (
            <p className="text-nkz-sm text-nkz-text-muted">
              {t('layerControl.noCropSeason', 'Sin campaña asignada')}
            </p>
          )}
        </div>

        {/* Data summary — shown when entity has data but no results loaded yet */}
        {!hasResults && entityDataStatus?.has_any_data && (
          <div className="space-y-nkz-tight pt-nkz-stack border-t border-nkz-border">
            <label className="text-nkz-xs font-semibold uppercase tracking-wider text-nkz-text-muted">
              {t('layerControl.dataAvailable', 'Datos disponibles')}
            </label>
            <div className="grid grid-cols-2 gap-nkz-inline">
              {entityDataStatus.latest_ndvi != null && (
                <div className="bg-nkz-surface-sunken rounded-nkz-md p-nkz-inline text-center">
                  <div className="text-nkz-xs text-nkz-text-muted">NDVI</div>
                  <div className="text-nkz-sm font-semibold text-nkz-text-primary">
                    {entityDataStatus.latest_ndvi.toFixed(3)}
                  </div>
                </div>
              )}
              <div className="bg-nkz-surface-sunken rounded-nkz-md p-nkz-inline text-center">
                <div className="text-nkz-xs text-nkz-text-muted">{t('layerControl.scenesCount', 'Escenas')}</div>
                <div className="text-nkz-sm font-semibold text-nkz-text-primary">
                  {entityDataStatus.total_scenes}
                </div>
              </div>
            </div>
            {entityDataStatus.date_range && (
              <p className="text-nkz-xs text-nkz-text-muted text-center">
                {entityDataStatus.date_range.first} – {entityDataStatus.date_range.last}
              </p>
            )}
          </div>
        )}

        {/* Quick Stats — shown when results are loaded */}
        {activeStats && hasResults && (
          <div className="space-y-nkz-tight pt-nkz-stack border-t border-nkz-border">
            <label className="text-nkz-xs font-semibold uppercase tracking-wider text-nkz-text-muted">
              {selectedIndex} — {t('analyticsPage.quickStats', 'Estadísticas')}
            </label>
            <div className="grid grid-cols-3 gap-nkz-inline">
              <div className="bg-nkz-surface-sunken rounded-nkz-md p-nkz-inline text-center">
                <div className="text-nkz-xs text-nkz-text-muted">{t('analytics.mean', 'Media')}</div>
                <div className="text-nkz-sm font-semibold text-nkz-text-primary">
                  {activeStats.mean?.toFixed(3) ?? '—'}
                </div>
              </div>
              <div className="bg-nkz-surface-sunken rounded-nkz-md p-nkz-inline text-center">
                <div className="text-nkz-xs text-nkz-text-muted">{t('analytics.min', 'Mín')}</div>
                <div className="text-nkz-sm font-semibold text-nkz-text-primary">
                  {activeStats.min?.toFixed(3) ?? '—'}
                </div>
              </div>
              <div className="bg-nkz-surface-sunken rounded-nkz-md p-nkz-inline text-center">
                <div className="text-nkz-xs text-nkz-text-muted">{t('analytics.max', 'Máx')}</div>
                <div className="text-nkz-sm font-semibold text-nkz-text-primary">
                  {activeStats.max?.toFixed(3) ?? '—'}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Monitoring + Usage indicators */}
        <div className="flex items-center gap-nkz-inline pt-nkz-stack border-t border-nkz-border">
          <Badge intent={hasCropSeason ? 'positive' : 'default'}>
            {hasCropSeason
              ? t('layerControl.active', 'Activa')
              : t('layerControl.inactive', 'Inactiva')}
          </Badge>
          {usageToday > 0 && (
            <span className="text-nkz-xs text-nkz-text-muted">
              {t('usage.jobsToday', '{{count}}/{{limit}} jobs', { count: usageToday, limit: usageLimit })}
            </span>
          )}
        </div>

        {/* Analysis progress */}
        {isAnalyzing && analysisProgress && (
          <div className="flex items-center gap-nkz-inline text-nkz-xs text-nkz-accent-base bg-nkz-accent-soft rounded-nkz-md p-nkz-inline">
            <Spinner size="sm" />
            <span>{analysisProgress}</span>
          </div>
        )}

        {/* Error display */}
        {(analysisError || ctrlError) && (
          <Badge intent="negative" className="flex items-center gap-nkz-tight">
            <span className="text-nkz-xs">{analysisError || ctrlError}</span>
            <button onClick={() => { setCtrlError(null); }} className="ml-auto text-nkz-xs hover:text-nkz-text-primary">
              <X className="w-3 h-3" />
            </button>
          </Badge>
        )}

        {/* Action Buttons */}
        <div className="flex gap-nkz-inline pt-nkz-stack border-t border-nkz-border">
          {isAnalyzing ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={cancelAnalysis}
              leadingIcon={<X className="w-4 h-4" />}
            >
              {t('layerControl.cancelAnalysis', 'Cancelar')}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              onClick={handleAnalyze}
              leadingIcon={hasResults ? <Satellite className="w-4 h-4" /> : <Beaker className="w-4 h-4" />}
            >
              {hasResults
                ? t('configPanel.forceLatestScene', 'Actualizar')
                : t('configPanel.analyzeFirstTime', 'Analizar')}
            </Button>
          )}
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
            {t('common.export', 'Exp.')}
          </Button>
        </div>
      </Stack>

      {/* Setup Wizard Modal */}
      {showSetupWizard && (
        <SetupWizard
          open={showSetupWizard}
          onClose={() => setShowSetupWizard(false)}
          entityId={selectedEntityId}
          entityName={displayName}
          onComplete={handleSetupComplete}
        />
      )}
    </SlotShell>
  );
};

export default VegetationLayerControl;
