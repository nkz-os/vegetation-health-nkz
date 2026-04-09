import React, { useState, useEffect } from 'react';
import { useTranslation } from '@nekazari/sdk';
import { useVegetationContext } from '../services/vegetationContext';
import { useVegetationApi } from '../services/api';
import { IndexPillSelector } from './widgets/IndexPillSelector';
import { CalculationButton } from './widgets/CalculationButton';

/**
 * Vegetation Cockpit - Minimalist Context Panel for Host Integration
 * (SOTA Standard - Phase 5)
 */
export const VegetationConfig: React.FC = () => {
  const { t } = useTranslation();
  const api = useVegetationApi();
  const {
    selectedEntityId,
    selectedIndex,
    setSelectedIndex,
    selectedDate,
    setSelectedSceneId,
    setSelectedDate
  } = useVegetationContext();

  const [availability, setAvailability] = useState<any[]>([]);
  const [opacity, setOpacity] = useState(0.8);
  const [isLoading, setIsLoading] = useState(false);

  // Fetch scene availability when entity/index changes
  useEffect(() => {
    if (selectedEntityId && selectedIndex) {
      setIsLoading(true);
      api.getScenesAvailable(selectedEntityId, selectedIndex)
        .then(res => setAvailability(res?.timeline || []))
        .catch(console.error)
        .finally(() => setIsLoading(false));
    }
  }, [selectedEntityId, selectedIndex]);

  // Sync opacity with Cesium Layer (via window property or internal event)
  useEffect(() => {
    const layer = (window as any).__NKZ_LAYERS__?.['vegetation-prime'];
    if (layer) layer.alpha = opacity;
  }, [opacity]);

  // Handle finding the closest available scene to the global date
  const findAndSelectClosest = () => {
    if (!selectedDate || availability.length === 0) return;
    const targetTime = selectedDate.getTime();
    const closest = availability.reduce((prev, curr) => {
      const prevDiff = Math.abs(new Date(prev.date).getTime() - targetTime);
      const currDiff = Math.abs(new Date(curr.date).getTime() - targetTime);
      return currDiff < prevDiff ? curr : prev;
    });
    
    if (closest) {
      setSelectedSceneId(closest.id);
      setSelectedDate(new Date(closest.date));
      // Dispatch time sync back to host if needed, but here we just align the module
    }
  };

  if (!selectedEntityId) {
    return (
      <div className="p-8 text-center text-slate-400 italic text-sm">
        {t('configPanel.selectParcelPrompt', 'Seleccione una parcela en el mapa para ver su salud vegetativa')}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-5 h-full bg-white animate-fade-in">
      {/* Header: Parcel Health */}
      <header>
        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
          {t('configPanel.diagnostic', 'Diagnóstico de Salud')}
        </div>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-slate-800 truncate max-w-[150px]">
            {selectedEntityId.split(':').pop()}
          </h2>
          <span className="px-2 py-1 bg-emerald-100 text-emerald-700 text-[10px] font-bold rounded-full uppercase">
            {selectedIndex || 'NDVI'}
          </span>
        </div>
      </header>

      {/* Index Selection */}
      <section>
        <label className="text-xs font-semibold text-slate-500 block mb-3">
          {t('configPanel.selectIndex', 'Índice Espectral')}
        </label>
        <IndexPillSelector
          selectedIndex={selectedIndex || 'NDVI'}
          onIndexChange={setSelectedIndex}
          showCustom={false}
          className="grid grid-cols-3 gap-2"
        />
      </section>

      {/* Opacity Control */}
      <section className="p-3 bg-slate-50 rounded-xl border border-slate-100">
        <div className="flex justify-between text-[11px] font-medium text-slate-600 mb-2">
          <span>{t('configPanel.opacity', 'Opacidad de Capa')}</span>
          <span className="font-mono">{Math.round(opacity * 100)}%</span>
        </div>
        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={opacity}
          onChange={(e) => setOpacity(parseFloat(e.target.value))}
          className="w-full h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-emerald-500"
        />
      </section>

      {/* Scene Availability Indicators */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <label className="text-xs font-semibold text-slate-500">
            {t('configPanel.availableData', 'Datos Sentinel-2')}
          </label>
          <button 
            onClick={findAndSelectClosest}
            className="text-[10px] text-emerald-600 font-bold hover:underline"
          >
            {t('configPanel.syncToTime', 'Sincronizar al Visor')}
          </button>
        </div>
        
        {isLoading ? (
          <div className="h-10 flex gap-1 items-end animate-pulse">
            {[...Array(10)].map((_, i) => (
              <div key={i} className="flex-1 bg-slate-200 rounded-sm h-4" />
            ))}
          </div>
        ) : availability.length > 0 ? (
          <div className="flex gap-1 h-10 items-end">
            {availability.slice(-12).map((item, i) => {
              const isActive = selectedDate?.toISOString().split('T')[0] === item.date.split('T')[0];
              return (
                <div 
                  key={i} 
                  title={`${item.date} - NDVI: ${item.mean_value}`}
                  className={`flex-1 rounded-t-md transition-all cursor-pointer ${
                    isActive ? 'bg-emerald-500 h-10 ring-2 ring-emerald-200' : 'bg-emerald-100 hover:bg-emerald-200 h-6'
                  }`}
                  onClick={() => {
                    setSelectedSceneId(item.id);
                    setSelectedDate(new Date(item.date));
                  }}
                />
              );
            })}
          </div>
        ) : (
          <div className="text-[11px] text-slate-400 italic bg-slate-50 p-3 rounded-lg text-center">
            {t('configPanel.noData', 'No hay imágenes recientes para esta parcela')}
          </div>
        )}
      </section>

      {/* Primary Action */}
      <section className="mt-auto border-t border-slate-100 pt-4">
        <CalculationButton
          entityId={selectedEntityId}
          indexType={selectedIndex || 'NDVI'}
          className="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 rounded-xl shadow-lg shadow-emerald-100 transition-all flex items-center justify-center gap-2"
        />
        <p className="text-[10px] text-slate-400 text-center mt-3 leading-tight">
          {t('configPanel.actionHint', 'El análisis procesa datos espectrales en tiempo real desde el servidor de tiles.')}
        </p>
      </section>
    </div>
  );
};

export default VegetationConfig;
