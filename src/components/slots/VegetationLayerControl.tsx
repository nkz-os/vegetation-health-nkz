/**
 * Vegetation Layer Control - Slot component.
 */

import React, { useState, useEffect, useMemo } from 'react';
import { Layers, Cloud } from 'lucide-react';
import { useTranslation } from '@nekazari/sdk';
import { useUIKit } from '../../hooks/useUIKit';
import { useViewer } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationScenes } from '../../hooks/useVegetationScenes';
import type { VegetationIndexType } from '../../types';
import { IndexPillSelector } from '../widgets/IndexPillSelector';
import { ColorScaleLegend } from '../widgets/ColorScaleLegend';
import DateSelector from '../widgets/DateSelector';

const VegetationLayerControl: React.FC = () => {
  const { t } = useTranslation();
  // Get UI components safely from Host
  const { Card } = useUIKit();
  const { setCurrentDate } = useViewer();
  
  // Get Module Context
  const {
    selectedIndex,
    selectedDate,
    selectedEntityId,
    selectedSceneId,
    indexResults,
    layerOpacity,
    setSelectedIndex,
    setSelectedDate,
    setSelectedSceneId,
    setLayerOpacity,
  } = useVegetationContext();

  const customIndexOptions = useMemo(
    () =>
      Object.keys(indexResults || {})
        .filter((k) => k.startsWith('custom:'))
        .map((k) => ({
          key: k,
          label: indexResults[k]?.formula_name || k.replace(/^custom:/, '').slice(0, 8),
        })),
    [indexResults],
  );

  const legendIndexType = (selectedIndex?.startsWith('custom:')
    ? 'CUSTOM'
    : selectedIndex || 'NDVI') as VegetationIndexType;

  // Load scenes for current entity
  const { scenes, loading: scenesLoading } = useVegetationScenes({ 
    entityId: selectedEntityId || undefined 
  });
  
  // Local state for UI controls
  const [showCloudMask, setShowCloudMask] = useState(false);
  const opacity = layerOpacity;
  const setOpacity = setLayerOpacity;
  const [showLegend, setShowLegend] = useState(true);
  
  // Helpers
  const currentScene = scenes.find(s => s.id === selectedSceneId);
  const cloudCoverage = currentScene?.cloud_coverage ?? 0;
  
  const [legendDynamic, setLegendDynamic] = useState(false);

  // Sync viewer date — compare by timestamp to avoid infinite re-render loop
  const lastSyncedDateRef = React.useRef<number>(0);
  useEffect(() => {
    if (!selectedDate || !setCurrentDate) return;
    const ts = selectedDate.getTime();
    if (ts === lastSyncedDateRef.current) return;
    lastSyncedDateRef.current = ts;
    setCurrentDate(selectedDate);
  }, [selectedDate, setCurrentDate]);

  // Handle date change from DateSelector (receives only sceneId)
  const handleDateChange = (sceneId: string) => {
    const scene = scenes.find((s) => s.id === sceneId);
    if (scene) {
      setSelectedDate(new Date(scene.sensing_date));
      setSelectedSceneId(sceneId);
    }
  };

  if (!selectedEntityId) {
    return (
      <Card padding="md" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl w-full">
        <div className="flex items-center justify-center gap-2 py-4 text-slate-500">
          <Layers className="w-5 h-5" />
          <p>{t('layerControl.selectParcel', 'Selecciona una parcela para ver capas')}</p>
        </div>
      </Card>
    );
  }

  return (
    <>
      <Card padding="md" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl w-full max-w-[320px] shadow-lg pointer-events-auto">
        <div className="space-y-4">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-100 pb-2">
            <h3 className="font-semibold text-slate-800 flex items-center gap-2">
              <Layers className="w-4 h-4 text-green-600" />
              {t('layerControl.title', 'Capas de Vegetación')}
            </h3>
            <div className="flex items-center gap-1">
              <span className="text-xs text-slate-500 font-mono">
                 {selectedEntityId.split(':').pop()}
              </span>
            </div>
          </div>

          {/* Index Selector */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-slate-600 uppercase tracking-wider">{t('layerControl.spectralIndex', 'Índice Espectral')}</label>
            <IndexPillSelector
              selectedIndex={selectedIndex || 'NDVI'}
              onIndexChange={(idx) => setSelectedIndex(idx)}
              customIndexOptions={customIndexOptions}
            />
          </div>

          {/* Date Selector */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-slate-600 uppercase tracking-wider flex justify-between">
              {t('layerControl.imageDate', 'Fecha de Imagen')}
              {scenesLoading && <span className="text-slate-400">{t('common.loading')}</span>}
            </label>
            <DateSelector 
              selectedSceneId={selectedSceneId}
              scenes={scenes}
              onSelect={handleDateChange}
            />
            
            {/* Scene Info */}
            {currentScene && (
              <div className="flex items-center gap-4 text-xs text-slate-500 mt-1 bg-slate-50 p-1.5 rounded border border-slate-100">
                <div className="flex items-center gap-1" title={t('layerControl.cloudCoverage', 'Cobertura de nubes')}>
                  <Cloud className={`w-3 h-3 ${cloudCoverage > 20 ? 'text-amber-500' : 'text-slate-400'}`} />
                  <span>{cloudCoverage.toFixed(1)}%</span>
                </div>
                <div>ID: {currentScene.id.substring(0, 8)}...</div>
              </div>
            )}
          </div>

          {/* Layer Opacity */}
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-slate-600">
               <span>{t('layerControl.opacity', 'Opacidad')}</span>
               <span>{opacity}%</span>
            </div>
            <input 
              type="range" 
              min="0" 
              max="100" 
              value={opacity} 
              onChange={(e) => setOpacity(parseInt(e.target.value))}
              className="w-full h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-green-600"
            />
          </div>

          {/* Toggles */}
          <div className="space-y-2 pt-2 border-t border-slate-100">
             <div className="flex items-center justify-between">
                <label className="text-sm text-slate-700 flex flex-col">
                   <span>{t('layerControl.cloudMask', 'Máscara de Nubes')}</span>
                   <span className="text-xs text-slate-400">{t('layerControl.cloudMaskDesc', 'Ocultar áreas nubladas')}</span>
                </label>
                <label className="relative inline-flex items-center cursor-pointer">
                   <input
                     type="checkbox"
                     checked={showCloudMask}
                     onChange={(e) => setShowCloudMask(e.target.checked)}
                     className="sr-only peer"
                   />
                   <div className="w-9 h-5 bg-gray-300 rounded-full peer-checked:bg-green-500 peer-focus:ring-2 peer-focus:ring-green-300 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full" />
                </label>
             </div>
            
             <div className="flex items-center justify-between mt-2">
                <label className="text-sm text-slate-700 flex flex-col">
                   <span>{t('layerControl.dynamicRange', 'Rango Dinámico')}</span>
                   <span className="text-xs text-slate-400">{t('layerControl.dynamicRangeDesc', 'Ajustar color a min/max')}</span>
                </label>
                <label className="relative inline-flex items-center cursor-pointer">
                   <input
                     type="checkbox"
                     checked={legendDynamic}
                     onChange={(e) => setLegendDynamic(e.target.checked)}
                     className="sr-only peer"
                   />
                   <div className="w-9 h-5 bg-gray-300 rounded-full peer-checked:bg-green-500 peer-focus:ring-2 peer-focus:ring-green-300 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full" />
                </label>
             </div>
          </div>

          {/* Legend Toggle */}
          <div className="flex items-center justify-between pt-2 border-t border-slate-200">
            <span className="text-xs text-slate-600">{t('layerControl.colorLegend', 'Leyenda de colores')}</span>
            <button
              onClick={() => setShowLegend(!showLegend)}
              className="text-xs text-green-600 hover:text-green-700"
            >
              {showLegend ? t('layerControl.hide', 'Ocultar') : t('layerControl.show', 'Mostrar')}
            </button>
          </div>
        </div>
      </Card>

      {/* Color Scale Legend - Floating */}
      {showLegend && (
        <ColorScaleLegend
          indexType={legendIndexType}
          position="top-right"
          onClose={() => setShowLegend(false)}
          dynamic={legendDynamic}
          onDynamicToggle={setLegendDynamic}
          dataMin={undefined} 
          dataMax={undefined}
        />
      )}
    </>
  );
};

export default VegetationLayerControl;
