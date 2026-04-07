/**
 * ExportTab - Combined Prescription exports + Alerts configuration.
 *
 * Consolidates the former PrescriptionTab and AlertsTab into a single tab.
 */

import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { AlertConfig, VegetationIndexType, ModuleCapabilities } from '../../types';
import {
  FileDown, Map, Bell, Send, Code, AlertTriangle,
  ChevronDown, ChevronUp,
} from 'lucide-react';

// ─── Alerts Section (inline) ────────────────────────────────────────────────

const INDEX_OPTIONS: { value: VegetationIndexType; label: string }[] = [
  { value: 'NDVI', label: 'NDVI - Vigor Vegetativo' },
  { value: 'NDMI', label: 'NDMI - Humedad' },
  { value: 'NDRE', label: 'NDRE - Clorofila' },
  { value: 'EVI', label: 'EVI - Vegetación Mejorado' },
  { value: 'SAVI', label: 'SAVI - Suelo Ajustado' },
];

const AlertsSection: React.FC<{ entityId: string | null }> = ({ entityId }) => {
  const { t } = useTranslation();
  const api = useVegetationApi();

  const [config, setConfig] = useState<Partial<AlertConfig>>({
    entity_id: entityId || undefined,
    index_type: 'NDVI',
    threshold_low: 0.3,
    threshold_high: undefined,
    webhook_url: '',
    enabled: true,
  });
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [formatExample, setFormatExample] = useState<any | null>(null);
  const [capabilities, setCapabilities] = useState<ModuleCapabilities | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    api.getCapabilities().then(setCapabilities).catch(() => {});
  }, [api]);

  useEffect(() => {
    setConfig(prev => ({ ...prev, entity_id: entityId || undefined }));
  }, [entityId]);

  const n8nAvailable = capabilities?.n8n_available ?? false;

  const handleSave = async () => {
    setIsSaving(true);
    setMessage(null);
    try {
      await api.configureAlerts(config as AlertConfig);
      setMessage({ type: 'success', text: t('alerts.saved', 'Configuración guardada') });
    } catch {
      setMessage({ type: 'error', text: t('alerts.saveError', 'Error al guardar') });
    } finally {
      setIsSaving(false);
    }
  };

  const handleTest = async () => {
    if (!entityId) return;
    setIsTesting(true);
    setTestResult(null);
    try {
      const result = await api.testAlert(entityId);
      setTestResult(result.preview || result.message);
      setMessage({
        type: result.would_trigger ? 'info' : 'success',
        text: result.would_trigger
          ? t('alerts.wouldTrigger', 'Se activaría con los valores actuales')
          : t('alerts.wouldNotTrigger', 'No se activaría con los valores actuales'),
      });
    } catch {
      setMessage({ type: 'error', text: t('alerts.testError', 'Error al probar') });
    } finally {
      setIsTesting(false);
    }
  };

  const handleShowFormat = async () => {
    if (!entityId) return;
    try {
      const format = await api.getAlertFormat(entityId);
      setFormatExample(format.example_payload);
    } catch {
      setFormatExample({
        event: 'vegetation_alert',
        entity_id: entityId,
        index_type: config.index_type,
        current_value: 0.28,
        threshold: config.threshold_low,
        severity: 'warning',
        timestamp: new Date().toISOString(),
      });
    }
  };

  return (
    <section className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      {/* Collapsible header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-5 hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Bell className="w-5 h-5 text-amber-500" />
          <h2 className="text-lg font-semibold text-slate-800">
            {t('export.alertsSection', 'Configurar Alertas')}
          </h2>
        </div>
        {expanded ? <ChevronUp className="w-5 h-5 text-slate-400" /> : <ChevronDown className="w-5 h-5 text-slate-400" />}
      </button>

      {expanded && (
        <div className="px-6 pb-6 space-y-4 border-t border-slate-100 pt-4">
          {message && (
            <div className={`p-3 rounded-lg text-sm ${
              message.type === 'success' ? 'bg-emerald-50 text-emerald-800' :
              message.type === 'error' ? 'bg-red-50 text-red-800' :
              'bg-blue-50 text-blue-800'
            }`}>
              {message.text}
            </div>
          )}

          {/* Index + Thresholds */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                {t('alerts.indexType', 'Índice')}
              </label>
              <select
                value={config.index_type}
                onChange={(e) => setConfig(prev => ({ ...prev, index_type: e.target.value as VegetationIndexType }))}
                className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
              >
                {INDEX_OPTIONS.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                {t('alerts.thresholdLow', 'Umbral bajo')}
              </label>
              <input
                type="number" step="0.01" min="0" max="1"
                value={config.threshold_low || ''}
                onChange={(e) => setConfig(prev => ({ ...prev, threshold_low: e.target.value ? parseFloat(e.target.value) : undefined }))}
                placeholder="0.3"
                className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                {t('alerts.thresholdHigh', 'Umbral alto')}
              </label>
              <input
                type="number" step="0.01" min="0" max="1"
                value={config.threshold_high || ''}
                onChange={(e) => setConfig(prev => ({ ...prev, threshold_high: e.target.value ? parseFloat(e.target.value) : undefined }))}
                placeholder={t('alerts.optional', 'Opcional')}
                className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
              />
            </div>
          </div>

          {/* Enable + Save */}
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={config.enabled}
                onChange={(e) => setConfig(prev => ({ ...prev, enabled: e.target.checked }))}
                className="rounded border-slate-300 text-emerald-600"
              />
              {t('alerts.enabled', 'Activa')}
            </label>
            <button
              onClick={handleSave}
              disabled={isSaving}
              className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-700 disabled:opacity-50"
            >
              {isSaving ? t('common.saving', 'Guardando...') : t('common.save', 'Guardar')}
            </button>
          </div>

          {/* Test */}
          <div className="flex gap-2 pt-2 border-t border-slate-100">
            <button
              onClick={handleTest}
              disabled={isTesting || !entityId}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-100 text-amber-700 rounded-lg text-xs font-medium hover:bg-amber-200 disabled:opacity-50"
            >
              <Send className="w-3.5 h-3.5" />
              {isTesting ? t('alerts.testing', 'Probando...') : t('alerts.test', 'Probar')}
            </button>
            <button
              onClick={handleShowFormat}
              disabled={!entityId}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 text-slate-600 rounded-lg text-xs font-medium hover:bg-slate-200 disabled:opacity-50"
            >
              <Code className="w-3.5 h-3.5" />
              N8N Format
            </button>
          </div>

          {testResult && (
            <div className="p-3 bg-slate-50 rounded-lg text-sm text-slate-600">{testResult}</div>
          )}
          {formatExample && (
            <pre className="p-3 bg-slate-900 rounded-lg text-xs text-emerald-400 overflow-x-auto">
              {JSON.stringify(formatExample, null, 2)}
            </pre>
          )}

          {!n8nAvailable && (
            <div className="flex items-start gap-2 p-3 bg-amber-50 rounded-lg text-sm text-amber-700">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>{t('alerts.n8nUnavailable', 'N8N no configurado. Las alertas se guardan pero no se envían.')}</span>
            </div>
          )}
        </div>
      )}
    </section>
  );
};

// ─── Main ExportTab ─────────────────────────────────────────────────────────

const ExportTab: React.FC = () => {
  const { t } = useTranslation();
  const { selectedEntityId } = useVegetationContext();
  const api = useVegetationApi();

  const [isExporting, setIsExporting] = useState<string | null>(null);
  const [exportMessage, setExportMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const handleExport = async (format: 'geojson' | 'shapefile' | 'csv') => {
    if (!selectedEntityId) return;
    setIsExporting(format);
    setExportMessage(null);

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

      setExportMessage({ type: 'success', text: t('prescription.exportSuccess', { filename }) });
    } catch {
      setExportMessage({ type: 'error', text: t('prescription.exportError') });
    } finally {
      setIsExporting(null);
    }
  };

  const handleViewOnMap = () => {
    if (!selectedEntityId) return;
    const url = `/entities?selectedEntity=${encodeURIComponent(selectedEntityId)}&activeLayers=vegetation,zoning`;
    if ((window as any).__nekazariNavigate) {
      (window as any).__nekazariNavigate(url);
    } else {
      window.location.href = url;
    }
  };

  if (!selectedEntityId) {
    return (
      <div className="p-6 text-center">
        <div className="text-slate-400 text-lg mb-2">{t('prescription.selectParcel')}</div>
        <p className="text-slate-500 text-sm">{t('prescription.selectParcelHint')}</p>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <header>
        <h1 className="text-xl font-bold text-slate-900">{t('prescription.exportTitle')}</h1>
        <p className="text-slate-600 text-sm mt-1">{t('prescription.exportDesc')}</p>
      </header>

      {exportMessage && (
        <div className={`p-4 rounded-lg ${exportMessage.type === 'success'
          ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
          : 'bg-red-50 text-red-800 border border-red-200'
        }`}>
          {exportMessage.text}
        </div>
      )}

      {/* Export formats */}
      <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
        <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
          <FileDown className="w-5 h-5 text-emerald-600" />
          {t('prescription.downloadFormats')}
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <button
            onClick={() => handleExport('geojson')}
            disabled={isExporting !== null}
            className="flex flex-col items-center p-4 border border-slate-200 rounded-lg hover:bg-slate-50 hover:border-emerald-300 transition-colors disabled:opacity-50"
          >
            <div className="w-12 h-12 bg-emerald-100 rounded-lg flex items-center justify-center mb-3">
              <span className="text-emerald-700 font-bold text-sm">JSON</span>
            </div>
            <span className="font-medium text-slate-800">GeoJSON</span>
            <span className="text-xs text-slate-500 mt-1">{t('prescription.forGis')}</span>
          </button>

          <button
            onClick={() => handleExport('shapefile')}
            disabled={isExporting !== null}
            className="flex flex-col items-center p-4 border border-slate-200 rounded-lg hover:bg-slate-50 hover:border-blue-300 transition-colors disabled:opacity-50"
          >
            <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center mb-3">
              <span className="text-blue-700 font-bold text-sm">SHP</span>
            </div>
            <span className="font-medium text-slate-800">Shapefile</span>
            <span className="text-xs text-slate-500 mt-1">{t('prescription.gisStandard')}</span>
          </button>

          <button
            onClick={() => handleExport('csv')}
            disabled={isExporting !== null}
            className="flex flex-col items-center p-4 border border-slate-200 rounded-lg hover:bg-slate-50 hover:border-amber-300 transition-colors disabled:opacity-50"
          >
            <div className="w-12 h-12 bg-amber-100 rounded-lg flex items-center justify-center mb-3">
              <span className="text-amber-700 font-bold text-sm">CSV</span>
            </div>
            <span className="font-medium text-slate-800">CSV</span>
            <span className="text-xs text-slate-500 mt-1">{t('prescription.tabularData')}</span>
          </button>
        </div>
      </section>

      {/* View on map */}
      <section className="bg-slate-50 rounded-xl border border-slate-200 p-4">
        <button
          onClick={handleViewOnMap}
          className="w-full flex items-center justify-center gap-2 py-3 text-emerald-700 hover:text-emerald-800 font-medium transition-colors"
        >
          <Map className="w-5 h-5" />
          {t('prescription.viewOnMap')}
        </button>
      </section>

      {/* Alerts (collapsible) */}
      <AlertsSection entityId={selectedEntityId} />
    </div>
  );
};

export default ExportTab;
