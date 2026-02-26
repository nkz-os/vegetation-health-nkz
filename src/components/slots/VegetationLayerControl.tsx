/**
 * Vegetation Layer Control - Slot component.
 */

import React, { useState, useEffect } from 'react';
import { Layers, Cloud } from 'lucide-react';
import { useUIKit } from '../../hooks/useUIKit';
import { useViewer } from '@nekazari/sdk';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationScenes } from '../../hooks/useVegetationScenes';
import { VegetationIndexType } from '../../types';
import { IndexPillSelector } from '../widgets/IndexPillSelector'; 
import { ColorScaleLegend } from '../widgets/ColorScaleLegend';
import DateSelector from '../widgets/DateSelector';

const VegetationLayerControl: React.FC = () => {
  // Get UI components safely from Host
  const { Card } = useUIKit();
  const { setCurrentDate } = useViewer(); 
  
  // Get Module Context
  const {
    selectedIndex,
    selectedDate,
    selectedEntityId,
    selectedSceneId,
    setSelectedIndex,
    setSelectedDate,
    setSelectedSceneId,
  } = useVegetationContext();

  // Load scenes for current entity
  const { scenes, loading: scenesLoading } = useVegetationScenes({ 
    entityId: selectedEntityId || undefined 
  });
  
  // Local state for UI controls
  const [showCloudMask, setShowCloudMask] = useState(false);
  const [opacity, setOpacity] = useState(100);
  const [showLegend, setShowLegend] = useState(true);
  
  // Helpers
  const currentScene = scenes.find(s => s.id === selectedSceneId);
  const cloudCoverage = currentScene?.cloud_coverage ?? 0;
  
  const [legendDynamic, setLegendDynamic] = useState(false);

  // Effect to sync viewer date
  useEffect(() => {
    // selectedDate is Date | null in context
    if (selectedDate && setCurrentDate) {
      // setCurrentDate expects Date
      setCurrentDate(selectedDate);
    }
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
          <p>Selecciona una parcela para ver capas</p>
        </div>
      </Card>
    );
  }

  return (
    <>
      <Card padding="md" className="bg-white/90 backdrop-blur-md border border-slate-200/50 rounded-xl w-80 shadow-lg pointer-events-auto">
        <div className="space-y-4">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-100 pb-2">
            <h3 className="font-semibold text-slate-800 flex items-center gap-2">
              <Layers className="w-4 h-4 text-green-600" />
              Capas de Vegetación
            </h3>
            <div className="flex items-center gap-1">
              <span className="text-xs text-slate-500 font-mono">
                 {selectedEntityId.split(':').pop()}
              </span>
            </div>
          </div>

          {/* Index Selector */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-slate-600 uppercase tracking-wider">Índice Espectral</label>
            <IndexPillSelector 
              selectedIndex={(selectedIndex || 'NDVI') as VegetationIndexType} 
              onIndexChange={(idx: any) => setSelectedIndex(idx)} 
            />
          </div>

          {/* Date Selector */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-slate-600 uppercase tracking-wider flex justify-between">
              Fecha de Imagen
              {scenesLoading && <span className="text-slate-400">Cargando...</span>}
            </label>
            <DateSelector 
              selectedSceneId={selectedSceneId}
              scenes={scenes}
              onSelect={handleDateChange}
            />
            
            {/* Scene Info */}
            {currentScene && (
              <div className="flex items-center gap-4 text-xs text-slate-500 mt-1 bg-slate-50 p-1.5 rounded border border-slate-100">
                <div className="flex items-center gap-1" title="Cobertura de nubes">
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
               <span>Opacidad</span>
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
                   <span>Máscara de Nubes</span>
                   <span className="text-xs text-slate-400">Ocultar áreas nubladas</span>
                </label>
                <input 
                   type="checkbox" 
                   checked={showCloudMask} 
                   onChange={(e) => setShowCloudMask(e.target.checked)}
                   className="toggle toggle-sm toggle-success"
                />
             </div>
            
             <div className="flex items-center justify-between mt-2">
                <label className="text-sm text-slate-700 flex flex-col">
                   <span>Rango Dinámico</span>
                   <span className="text-xs text-slate-400">Ajustar color a min/max</span>
                </label>
                <input 
                   type="checkbox" 
                   checked={legendDynamic} 
                   onChange={(e) => setLegendDynamic(e.target.checked)}
                   className="toggle toggle-sm toggle-success"
                />
             </div>
          </div>

          {/* Legend Toggle */}
          <div className="flex items-center justify-between pt-2 border-t border-slate-200">
            <span className="text-xs text-slate-600">Leyenda de colores</span>
            <button
              onClick={() => setShowLegend(!showLegend)}
              className="text-xs text-green-600 hover:text-green-700"
            >
              {showLegend ? 'Ocultar' : 'Mostrar'}
            </button>
          </div>
        </div>
      </Card>

      {/* Color Scale Legend - Floating */}
      {showLegend && (
        <ColorScaleLegend
          indexType={(selectedIndex || 'NDVI') as VegetationIndexType}
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
