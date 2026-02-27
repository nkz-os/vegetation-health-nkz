/**
 * Prescription Tab - Vegetation Prime
 *
 * Export prescription maps (GeoJSON, Shapefile, CSV) and link to viewer.
 * Machinery integration (ISOBUS, N8N) removed per Phase 1 cleanup.
 */

import React, { useState } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { FileDown, Map } from 'lucide-react';

const PrescriptionTab: React.FC = () => {
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
          Descarga el mapa de prescripción VRA en diferentes formatos.
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
