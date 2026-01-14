/**
 * Index Pill Selector - Visual pill buttons for selecting vegetation indices.
 * Groups indices by use case for better UX.
 */

import React from 'react';
import { Leaf, Droplet, Sun } from 'lucide-react';
import type { VegetationIndexType } from '../../types';

interface IndexGroup {
  category: string;
  icon: React.ReactNode;
  description: string;
  indices: Array<{
    value: VegetationIndexType;
    label: string;
    shortLabel: string;
  }>;
}

const INDEX_GROUPS: IndexGroup[] = [
  {
    category: 'Salud General',
    icon: <Leaf className="w-4 h-4" />,
    description: 'Índices para monitorización general de vegetación',
    indices: [
      { value: 'NDVI', label: 'NDVI - Normalized Difference Vegetation Index', shortLabel: 'NDVI' },
      { value: 'EVI', label: 'EVI - Enhanced Vegetation Index', shortLabel: 'EVI' },
    ],
  },
  {
    category: 'Nutrición/Clorofila',
    icon: <Droplet className="w-4 h-4" />,
    description: 'Índices para análisis de nutrición y clorofila',
    indices: [
      { value: 'GNDVI', label: 'GNDVI - Green Normalized Difference Vegetation Index', shortLabel: 'GNDVI' },
      { value: 'NDRE', label: 'NDRE - Normalized Difference Red Edge', shortLabel: 'NDRE' },
    ],
  },
  {
    category: 'Suelo/Agua',
    icon: <Sun className="w-4 h-4" />,
    description: 'Índices para etapas tempranas y análisis de suelo',
    indices: [
      { value: 'SAVI', label: 'SAVI - Soil-Adjusted Vegetation Index', shortLabel: 'SAVI' },
    ],
  },
];

interface IndexPillSelectorProps {
  selectedIndex: VegetationIndexType;
  onIndexChange: (index: VegetationIndexType) => void;
  showCustom?: boolean;
  className?: string;
}

export const IndexPillSelector: React.FC<IndexPillSelectorProps> = ({
  selectedIndex,
  onIndexChange,
  showCustom = false,
  className = '',
}) => {
  return (
    <div className={`space-y-4 ${className}`}>
      {INDEX_GROUPS.map((group) => (
        <div key={group.category}>
          <div className="flex items-center gap-2 mb-2">
            <div className="text-gray-500">{group.icon}</div>
            <h4 className="text-sm font-semibold text-gray-700">{group.category}</h4>
          </div>
          <p className="text-xs text-gray-500 mb-2">{group.description}</p>
          <div className="flex flex-wrap gap-2">
            {group.indices.map((index) => (
              <button
                key={index.value}
                onClick={() => onIndexChange(index.value)}
                className={`
                  px-3 py-1.5 rounded-full text-sm font-medium transition-all
                  ${
                    selectedIndex === index.value
                      ? 'bg-green-600 text-white shadow-md'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }
                `}
                title={index.label}
              >
                {index.shortLabel}
              </button>
            ))}
          </div>
        </div>
      ))}

      {showCustom && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <h4 className="text-sm font-semibold text-gray-700">Personalizado</h4>
          </div>
          <button
            onClick={() => onIndexChange('CUSTOM')}
            className={`
              px-3 py-1.5 rounded-full text-sm font-medium transition-all
              ${
                selectedIndex === 'CUSTOM'
                  ? 'bg-purple-600 text-white shadow-md'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }
            `}
          >
            CUSTOM
          </button>
        </div>
      )}
    </div>
  );
};














