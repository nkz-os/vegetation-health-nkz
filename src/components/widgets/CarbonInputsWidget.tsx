/**
 * Carbon Inputs Widget (Phase F3 Frontend)
 * Form for capturing user inputs required for carbon calculation:
 * - strawRemoved: Whether straw residue is removed from field
 * - soilType: Soil classification affecting carbon sequestration
 * 
 * Wired to API: getCarbonConfig / saveCarbonConfig
 */

import React, { useState, useEffect } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';

interface CarbonConfigLocal {
  strawRemoved: boolean;
  soilType: 'clay' | 'loam' | 'sandy' | 'organic';
  tillageType?: 'conventional' | 'reduced' | 'no-till';
}

interface CarbonInputsWidgetProps {
  entityId?: string;
  onSave?: (config: CarbonConfigLocal) => void;
  compact?: boolean;
}

const SOIL_TYPES = [
  { value: 'clay', label: 'Arcilloso', description: 'Retiene más carbono' },
  { value: 'loam', label: 'Franco', description: 'Equilibrado' },
  { value: 'sandy', label: 'Arenoso', description: 'Menor retención' },
  { value: 'organic', label: 'Orgánico', description: 'Alta capacidad' },
];

const TILLAGE_TYPES = [
  { value: 'conventional', label: 'Convencional', factor: 0.7 },
  { value: 'reduced', label: 'Reducido', factor: 0.85 },
  { value: 'no-till', label: 'Siembra Directa', factor: 1.0 },
];

export const CarbonInputsWidget: React.FC<CarbonInputsWidgetProps> = ({
  entityId: propEntityId,
  onSave,
  compact = false,
}) => {
  const { selectedEntityId } = useVegetationContext();
  const effectiveEntityId = propEntityId || selectedEntityId;
  const api = useVegetationApi();

  const [config, setConfig] = useState<CarbonConfigLocal>({
    strawRemoved: false,
    soilType: 'loam',
    tillageType: 'conventional',
  });

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load existing carbon config when entity changes
  useEffect(() => {
    if (!effectiveEntityId) return;

    const loadConfig = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const existingConfig = await api.getCarbonConfig(effectiveEntityId);
        if (existingConfig) {
          setConfig({
            strawRemoved: existingConfig.strawRemoved,
            soilType: existingConfig.soilType as CarbonConfigLocal['soilType'],
            tillageType: existingConfig.tillageType as CarbonConfigLocal['tillageType'],
          });
        }
      } catch (err) {
        // Config doesn't exist yet - use defaults
        console.debug('[CarbonInputsWidget] No existing config, using defaults');
      } finally {
        setIsLoading(false);
      }
    };

    loadConfig();
  }, [effectiveEntityId, api]);

  const handleSave = async () => {
    if (!effectiveEntityId) return;

    setIsSaving(true);
    setError(null);
    try {
      await api.saveCarbonConfig(effectiveEntityId, config);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      onSave?.(config);
    } catch (err) {
      console.error('Failed to save carbon config:', err);
      setError('Error al guardar la configuración');
    } finally {
      setIsSaving(false);
    }
  };

  if (!effectiveEntityId) {
    return (
      <div className="p-4 text-center text-slate-500 text-sm">
        Selecciona una parcela para configurar
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className={`bg-white rounded-lg border border-slate-200 ${compact ? 'p-3' : 'p-4'}`}>
        <div className="flex items-center justify-center py-4">
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-emerald-600"></div>
          <span className="ml-2 text-sm text-slate-500">Cargando configuración...</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`bg-white rounded-lg border border-slate-200 ${compact ? 'p-3' : 'p-4'}`}>
      <h3 className={`font-semibold text-slate-800 ${compact ? 'text-sm mb-2' : 'text-base mb-4'}`}>
        Configuración de Carbono
      </h3>

      {error && (
        <div className="mb-3 p-2 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Straw Removal Toggle */}
      <div className="mb-4">
        <label className="flex items-center justify-between cursor-pointer">
          <span className="text-sm text-slate-700">¿Se retira la paja?</span>
          <div className="relative">
            <input
              type="checkbox"
              checked={config.strawRemoved}
              onChange={(e) => setConfig({ ...config, strawRemoved: e.target.checked })}
              className="sr-only"
            />
            <div className={`w-10 h-5 rounded-full transition-colors ${config.strawRemoved ? 'bg-orange-500' : 'bg-green-500'
              }`}>
              <div className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${config.strawRemoved ? 'translate-x-5' : ''
                }`} />
            </div>
          </div>
        </label>
        <p className="text-xs text-slate-500 mt-1">
          {config.strawRemoved
            ? '⚠️ Menor secuestro de carbono'
            : '✓ La paja se incorpora al suelo'}
        </p>
      </div>

      {/* Soil Type Selector */}
      <div className="mb-4">
        <label className="block text-sm text-slate-700 mb-2">Tipo de Suelo</label>
        <div className={`grid ${compact ? 'grid-cols-2' : 'grid-cols-4'} gap-2`}>
          {SOIL_TYPES.map((soil) => (
            <button
              key={soil.value}
              onClick={() => setConfig({ ...config, soilType: soil.value as CarbonConfigLocal['soilType'] })}
              className={`px-3 py-2 rounded-lg text-xs font-medium transition-all ${config.soilType === soil.value
                  ? 'bg-emerald-100 text-emerald-800 border-2 border-emerald-500'
                  : 'bg-slate-50 text-slate-600 border border-slate-200 hover:bg-slate-100'
                }`}
            >
              {soil.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tillage Type (optional, non-compact only) */}
      {!compact && (
        <div className="mb-4">
          <label className="block text-sm text-slate-700 mb-2">Sistema de Laboreo</label>
          <select
            value={config.tillageType}
            onChange={(e) => setConfig({ ...config, tillageType: e.target.value as CarbonConfigLocal['tillageType'] })}
            className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm bg-white"
          >
            {TILLAGE_TYPES.map((tillage) => (
              <option key={tillage.value} value={tillage.value}>
                {tillage.label} (×{tillage.factor})
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Save Button */}
      <button
        onClick={handleSave}
        disabled={isSaving}
        className={`w-full py-2 rounded-lg font-medium text-sm transition-all ${saved
            ? 'bg-green-500 text-white'
            : isSaving
              ? 'bg-slate-300 text-slate-500 cursor-wait'
              : 'bg-emerald-600 text-white hover:bg-emerald-700'
          }`}
      >
        {saved ? '✓ Guardado' : isSaving ? 'Guardando...' : 'Guardar Configuración'}
      </button>
    </div>
  );
};

export default CarbonInputsWidget;
