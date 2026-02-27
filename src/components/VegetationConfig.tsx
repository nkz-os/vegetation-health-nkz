import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationConfig } from '../hooks/useVegetationConfig';
import { ModeSelector } from './widgets/ModeSelector';
import { CalculationButton } from './widgets/CalculationButton';
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
  const { t } = useTranslation();
  // Note: api hook available for future ISOBUS features

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
        <span>{t('configPanel.exportMap')}</span>
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
        <span>{t('configPanel.openInVegetation')}</span>
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
  const [formula, setFormula] = useState('');
  const [formulaValid, setFormulaValid] = useState<boolean | null>(null);
  const formulaRef = useRef<HTMLTextAreaElement>(null);
  const { t } = useTranslation();
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
            <span className="font-medium text-blue-800">{t('configPanel.recommendedIndex')}</span>{' '}
            <span className="text-blue-600">{recommendation.default_index}</span>
            {recommendation.valid_indices.length > 1 && (
              <span className="text-blue-500 ml-1">
                ({t('configPanel.validIndices', { indices: recommendation.valid_indices.join(', ') })})
              </span>
            )}
          </div>
        )}

        <section>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">{t('configPanel.indexAndCalc')}</h3>
          <IndexPillSelector
            selectedIndex={selectedIndex || 'NDVI'}
            onIndexChange={setSelectedIndex}
            showCustom={true}
            className="mb-4"
          />

          {selectedIndex === 'CUSTOM' && (
            <div className={`mb-4 p-3 rounded-md border transition-colors ${formulaValid === true ? 'bg-emerald-50 border-emerald-300' :
              formulaValid === false ? 'bg-red-50 border-red-300' :
                'bg-purple-50 border-purple-100'
              }`}>
              <label className="block text-xs font-medium text-purple-800 mb-1">{t('configPanel.customFormula')}</label>
              <textarea
                ref={formulaRef}
                value={formula}
                onChange={(e) => {
                  setFormula(e.target.value);
                  // Basic paren-balance validation
                  const val = e.target.value.trim();
                  if (!val) { setFormulaValid(null); return; }
                  const opens = (val.match(/\(/g) || []).length;
                  const closes = (val.match(/\)/g) || []).length;
                  const hasBands = /B\d{2}/i.test(val);
                  setFormulaValid(opens === closes && hasBands);
                }}
                placeholder={t('configPanel.formulaPlaceholder')}
                className="w-full text-xs rounded p-2 h-20 font-mono bg-white border border-slate-200 focus:ring-purple-500 focus:border-purple-500"
              />

              {/* Band chips — color-coded by spectrum */}
              <div className="mt-2">
                <span className="text-[10px] uppercase font-bold text-slate-400 mr-1">{t('configPanel.bands')}</span>
                <div className="flex flex-wrap gap-1 mt-1">
                  {/* Visible (Blue/Green/Red) */}
                  {[{ band: 'B02', label: 'B02 (Blue)', color: 'bg-blue-100 text-blue-700 border-blue-200 hover:bg-blue-200' },
                  { band: 'B03', label: 'B03 (Green)', color: 'bg-green-100 text-green-700 border-green-200 hover:bg-green-200' },
                  { band: 'B04', label: 'B04 (Red)', color: 'bg-red-100 text-red-700 border-red-200 hover:bg-red-200' },
                  { band: 'B05', label: 'B05 (RE1)', color: 'bg-orange-100 text-orange-700 border-orange-200 hover:bg-orange-200' },
                  /* NIR */
                  { band: 'B08', label: 'B08 (NIR)', color: 'bg-emerald-100 text-emerald-700 border-emerald-200 hover:bg-emerald-200' },
                  { band: 'B8A', label: 'B8A (NIR)', color: 'bg-emerald-100 text-emerald-700 border-emerald-200 hover:bg-emerald-200' },
                  /* SWIR */
                  { band: 'B11', label: 'B11 (SWIR)', color: 'bg-amber-100 text-amber-700 border-amber-200 hover:bg-amber-200' },
                  { band: 'B12', label: 'B12 (SWIR)', color: 'bg-amber-100 text-amber-700 border-amber-200 hover:bg-amber-200' },
                  ].map(({ band, label, color }) => (
                    <button key={band} type="button" title={label}
                      className={`text-[10px] px-2 py-0.5 border rounded font-mono cursor-pointer transition-colors ${color}`}
                      onClick={() => { setFormula(prev => prev + band); formulaRef.current?.focus(); }}
                    >
                      {band}
                    </button>
                  ))}
                </div>
              </div>

              {/* Operator chips */}
              <div className="mt-2">
                <span className="text-[10px] uppercase font-bold text-slate-400 mr-1">{t('configPanel.operators')}</span>
                <div className="flex flex-wrap gap-1 mt-1">
                  {['+', '-', '*', '/', '(', ')'].map(op => (
                    <button key={op} type="button"
                      className="text-xs px-2 py-0.5 bg-slate-100 border border-slate-200 rounded font-mono cursor-pointer hover:bg-slate-200 text-slate-600 transition-colors"
                      onClick={() => { setFormula(prev => prev + ` ${op} `); formulaRef.current?.focus(); }}
                    >
                      {op}
                    </button>
                  ))}
                </div>
              </div>

              {/* Preset formulas */}
              <div className="mt-2 flex items-center gap-2">
                <span className="text-[10px] uppercase font-bold text-slate-400">{t('configPanel.presets')}</span>
                {[{ name: 'NDVI', formula: '(B08 - B04) / (B08 + B04)' },
                { name: 'EVI', formula: '2.5 * (B08 - B04) / (B08 + 6 * B04 - 7.5 * B02 + 1)' },
                { name: 'SAVI', formula: '1.5 * (B08 - B04) / (B08 + B04 + 0.5)' },
                ].map(preset => (
                  <button key={preset.name} type="button"
                    className="text-[10px] px-2 py-0.5 bg-purple-100 border border-purple-200 rounded text-purple-700 font-semibold cursor-pointer hover:bg-purple-200 transition-colors"
                    onClick={() => { setFormula(preset.formula); setFormulaValid(true); formulaRef.current?.focus(); }}
                  >
                    {preset.name}
                  </button>
                ))}
                <button type="button"
                  className="text-[10px] px-2 py-0.5 bg-slate-50 border border-slate-200 rounded text-slate-500 cursor-pointer hover:bg-red-50 hover:text-red-500 hover:border-red-200 transition-colors ml-auto"
                  onClick={() => { setFormula(''); setFormulaValid(null); }}
                >
                  ✕ {t('configPanel.clear')}
                </button>
              </div>
            </div>
          )}
        </section>

        <section>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">{t('configPanel.analysisPeriod')}</h3>
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
          <h3 className="text-sm font-semibold text-slate-700 mb-2">{t('configPanel.copernicusCredentials')}</h3>
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
            <p className="text-[10px] text-slate-400">{t('configPanel.credentialsHint')}</p>
          </div>
        </section>

        <div className="border-t border-slate-200 my-2" />

        <section>
          <h3 className="text-sm font-semibold text-slate-700 mb-2">{t('configPanel.recentActivity')}</h3>
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
            <p className="text-xs text-slate-400 italic">{t('configPanel.noRecentActivity')}</p>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-900 mb-6">{t('configPanel.advancedConfig')}</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <h2 className="text-lg font-semibold mb-4">{t('configPanel.vegetationAnalysis')}</h2>
          <ModeSelector
            currentIndex={selectedIndex || 'NDVI'}
            onChange={handleModeChange}
          />
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200">
          <h2 className="text-lg font-semibold mb-4">{t('configPanel.copernicusCredentials')}</h2>
          <div className="space-y-3">
            <input
              type="text"
              placeholder="Client ID (Opcional)"
              className="block w-full text-sm border-slate-300 rounded-md shadow-sm focus:border-blue-500 focus:ring-blue-500"
              value={config.copernicus_client_id || ''}
              onChange={(e) => saveConfig({ ...config, copernicus_client_id: e.target.value } as any)}
            />
            <input
              type="password"
              placeholder="Client Secret (Opcional)"
              className="block w-full text-sm border-slate-300 rounded-md shadow-sm focus:border-blue-500 focus:ring-blue-500"
              onChange={(e) => saveConfig({ ...config, copernicus_client_secret: e.target.value } as any)}
            />
            <p className="text-xs text-slate-400">{t('configPanel.credentialsHint')}</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default VegetationConfig;
