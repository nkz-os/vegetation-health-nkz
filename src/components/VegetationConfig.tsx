import React, { useState, useEffect } from 'react';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationConfig } from '../hooks/useVegetationConfig';
import { ModeSelector } from './widgets/ModeSelector';
import { CalculationButton } from './widgets/CalculationButton';
import { CarbonInputsWidget } from './widgets/CarbonInputsWidget';
import { DateRangePicker } from './widgets/DateRangePicker';
import { IndexPillSelector } from './widgets/IndexPillSelector';
import { useVegetationApi } from '../services/api';

interface VegetationConfigProps {
  mode?: 'panel' | 'page';
}

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

  useEffect(() => {
    api.listJobs('completed', 5, 0)
      .then(response => {
        if (response && response.jobs) {
          setRecentJobs(response.jobs);
        }
      })
      .catch(console.error);
  }, []);

  const handleModeChange = (indexType: string) => {
    // Ensure casting if strict types are enforced, though string usually works due to union
    setSelectedIndex(indexType as any);
  };

  if (mode === 'panel') {
    return (
      <div className="flex flex-col gap-4 p-4 h-full overflow-y-auto">
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
