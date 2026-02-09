/**
 * Prescription Tab - Ferrari Frontend
 * 
 * Export prescription maps (GeoJSON, Shapefile, CSV), bridge to ISOBUS for ISOXML,
 * and send map to cloud via N8N.
 * 
 * IMPORTANT: ISOXML export is delegated to NKZ-ISOBUS module (DDD separation).
 * This tab only provides a bridge/navigation to ISOBUS, not direct export.
 */

import React, { useState, useEffect } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { ModuleCapabilities } from '../../types';
import { FileDown, Upload, Map, ExternalLink } from 'lucide-react';

const PrescriptionTab: React.FC = () => {
  const { selectedEntityId } = useVegetationContext();
  const api = useVegetationApi();

  const [isExporting, setIsExporting] = useState<string | null>(null);
  const [exportMessage, setExportMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [capabilities, setCapabilities] = useState<ModuleCapabilities | null>(null);
  const [_loadingCapabilities, setLoadingCapabilities] = useState(true);

  // Load module capabilities for graceful degradation
  useEffect(() => {
    api.getCapabilities()
      .then(setCapabilities)
      .catch(console.error)
      .finally(() => setLoadingCapabilities(false));
  }, [api]);

  // Check if ISOBUS is available via host
  const isobusAvailable = api.isIsobusAvailable() || capabilities?.isobus_available;
  const n8nAvailable = capabilities?.n8n_available ?? false;

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

      // Trigger download
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      setExportMessage({ type: 'success', text: `Exportado correctamente: ${filename}` });
    } catch (error) {
      console.error('Export failed:', error);
      setExportMessage({ type: 'error', text: 'Error al exportar. Inténtalo de nuevo.' });
    } finally {
      setIsExporting(null);
    }
  };

  /**
   * Bridge to ISOBUS module
   * Navigate to ISOBUS route with entity context, letting ISOBUS handle ISOXML generation
   */
  const handleIsobusExport = () => {
    if (!selectedEntityId) return;

    // Try host-provided callback first
    if (typeof (window as any).__nekazariOpenISOBUS === 'function') {
      const geometry = (window as any).__nekazariContext?.selectedGeometry;
      (window as any).__nekazariOpenISOBUS(geometry, { entityId: selectedEntityId });
      return;
    }

    // Fallback: Navigate to ISOBUS route with query params
    const url = `/isobus/create-task?source=vegetation&entityId=${encodeURIComponent(selectedEntityId)}`;
    if ((window as any).__nekazariNavigate) {
      (window as any).__nekazariNavigate(url);
    } else {
      window.location.href = url;
    }
  };

  /**
   * Send prescription to cloud via N8N
   * Goes through our backend which proxies to N8N (security: CORS + credentials)
   */
  const handleSendToCloud = async () => {
    if (!selectedEntityId) return;

    setIsExporting('n8n');
    setExportMessage(null);

    try {
      const result = await api.sendToCloud(selectedEntityId, {
        prescription_type: 'vra_zones',
        metadata: {
          exported_at: new Date().toISOString(),
          source: 'vegetation-prime'
        }
      });

      setExportMessage({
        type: 'success',
        text: result.message || 'Mapa enviado correctamente a la nube'
      });
    } catch (error) {
      console.error('Send to cloud failed:', error);
      setExportMessage({ type: 'error', text: 'Error al enviar a la nube. Verifica la configuración de N8N.' });
    } finally {
      setIsExporting(null);
    }
  };

  /**
   * Navigate to viewer with vegetation + zoning layers active
   */
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
        <div className="text-slate-400 text-lg mb-2">Selecciona una parcela</div>
        <p className="text-slate-500 text-sm">
          Vuelve al listado y selecciona una parcela para exportar mapas de prescripción.
        </p>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <header className="mb-6">
        <h1 className="text-xl font-bold text-slate-900">Exportar Mapa de Prescripción</h1>
        <p className="text-slate-600 text-sm mt-1">
          Descarga el mapa de prescripción VRA en diferentes formatos o envíalo a maquinaria.
        </p>
      </header>

      {/* Export Message */}
      {exportMessage && (
        <div className={`mb-6 p-4 rounded-lg ${exportMessage.type === 'success'
            ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
            : 'bg-red-50 text-red-800 border border-red-200'
          }`}>
          {exportMessage.text}
        </div>
      )}

      {/* Standard Exports */}
      <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
        <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
          <FileDown className="w-5 h-5 text-emerald-600" />
          Formatos de Descarga
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* GeoJSON */}
          <button
            onClick={() => handleExport('geojson')}
            disabled={isExporting !== null}
            className="flex flex-col items-center p-4 border border-slate-200 rounded-lg hover:bg-slate-50 hover:border-emerald-300 transition-colors disabled:opacity-50"
          >
            <div className="w-12 h-12 bg-emerald-100 rounded-lg flex items-center justify-center mb-3">
              <span className="text-emerald-700 font-bold text-sm">JSON</span>
            </div>
            <span className="font-medium text-slate-800">GeoJSON</span>
            <span className="text-xs text-slate-500 mt-1">Para SIG y análisis</span>
            {isExporting === 'geojson' && <span className="text-xs text-emerald-600 mt-2">Exportando...</span>}
          </button>

          {/* Shapefile */}
          <button
            onClick={() => handleExport('shapefile')}
            disabled={isExporting !== null}
            className="flex flex-col items-center p-4 border border-slate-200 rounded-lg hover:bg-slate-50 hover:border-blue-300 transition-colors disabled:opacity-50"
          >
            <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center mb-3">
              <span className="text-blue-700 font-bold text-sm">SHP</span>
            </div>
            <span className="font-medium text-slate-800">Shapefile</span>
            <span className="text-xs text-slate-500 mt-1">Formato estándar SIG</span>
            {isExporting === 'shapefile' && <span className="text-xs text-blue-600 mt-2">Exportando...</span>}
          </button>

          {/* CSV */}
          <button
            onClick={() => handleExport('csv')}
            disabled={isExporting !== null}
            className="flex flex-col items-center p-4 border border-slate-200 rounded-lg hover:bg-slate-50 hover:border-amber-300 transition-colors disabled:opacity-50"
          >
            <div className="w-12 h-12 bg-amber-100 rounded-lg flex items-center justify-center mb-3">
              <span className="text-amber-700 font-bold text-sm">CSV</span>
            </div>
            <span className="font-medium text-slate-800">CSV</span>
            <span className="text-xs text-slate-500 mt-1">Datos tabulares</span>
            {isExporting === 'csv' && <span className="text-xs text-amber-600 mt-2">Exportando...</span>}
          </button>
        </div>
      </section>

      {/* Machinery Integration */}
      <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
        <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
          <Upload className="w-5 h-5 text-blue-600" />
          Integración con Maquinaria
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* ISOBUS Export (Bridge) */}
          <div className="p-4 border border-slate-200 rounded-lg">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center flex-shrink-0">
                <span className="text-purple-700 font-bold text-xs">ISO</span>
              </div>
              <div className="flex-1">
                <h3 className="font-medium text-slate-800">ISOXML (ISOBUS)</h3>
                <p className="text-xs text-slate-500 mt-1">
                  Formato para tractores compatibles con ISOBUS
                </p>
                <button
                  onClick={handleIsobusExport}
                  disabled={!isobusAvailable || isExporting !== null}
                  className={`mt-3 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${isobusAvailable
                      ? 'bg-purple-600 text-white hover:bg-purple-700'
                      : 'bg-slate-200 text-slate-400 cursor-not-allowed'
                    }`}
                  title={!isobusAvailable ? 'Módulo ISOBUS no disponible para este tenant' : ''}
                >
                  <span className="flex items-center gap-2">
                    <ExternalLink className="w-4 h-4" />
                    {isobusAvailable ? 'Exportar ISOXML' : 'ISOBUS no disponible'}
                  </span>
                </button>
              </div>
            </div>
          </div>

          {/* Send to Cloud (N8N) */}
          <div className="p-4 border border-slate-200 rounded-lg">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 bg-sky-100 rounded-lg flex items-center justify-center flex-shrink-0">
                <Upload className="w-5 h-5 text-sky-700" />
              </div>
              <div className="flex-1">
                <h3 className="font-medium text-slate-800">Enviar a la Nube</h3>
                <p className="text-xs text-slate-500 mt-1">
                  Envía el mapa a la nube de maquinaria vía N8N
                </p>
                <button
                  onClick={handleSendToCloud}
                  disabled={!n8nAvailable || isExporting !== null}
                  className={`mt-3 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${n8nAvailable
                      ? 'bg-sky-600 text-white hover:bg-sky-700'
                      : 'bg-slate-200 text-slate-400 cursor-not-allowed'
                    }`}
                  title={!n8nAvailable ? 'N8N no configurado para este tenant' : ''}
                >
                  {isExporting === 'n8n' ? (
                    <span className="flex items-center gap-2">
                      <span className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></span>
                      Enviando...
                    </span>
                  ) : (
                    <span>{n8nAvailable ? 'Enviar a la Nube' : 'N8N no configurado'}</span>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* View on Map */}
      <section className="bg-slate-50 rounded-xl border border-slate-200 p-4">
        <button
          onClick={handleViewOnMap}
          className="w-full flex items-center justify-center gap-2 py-3 text-emerald-700 hover:text-emerald-800 font-medium transition-colors"
        >
          <Map className="w-5 h-5" />
          Ver en el mapa
        </button>
      </section>
    </div>
  );
};

export default PrescriptionTab;
