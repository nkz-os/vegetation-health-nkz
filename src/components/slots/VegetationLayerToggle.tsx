import React from 'react';
import { Leaf } from 'lucide-react';
import { LayerMenuRow } from '@nekazari/module-kit';
import { useVegetationContext } from '../../services/vegetationContext';
import { useTranslation } from '@nekazari/sdk';

const vegetationAccent = { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' };

const AVAILABLE_INDICES = ['NDVI', 'NDRE', 'NDMI', 'SAVI', 'EVI'];

const VegetationLayerToggle: React.FC = () => {
  const { t } = useTranslation();
  const {
    selectedEntityId,
    selectedIndex, setSelectedIndex,
    activeJobId, activeRasterPath,
    indexResults, entityDataStatus,
    layerVisible, setLayerVisible,
    layerScope, setLayerScope,
    layerOpacity, setLayerOpacity,
  } = useVegetationContext();

  const hasSelectedData = !!(activeJobId || activeRasterPath || (selectedIndex && indexResults?.[selectedIndex]?.job_id));
  const hasAnyDataInTenant = entityDataStatus?.has_any_data;

  const disabledReason = (() => {
    if (layerScope === 'selected' && !selectedEntityId) return t('layerToggle.needsSelection', 'Selecciona una parcela o cambia a Todas');
    if (layerScope === 'selected' && !hasSelectedData) return t('layerToggle.noData', 'Sin datos');
    if (layerScope === 'all' && !hasAnyDataInTenant) return t('layerToggle.noTenantData', 'Aún sin datos en el tenant');
    return undefined;
  })();

  return (
    <LayerMenuRow
      moduleId="vegetation-prime"
      accent={vegetationAccent}
      icon={<Leaf className="w-4 h-4" />}
      title={t('module.name', 'Vegetation')}
      enabled={layerVisible}
      onToggle={setLayerVisible}
      scope={layerScope}
      onScopeChange={setLayerScope}
      disabledReason={disabledReason}
      scopeLabel={t('layerToggle.scope', 'Ámbito')}
      selectedLabel={t('layerToggle.selected', 'Seleccionada')}
      allLabel={t('layerToggle.all', 'Todas')}
      opacityLabel={t('layerToggle.opacity', 'Opacidad')}
      mode={
        <div className="flex flex-wrap gap-nkz-tight">
          {AVAILABLE_INDICES.map(idx => (
            <button
              key={idx}
              type="button"
              aria-pressed={selectedIndex === idx}
              onClick={() => setSelectedIndex(idx)}
              className={`px-nkz-inline py-nkz-tight text-nkz-xs rounded-nkz-md transition-colors ${
                selectedIndex === idx
                  ? 'bg-nkz-accent-soft text-nkz-accent-strong'
                  : 'bg-nkz-surface-sunken text-nkz-text-muted hover:bg-nkz-surface'
              }`}
            >
              {idx}
            </button>
          ))}
        </div>
      }
      opacity={layerOpacity ?? 75}
      onOpacityChange={setLayerOpacity}
    />
  );
};

export default VegetationLayerToggle;
