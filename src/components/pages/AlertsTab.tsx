/**
 * Alerts Tab - Ferrari Frontend
 * 
 * Configure vegetation alerts (thresholds, webhooks) and test payloads for N8N.
 * Supports graceful degradation when N8N is not available.
 */

import React, { useState, useEffect } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { AlertConfig, VegetationIndexType, ModuleCapabilities } from '../../types';
import { Bell, Send, Code, AlertTriangle } from 'lucide-react';

const INDEX_OPTIONS: { value: VegetationIndexType; label: string }[] = [
  { value: 'NDVI', label: 'NDVI - Vigor Vegetativo' },
  { value: 'NDMI', label: 'NDMI - Humedad' },
  { value: 'NDRE', label: 'NDRE - Clorofila' },
  { value: 'EVI', label: 'EVI - Vegetación Mejorado' },
  { value: 'SAVI', label: 'SAVI - Suelo Ajustado' },
];

const AlertsTab: React.FC = () => {
  const { selectedEntityId } = useVegetationContext();
  const api = useVegetationApi();
  
  // Form state
  const [config, setConfig] = useState<Partial<AlertConfig>>({
    entity_id: selectedEntityId || undefined,
    index_type: 'NDVI',
    threshold_low: 0.3,
    threshold_high: undefined,
    webhook_url: '',
    enabled: true
  });
  
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [formatExample, setFormatExample] = useState<any | null>(null);
  const [capabilities, setCapabilities] = useState<ModuleCapabilities | null>(null);

  // Load capabilities
  useEffect(() => {
    api.getCapabilities().then(setCapabilities).catch(console.error);
  }, [api]);

  // Update entity_id when selection changes
  useEffect(() => {
    setConfig(prev => ({ ...prev, entity_id: selectedEntityId || undefined }));
  }, [selectedEntityId]);

  const n8nAvailable = capabilities?.n8n_available ?? false;

  const handleSave = async () => {
    setIsSaving(true);
    setMessage(null);
    
    try {
      await api.configureAlerts(config as AlertConfig);
      setMessage({ type: 'success', text: 'Configuración de alertas guardada correctamente' });
    } catch (error) {
      console.error('Failed to save alerts config:', error);
      setMessage({ type: 'error', text: 'Error al guardar la configuración' });
    } finally {
      setIsSaving(false);
    }
  };

  const handleTestAlert = async () => {
    if (!selectedEntityId) {
      setMessage({ type: 'error', text: 'Selecciona una parcela primero' });
      return;
    }
    
    setIsTesting(true);
    setTestResult(null);
    
    try {
      const result = await api.testAlert(selectedEntityId);
      setTestResult(result.preview || result.message);
      setMessage({ 
        type: result.would_trigger ? 'info' : 'success', 
        text: result.would_trigger 
          ? 'Esta alerta SE ACTIVARÍA con los valores actuales' 
          : 'Esta alerta NO se activaría con los valores actuales'
      });
    } catch (error) {
      console.error('Test alert failed:', error);
      setMessage({ type: 'error', text: 'Error al probar la alerta' });
    } finally {
      setIsTesting(false);
    }
  };

  const handleShowFormat = async () => {
    if (!selectedEntityId) {
      setMessage({ type: 'error', text: 'Selecciona una parcela primero' });
      return;
    }
    
    try {
      const format = await api.getAlertFormat(selectedEntityId);
      setFormatExample(format.example_payload);
    } catch (error) {
      console.error('Get format failed:', error);
      // Show a mock format for demonstration
      setFormatExample({
        event: 'vegetation_alert',
        entity_id: selectedEntityId,
        index_type: config.index_type,
        current_value: 0.28,
        threshold: config.threshold_low,
        severity: 'warning',
        timestamp: new Date().toISOString(),
        message: `NDVI por debajo del umbral (${config.threshold_low})`
      });
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <header className="mb-6">
        <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <Bell className="w-6 h-6 text-amber-500" />
          Configuración de Alertas
        </h1>
        <p className="text-slate-600 text-sm mt-1">
          Configura umbrales y webhooks para recibir alertas de vegetación.
        </p>
      </header>

      {/* Message */}
      {message && (
        <div className={`mb-6 p-4 rounded-lg ${
          message.type === 'success' ? 'bg-emerald-50 text-emerald-800 border border-emerald-200' :
          message.type === 'error' ? 'bg-red-50 text-red-800 border border-red-200' :
          'bg-blue-50 text-blue-800 border border-blue-200'
        }`}>
          {message.text}
        </div>
      )}

      {/* Alert Configuration Form */}
      <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
        <h2 className="text-lg font-semibold text-slate-800 mb-4">Configuración de Umbrales</h2>
        
        <div className="space-y-4">
          {/* Entity Selection */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Parcela
            </label>
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={selectedEntityId || 'Todas las parcelas'}
                disabled
                className="flex-1 px-3 py-2 border border-slate-200 rounded-lg bg-slate-50 text-slate-600 text-sm"
              />
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <input
                  type="checkbox"
                  checked={!config.entity_id}
                  onChange={(e) => setConfig(prev => ({ 
                    ...prev, 
                    entity_id: e.target.checked ? undefined : selectedEntityId || undefined 
                  }))}
                  className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                />
                Aplicar a todas
              </label>
            </div>
          </div>

          {/* Index Type */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Índice de Vegetación
            </label>
            <select
              value={config.index_type}
              onChange={(e) => setConfig(prev => ({ ...prev, index_type: e.target.value as VegetationIndexType }))}
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-emerald-500 focus:border-emerald-500"
            >
              {INDEX_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          {/* Thresholds */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Umbral Bajo (alerta si &lt;)
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={config.threshold_low || ''}
                onChange={(e) => setConfig(prev => ({ 
                  ...prev, 
                  threshold_low: e.target.value ? parseFloat(e.target.value) : undefined 
                }))}
                placeholder="Ej: 0.3"
                className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-emerald-500 focus:border-emerald-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                Umbral Alto (alerta si &gt;)
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                max="1"
                value={config.threshold_high || ''}
                onChange={(e) => setConfig(prev => ({ 
                  ...prev, 
                  threshold_high: e.target.value ? parseFloat(e.target.value) : undefined 
                }))}
                placeholder="Opcional"
                className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-emerald-500 focus:border-emerald-500"
              />
            </div>
          </div>

          {/* Webhook URL */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              URL de Webhook (N8N)
              {!n8nAvailable && (
                <span className="ml-2 text-xs text-amber-600">(N8N no configurado)</span>
              )}
            </label>
            <input
              type="url"
              value={config.webhook_url || ''}
              onChange={(e) => setConfig(prev => ({ ...prev, webhook_url: e.target.value }))}
              placeholder="https://n8n.example.com/webhook/..."
              disabled={!n8nAvailable}
              className={`w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-emerald-500 focus:border-emerald-500 ${
                !n8nAvailable ? 'bg-slate-50 text-slate-400' : ''
              }`}
            />
            <p className="text-xs text-slate-500 mt-1">
              Opcional. Si se configura, se enviará una notificación al webhook cuando se active la alerta.
            </p>
          </div>

          {/* Enable Toggle */}
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="enabled"
              checked={config.enabled}
              onChange={(e) => setConfig(prev => ({ ...prev, enabled: e.target.checked }))}
              className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
            />
            <label htmlFor="enabled" className="text-sm text-slate-700">
              Alerta activa
            </label>
          </div>

          {/* Save Button */}
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="w-full py-2.5 bg-emerald-600 text-white rounded-lg font-medium hover:bg-emerald-700 disabled:bg-slate-300 disabled:cursor-wait transition-colors"
          >
            {isSaving ? 'Guardando...' : 'Guardar Configuración'}
          </button>
        </div>
      </section>

      {/* Test Alert */}
      <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
        <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-amber-500" />
          Probar Alerta
        </h2>
        
        <p className="text-sm text-slate-600 mb-4">
          Simula una alerta para verificar la configuración y ver el mensaje que se enviaría.
        </p>

        <div className="flex gap-3">
          <button
            onClick={handleTestAlert}
            disabled={isTesting || !selectedEntityId}
            className="flex items-center gap-2 px-4 py-2 bg-amber-500 text-white rounded-lg font-medium hover:bg-amber-600 disabled:bg-slate-300 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="w-4 h-4" />
            {isTesting ? 'Probando...' : 'Probar Alerta'}
          </button>
          
          <button
            onClick={handleShowFormat}
            disabled={!selectedEntityId}
            className="flex items-center gap-2 px-4 py-2 bg-slate-100 text-slate-700 rounded-lg font-medium hover:bg-slate-200 disabled:bg-slate-100 disabled:text-slate-400 disabled:cursor-not-allowed transition-colors"
          >
            <Code className="w-4 h-4" />
            Ver Formato N8N
          </button>
        </div>

        {/* Test Result */}
        {testResult && (
          <div className="mt-4 p-4 bg-slate-50 rounded-lg border border-slate-200">
            <h3 className="text-sm font-medium text-slate-700 mb-2">Vista previa del mensaje:</h3>
            <p className="text-sm text-slate-600 whitespace-pre-wrap">{testResult}</p>
          </div>
        )}

        {/* Format Example */}
        {formatExample && (
          <div className="mt-4 p-4 bg-slate-900 rounded-lg">
            <h3 className="text-sm font-medium text-slate-300 mb-2">Ejemplo de payload N8N:</h3>
            <pre className="text-xs text-emerald-400 overflow-x-auto">
              {JSON.stringify(formatExample, null, 2)}
            </pre>
          </div>
        )}
      </section>

      {/* N8N Not Available Warning */}
      {!n8nAvailable && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="font-medium text-amber-800">N8N no configurado</h3>
              <p className="text-sm text-amber-700 mt-1">
                La integración con N8N no está disponible para este tenant. Las alertas se pueden configurar,
                pero los webhooks no se enviarán hasta que N8N esté habilitado.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AlertsTab;
