/**
 * Vegetation Layer Toggle - Simple on/off toggle for the layer-toggle slot.
 * The full control panel lives in the context-panel slot.
 */

import React from 'react';
import { Leaf } from 'lucide-react';
import { SlotShellCompact } from '@nekazari/viewer-kit';
import { Toggle } from '@nekazari/ui-kit';
import { useVegetationContext } from '../../services/vegetationContext';
import { useTranslation } from '@nekazari/sdk';

const vegetationAccent = { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' };

const VegetationLayerToggle: React.FC = () => {
  const { t } = useTranslation();
  const { selectedEntityId, selectedIndex, activeJobId, activeRasterPath, indexResults, layerVisible, setLayerVisible } = useVegetationContext();

  const hasLayer = !!(activeJobId || activeRasterPath || (selectedIndex && indexResults?.[selectedIndex]?.job_id));

  if (!selectedEntityId) return null;

  return (
    <SlotShellCompact moduleId="vegetation-prime" accent={vegetationAccent}>
      <div className="flex items-center gap-nkz-inline">
        <Leaf className="w-4 h-4 text-nkz-accent-base" />
        <Toggle
          checked={layerVisible}
          onChange={setLayerVisible}
          label={t('layerToggle.label')}
          disabled={!hasLayer}
        />
        {!hasLayer && (
          <span className="text-nkz-xs text-nkz-text-muted">{t('layerToggle.noData')}</span>
        )}
      </div>
    </SlotShellCompact>
  );
};

export default VegetationLayerToggle;
