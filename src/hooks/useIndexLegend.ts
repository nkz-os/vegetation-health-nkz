/**
 * Hook for managing color scale legend for vegetation indices.
 * Provides color mapping and legend configuration for each index type.
 */

import { useMemo } from 'react';
import type { VegetationIndexType } from '../types';

interface ColorStop {
  value: number;
  color: string;
  label: string;
}

interface LegendConfig {
  min: number;
  max: number;
  stops: ColorStop[];
  description: string;
  dynamic?: boolean;
  dataMin?: number;
  dataMax?: number;
}

export function getIndexColor(value: number, _indexType: VegetationIndexType): string {
  const normalized = Math.max(0, Math.min(1, (value + 1) / 2));
  if (normalized < 0.2) return `rgb(${255}, ${Math.floor(255 * (normalized / 0.2))}, 0)`;
  if (normalized < 0.5) {
    const t = (normalized - 0.2) / 0.3;
    return `rgb(255, ${Math.floor(255 * (1 - t * 0.5))}, 0)`;
  }
  if (normalized < 0.7) {
    const t = (normalized - 0.5) / 0.2;
    return `rgb(${Math.floor(255 * (1 - t))}, 255, ${Math.floor(100 * t)})`;
  }
  const t = (normalized - 0.7) / 0.3;
  return `rgb(0, ${Math.floor(200 + 55 * t)}, ${Math.floor(100 * t)})`;
}

export function getIndexLegend(
  indexType: VegetationIndexType,
  dynamic: boolean = false,
  dataMin?: number,
  dataMax?: number
): LegendConfig {
  const baseStops: ColorStop[] = [
    { value: -1, color: '#8B0000', label: 'No Vegetation' },
    { value: 0, color: '#FFD700', label: 'Low' },
    { value: 0.3, color: '#FFA500', label: 'Moderate' },
    { value: 0.5, color: '#ADFF2F', label: 'Good' },
    { value: 0.7, color: '#32CD32', label: 'High' },
    { value: 1, color: '#006400', label: 'Very High' },
  ];

  const descriptions: Record<VegetationIndexType, string> = {
    NDVI: 'Normalized Difference Vegetation Index - Measures vegetation health and density',
    EVI: 'Enhanced Vegetation Index - Reduces atmospheric and soil effects',
    SAVI: 'Soil-Adjusted Vegetation Index - Best for areas with exposed soil',
    GNDVI: 'Green Normalized Difference Vegetation Index - Sensitive to chlorophyll content',
    NDRE: 'Normalized Difference Red Edge - Sensitive to crop stress and nitrogen',
    NDMI: 'Normalized Difference Moisture Index - Measures vegetation water content',
    VRA_ZONES: 'Variable Rate Application Zones - AI-clustered management zones',
    CUSTOM: 'Custom Index - User-defined formula',
  };

  const effectiveMin = dynamic && dataMin !== undefined ? dataMin : -1;
  const effectiveMax = dynamic && dataMax !== undefined ? dataMax : 1;

  const dynamicStops = dynamic
    ? baseStops.map((stop) => ({
        ...stop,
        value: effectiveMin + (stop.value + 1) * ((effectiveMax - effectiveMin) / 2),
      }))
    : baseStops;

  return {
    min: effectiveMin,
    max: effectiveMax,
    stops: dynamicStops,
    description: descriptions[indexType],
    dynamic,
    dataMin: dynamic ? dataMin : undefined,
    dataMax: dynamic ? dataMax : undefined,
  };
}

export function useIndexLegend(
  indexType: VegetationIndexType,
  dynamic: boolean = false,
  dataMin?: number,
  dataMax?: number
) {
  const legend = useMemo(
    () => getIndexLegend(indexType, dynamic, dataMin, dataMax),
    [indexType, dynamic, dataMin, dataMax]
  );

  const getColor = useMemo(
    () => (value: number) => {
      if (dynamic && dataMin !== undefined && dataMax !== undefined) {
        const normalized = (value - dataMin) / (dataMax - dataMin || 1);
        const clamped = Math.max(0, Math.min(1, normalized));
        return getIndexColor(clamped * 2 - 1, indexType);
      }
      return getIndexColor(value, indexType);
    },
    [indexType, dynamic, dataMin, dataMax]
  );

  return { legend, getColor };
}
