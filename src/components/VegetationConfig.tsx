import React, { useState, useEffect } from 'react';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationConfig } from '../hooks/useVegetationConfig';
import { ModeSelector } from './widgets/ModeSelector';
import { CalculationButton } from './widgets/CalculationButton';
import { CarbonInputsWidget } from './widgets/CarbonInputsWidget';
import { DateRangePicker } from './widgets/DateRangePicker';
import { IndexPillSelector } from './widgets/IndexPillSelector';
import { useVegetationApi } from '../services/api';
import { useCropRecommendation } from '../hooks/useCropRecommendation';

interface VegetationConfigProps {
  mode?: 'panel' | 'page';
}

/**
 * Quick Actions Toolbar for Unified Viewer (Option B only)
 * Integrated at the TOP of VegetationConfig when mode === 'panel'
 */
const QuickActionsToolbar: React.FC<{
  entityId: string | null;
  onZoningTrigger: () => void;
  isZoningLoading: boolean;
}> = ({ entityId, onZoningTrigger, isZoningLoading }) => {
  const api = useVegetationApi();
  const isobusAvailable = api.isIsobusAvailable();

  const handleNavigateToModule = (tab: string) => {
    if (!entityId) return;
    const url = `/vegetation?entityId=${encodeURIComponent(entityId)}&tab=${tab}`;
    // Use host navigation if available, fallback to window.location
    if ((window as any).__nekazariNavigate) {
      (window as any).__nekazariNavigate(url);
    } else {
      window.location.href = url;
    }
  };

  if (!entityId) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-2 p-2 bg-gradient-to-r from-emerald-50 to-blue-50 rounded-lg border border-emerald-200 mb-4">
      {/* Generate VRA Zones */}
      <button
        onClick={onZoningTrigger}
        disabled={isZoningLoading}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-white rounded-md text-xs font-medium text-emerald-700 hover:bg-emerald-50 border border-emerald-200 disabled:opacity-50 disabled:cursor-wait transition-colors"
        title="Generar zonas de manejo variable (VRA)"
      >
        {isZoningLoading ? (
          <span className="animate-spin h-3 w-3 border-2 border-emerald-500 border-t-transparent rounded-full"></span>
        ) : (
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
          </svg>
        )}
        <span>{isZoningLoading ? 'Generando...' : 'Generar Zonas'}</span>
      </button>

      {/* Export Prescription Link */}
      <button
        onClick={() => handleNavigateToModule('prescription')}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-white rounded-md text-xs font-medium text-blue-700 hover:bg-blue-50 border border-blue-200 transition-colors"
        title="Exportar mapa de prescripción"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
        <span>Exportar Mapa</span>
      </button>

      {/* Carbon Shortcut */}
      <button
        onClick={() => handleNavigateToModule('config')}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-white rounded-md text-xs font-medium text-amber-700 hover:bg-amber-50 border border-amber-200 transition-colors"
        title="Configurar cálculo de carbono"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707" />
        </svg>
        <span>Carbono</span>
      </button>

      {/* Open in Vegetation Prime */}
      <button
        onClick={() => handleNavigateToModule('analytics')}
        className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 rounded-md text-xs font-medium text-white hover:bg-emerald-700 transition-colors"
        title="Abrir análisis completo en Vegetation Prime"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
        </svg>
        <span>Abrir en Vegetation</span>
      </button>
    </div>
  );
};

export const VegetationConfig: React.FC<VegetationConfigProps> = ({ mode = 'panel' }) => {
  const {
    selectedEntityId,
    selectedIndex,
    setSelectedIndex,
    dateRange,
    setDateRange
  } = useVegetationContext();

  // Hook returns { config, loading, error, saveConfig, ... }
  // NOT updateConfig
  const { config, saveConfig } = useVegetationConfig();
  const api = useVegetationApi();
  const [showCarbonConfig, setShowCarbonConfig] = useState(false);
  const [formula, setFormula] = useState('');
  const [recentJobs, setRecentJobs] = useState<any[]>([]);
  
  // Quick Actions state
  const [isZoningLoading, setIsZoningLoading] = useState(false);
  const [zoningMessage, setZoningMessage] = useState<string | null>(null);

  // Crop recommendation for selected entity
  const { recommendation, loading: cropLoading } = useCropRecommendation(
    // Get crop species from entity if available
    (window as any).__nekazariContext?.selectedEntity?.cropSpecies || null
  );

  useEffect(() => {
    api.listJobs('completed', 5, 0)
      .then(response => {
        if (response && response.jobs) {
          setRecentJobs(response.jobs);
        }
      })
      .catch(console.error);
  }, []);

  // Handle zoning trigger from Quick Actions
  const handleZoningTrigger = async () => {
    if (!selectedEntityId) return;
    
    setIsZoningLoading(true);
    setZoningMessage(null);
    
    try {
      const result = await api.triggerZoning(selectedEntityId);
      setZoningMessage(`Zonas generándose. Task ID: ${result.task_id}`);
      // Optionally poll or show link to zoning tab
    } catch (error) {
      console.error('Zoning trigger failed:', error);
      setZoningMessage('Error al generar zonas');
    } finally {
      setIsZoningLoading(false);
    }
  };

  const handleModeChange = (indexType: string) => {
    // Ensure casting if strict types are enforced, though string usually works due to union
    setSelectedIndex(indexType as any);
  };

  if (mode === 'panel') {
    return (
      <div className="flex flex-col gap-4 p-4 h-full overflow-y-auto">
        {/* Quick Actions Toolbar (Option B - at TOP) */}
        <QuickActionsToolbar
          entityId={selectedEntityId}
          onZoningTrigger={handleZoningTrigger}
          isZoningLoading={isZoningLoading}
        />

        {/* Zoning status message */}
        {zoningMessage && (
          <div className={`p-2 rounded text-xs ${zoningMessage.includes('Error') ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700'}`}>
            {zoningMessage}
          </div>
        )}

        {/* Crop Recommendation (if available) */}
        {recommendation && !cropLoading && (
          <div className="p-2 bg-blue-50 rounded-lg border border-blue-200 text-xs">
            <span className="font-medium text-blue-800">Índice recomendado:</span>{' '}
            <span className="text-blue-600">{recommendation.default_index}</span>
            {recommendation.valid_indices.length > 1 && (
              <span className="text-blue-500 ml-1">
                (Válidos: {recommendation.valid_indices.join(', ')})
              </span>
            )}
          </div>
        )}

        <section>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">Índice & Cálculo</h3>
          <IndexPillSelector
            selectedIndex={selectedIndex || 'NDVI'}
            onIndexChange={setSelectedIndex}
            showCustom={true}
            className="mb-4"
          />

          {selectedIndex === 'CUSTOM' && (
            <div className="mb-4 p-3 bg-purple-50 rounded-md border border-purple-100">
              <label className="block text-xs font-medium text-purple-800 mb-1">Fórmula Personalizada</label>
              <textarea
                value={formula}
                onChange={(e) => setFormula(e.target.value)}
                placeholder="Ej: (B08 - B04) / (B08 + B04)"
                className="w-full text-xs border-purple-200 rounded p-2 h-20 focus:ring-purple-500 focus:border-purple-500 font-mono"
              />
              <div className="flex flex-wrap gap-1 mt-2">
                {['B02', 'B03', 'B04', 'B08', 'B11', 'B12'].map(b => (
                  <span key={b} className="text-[10px] px-1.5 py-0.5 bg-white border border-purple-200 rounded text-purple-600 font-mono cursor-pointer hover:bg-purple-100" onClick={() => setFormula(prev => prev + b)}>
                    {b}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>

        <section>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">Periodo de Análisis</h3>
          <DateRangePicker
            dateRange={dateRange}
            onChange={setDateRange}
          />
        </section>

        <section>
          <div className="flex gap-2">
            <CalculationButton
              formula={selectedIndex === 'CUSTOM' ? formula : undefined}
              startDate={dateRange.startDate?.toISOString().split('T')[0]}
              endDate={dateRange.endDate?.toISOString().split('T')[0]}
              entityId={selectedEntityId || undefined}
            />
            <button
              onClick={async () => {
                // Check if geometry is available
                // Note: selectedGeometry is now available in context
                if (!selectedEntityId) {
                  alert("Seleccione una zona en el mapa primero.");
                  return;
                }
                const name = prompt("Nombre para la Nueva Zona de Gestión:");
                if (name) {
                  try {
                    // We use a pragmatic approach: if context has geometry, use it.
                    // If not, we might be saving an existing entity clone or failing gracefully.
                    // Here we assume context has it or pass null to let backend handle/mock.
                    const { saveManagementZone } = useVegetationApi();
                    // @ts-ignore
                    const geom = (window as any).__nekazariContext?.selectedGeometry || null;

                    await saveManagementZone(name, geom, selectedEntityId); // selectedEntityId as parent? or just ref
                    alert("Zona guardada correctamente.");
                  } catch (e) {
                    console.error(e);
                    alert("Error al guardar zona.");
                  }
                }
              }}
              className="flex-1 px-4 py-2 bg-white border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
            >
              💾 Guardar Zona
            </button>
          </div>
        </section>

        <div className="border-t border-slate-200 my-2" />

        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-slate-700">Opciones Avanzadas</h3>
            <button
              onClick={() => setShowCarbonConfig(!showCarbonConfig)}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              {showCarbonConfig ? 'Ocultar' : 'Mostrar'}
            </button>
          </div>

          {showCarbonConfig && (
            <div className="space-y-4">
              <CarbonInputsWidget
                entityId={selectedEntityId || undefined}
                compact={true}
                onSave={(cfg) => saveConfig({ ...config, ...cfg } as any)}
              />

              <div className="pt-2 border-t border-slate-100">
                <h4 className="text-xs font-medium text-slate-700 mb-2">Credenciales Copernicus</h4>
                <div className="space-y-2">
                  <input
                    type="text"
                    placeholder="Client ID (Opcional)"
                    className="block w-full text-xs border-slate-300 rounded-md shadow-sm focus:border-blue-500 focus:ring-blue-500"
                    value={config.copernicus_client_id || ''}
                    onChange={(e) => saveConfig({ ...config, copernicus_client_id: e.target.value } as any)}
                  />
                  <input
                    type="password"
                    placeholder="Client Secret (Opcional)"
                    className="block w-full text-xs border-slate-300 rounded-md shadow-sm focus:border-blue-500 focus:ring-blue-500"
                    onChange={(e) => saveConfig({ ...config, copernicus_client_secret: e.target.value } as any)}
                  />
                  <p className="text-[10px] text-slate-400">Dejar en blanco para usar credenciales de plataforma.</p>
                </div>
              </div>
            </div>
          )}
        </section>

        <div className="border-t border-slate-200 my-2" />

        <section>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">Actividad Reciente</h3>
          {recentJobs && recentJobs.length > 0 ? (
            <div className="space-y-2">
              {recentJobs.map(job => (
                <div key={job.id} className="text-xs flex justify-between items-center p-2 bg-slate-50 rounded border border-slate-100">
                  <div>
                    <div className="font-medium text-slate-800">Descarga {job.status === 'completed' ? '✅' : job.status === 'failed' ? '❌' : '⏳'}</div>
                    <div className="text-slate-500">{new Date(job.created_at).toLocaleDateString()}</div>
                  </div>
                  <div className="text-right">
                    {job.status === 'processing' && <span className="text-blue-500">{job.progress_percentage}%</span>}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-400 italic">No hay actividad reciente.</p>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-900 mb-6">Configuración Avanzada</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <h2 className="text-lg font-semibold mb-4">Análisis de Vegetación</h2>
          <ModeSelector
            currentIndex={selectedIndex || 'NDVI'}
            onChange={handleModeChange}
          />
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <h2 className="text-lg font-semibold mb-4">Cálculo de Carbono (LUE)</h2>
          <CarbonInputsWidget
            entityId={selectedEntityId || undefined}
            onSave={(cfg) => saveConfig({ ...config, ...cfg } as any)}
          />
        </div>
      </div>
    </div>
  );
};

export default VegetationConfig;
