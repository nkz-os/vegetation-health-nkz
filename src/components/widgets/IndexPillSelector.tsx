/**
 * Index Pill Selector — Compact vegetation index pills.
 *
 * Two groups: "Vegetación" (NDVI, EVI, SAVI, GNDVI, NDRE) and "Manejo" (VRA_ZONES).
 * Custom formulas rendered inline after standard indices.
 * Supports compact mode (flat row, no group labels) for bottom-panel slots.
 */

import React, { useState } from 'react';
import { Info } from 'lucide-react';
import { useTranslation } from '@nekazari/sdk';
import type { VegetationIndexType } from '../../types';
import { IndexInfoModal } from './IndexInfoModal';

export interface CustomIndexOption {
  key: string;
  label: string;
}

const INDEX_GROUPS: Array<{
  labelKey: string;
  indices: Array<{ value: VegetationIndexType; shortLabel: string; color: string }>;
}> = [
  {
    labelKey: 'indices.categoryGeneral',
    indices: [
      { value: 'NDVI', shortLabel: 'NDVI', color: 'bg-green-600' },
      { value: 'EVI', shortLabel: 'EVI', color: 'bg-emerald-500' },
      { value: 'SAVI', shortLabel: 'SAVI', color: 'bg-lime-500' },
      { value: 'GNDVI', shortLabel: 'GNDVI', color: 'bg-teal-500' },
      { value: 'NDRE', shortLabel: 'NDRE', color: 'bg-cyan-600' },
    ],
  },
  {
    labelKey: 'indices.categoryManagement',
    indices: [
      { value: 'VRA_ZONES', shortLabel: 'VRA', color: 'bg-purple-600' },
    ],
  },
];

interface IndexPillSelectorProps {
  selectedIndex: string;
  onIndexChange: (index: string) => void;
  customIndexOptions?: CustomIndexOption[];
  /** Indices that have data available. Unavailable pills render dimmed with tooltip. */
  availableIndices?: string[];
  /** Flat row without group labels — for bottom-panel slots */
  compact?: boolean;
  className?: string;
}

export const IndexPillSelector: React.FC<IndexPillSelectorProps> = ({
  selectedIndex,
  onIndexChange,
  customIndexOptions = [],
  availableIndices,
  compact = false,
  className = '',
}) => {
  const { t } = useTranslation();
  const [infoModalIndex, setInfoModalIndex] = useState<string | null>(null);

  const allIndices = INDEX_GROUPS.flatMap(g => g.indices);

  const renderPill = (value: string, shortLabel: string, colorClass?: string) => {
    const isSelected = selectedIndex === value;
    const isCustom = value.startsWith('custom:');
    const isAvailable = !availableIndices || availableIndices.includes(value);

    return (
      <button
        key={value}
        onClick={() => isAvailable && onIndexChange(value)}
        disabled={!isAvailable}
        className={`
          relative px-3 py-1.5 rounded-full text-xs font-semibold transition-all whitespace-nowrap
          ${!isAvailable
            ? 'bg-slate-50 text-slate-300 cursor-not-allowed'
            : isSelected
              ? (isCustom ? 'bg-purple-600 text-white shadow-md' : 'bg-green-600 text-white shadow-md')
              : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
          }
        `}
        title={isAvailable ? shortLabel : `${shortLabel} — ${t('layerControl.indexNotAvailable', 'No data available')}`}
      >
        {!isSelected && colorClass && !isCustom && (
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${colorClass} mr-1.5 align-middle ${isAvailable ? 'opacity-60' : 'opacity-20'}`} />
        )}
        {isSelected && !isCustom && (
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-white mr-1.5 align-middle" />
        )}
        {shortLabel}
      </button>
    );
  };

  if (compact) {
    return (
      <div className={`flex flex-wrap items-center gap-1.5 ${className}`}>
        {allIndices.map(idx => renderPill(idx.value, idx.shortLabel))}
        {customIndexOptions.map(opt => renderPill(opt.key, opt.label))}
        {allIndices.map(idx => (
          <button
            key={`info-${idx.value}`}
            onClick={(e) => { e.stopPropagation(); setInfoModalIndex(idx.value); }}
            className="p-0.5 text-slate-300 hover:text-slate-500 rounded-full transition-colors"
            title={t('indices.infoTooltip')}
          >
            <Info className="w-3 h-3" />
          </button>
        ))}
        <IndexInfoModal
          indexType={infoModalIndex || ''}
          isOpen={!!infoModalIndex}
          onClose={() => setInfoModalIndex(null)}
        />
      </div>
    );
  }

  return (
    <div className={`space-y-3 ${className}`}>
      {INDEX_GROUPS.map((group) => (
        <div key={group.labelKey}>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
              {t(group.labelKey)}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {group.indices.map(idx => renderPill(idx.value, idx.shortLabel))}
            {group.indices.map(idx => (
              <button
                key={`info-${idx.value}`}
                onClick={(e) => { e.stopPropagation(); setInfoModalIndex(idx.value); }}
                className="p-0.5 text-slate-300 hover:text-slate-500 rounded-full transition-colors ml-0.5"
                title={t('indices.infoTooltip')}
              >
                <Info className="w-3 h-3" />
              </button>
            ))}
          </div>
        </div>
      ))}

      {customIndexOptions.length > 0 && (
        <div>
          <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
            {t('analyticsPage.customIndex')}
          </span>
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {customIndexOptions.map(opt => renderPill(opt.key, opt.label))}
          </div>
        </div>
      )}

      <IndexInfoModal
        indexType={infoModalIndex || ''}
        isOpen={!!infoModalIndex}
        onClose={() => setInfoModalIndex(null)}
      />
    </div>
  );
};
