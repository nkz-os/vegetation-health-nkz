/**
 * Color Scale Legend - Floating legend component for vegetation index visualization.
 * Shows color scale with value ranges and description.
 */

import React from 'react';
import { X } from 'lucide-react';
import { useIndexLegend } from '../../hooks/useIndexLegend';
import type { VegetationIndexType } from '../../types';

interface ColorScaleLegendProps {
  indexType: VegetationIndexType;
  position?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left';
  onClose?: () => void;
  className?: string;
  // Dynamic stretching options
  dynamic?: boolean;
  dataMin?: number;
  dataMax?: number;
  onDynamicToggle?: (enabled: boolean) => void;
}

export const ColorScaleLegend: React.FC<ColorScaleLegendProps> = ({
  indexType,
  position = 'top-right',
  onClose,
  className = '',
  dynamic = false,
  dataMin,
  dataMax,
  onDynamicToggle,
}) => {
  const { legend } = useIndexLegend(indexType, dynamic, dataMin, dataMax);

  const positionClasses = {
    'top-right': 'top-4 right-4',
    'top-left': 'top-4 left-4',
    'bottom-right': 'bottom-4 right-4',
    'bottom-left': 'bottom-4 left-4',
  };

  return (
    <div
      className={`
        absolute ${positionClasses[position]} 
        bg-white rounded-lg shadow-lg border border-gray-200 p-4 z-50
        min-w-[200px] ${className}
      `}
    >
      {onClose && (
        <button
          onClick={onClose}
          className="absolute top-2 right-2 text-gray-400 hover:text-gray-600 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      )}

      <div className="mb-2">
        <div className="flex items-center justify-between mb-1">
          <h4 className="text-sm font-semibold text-gray-900">{indexType}</h4>
          {onDynamicToggle && (
            <label className="flex items-center gap-1 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={dynamic}
                onChange={(e) => onDynamicToggle(e.target.checked)}
                className="rounded border-nkz-border text-green-600 focus:ring-green-500"
              />
              <span className="text-gray-600">Auto-stretch</span>
            </label>
          )}
        </div>
        <p className="text-xs text-gray-500">{legend.description}</p>
        {dynamic && dataMin !== undefined && dataMax !== undefined && (
          <p className="text-xs text-nkz-info mt-1">
            Rango dinámico: {dataMin.toFixed(3)} - {dataMax.toFixed(3)}
          </p>
        )}
      </div>

      {/* Color Scale */}
      <div className="relative h-6 w-full rounded overflow-hidden mb-2 border border-nkz-border">
        <div
          className="absolute inset-0"
          style={{
            background: `linear-gradient(to right, 
              ${legend.stops[0].color} 0%, 
              ${legend.stops[1].color} 20%, 
              ${legend.stops[2].color} 40%, 
              ${legend.stops[3].color} 60%, 
              ${legend.stops[4].color} 80%, 
              ${legend.stops[5].color} 100%)`,
          }}
        />
      </div>

      {/* Value Labels */}
      <div className="flex justify-between text-xs text-gray-600">
        <span>{legend.min.toFixed(1)}</span>
        <span>{legend.max.toFixed(1)}</span>
      </div>

      {/* Stop Labels */}
      <div className="mt-2 space-y-1">
        {legend.stops.slice(1, -1).map((stop, idx) => (
          <div key={idx} className="flex items-center gap-2 text-xs">
            <div
              className="w-4 h-4 rounded border border-nkz-border"
              style={{ backgroundColor: stop.color }}
            />
            <span className="text-gray-600">{stop.label}</span>
            <span className="text-gray-400 ml-auto">({stop.value.toFixed(1)})</span>
          </div>
        ))}
      </div>
    </div>
  );
};

