/**
 * Vegetation Layer Toggle - Simple on/off toggle for the layer-toggle slot.
 * The full control panel lives in the context-panel slot.
 */

import React from 'react';
import { Leaf } from 'lucide-react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useTranslation } from '@nekazari/sdk';

const VegetationLayerToggle: React.FC = () => {
  const { t } = useTranslation();
  const { selectedEntityId, selectedIndex, activeJobId, activeRasterPath, indexResults, layerVisible, setLayerVisible } = useVegetationContext();

  const hasLayer = !!(activeJobId || activeRasterPath || (selectedIndex && indexResults?.[selectedIndex]?.job_id));

  if (!selectedEntityId) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/90 border border-slate-200/50 shadow-sm pointer-events-auto">
      <Leaf className="w-4 h-4 text-green-600" />
      <span className="text-sm font-medium text-slate-700">{t('layerToggle.label')}</span>
      {hasLayer && (
        <label className="ml-auto relative inline-flex items-center cursor-pointer">
          <input
            type="checkbox"
            checked={layerVisible}
            onChange={(e) => setLayerVisible(e.target.checked)}
            className="sr-only peer"
          />
          <div className="w-9 h-5 bg-gray-300 rounded-full peer-checked:bg-green-500 peer-focus:ring-2 peer-focus:ring-green-300 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"
            title={layerVisible ? t('layerToggle.hide') : t('layerToggle.show')}
          />
        </label>
      )}
      {!hasLayer && (
        <span className="ml-auto text-xs text-slate-400">{t('layerToggle.noData')}</span>
      )}
    </div>
  );
};

export default VegetationLayerToggle;
