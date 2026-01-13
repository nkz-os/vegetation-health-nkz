/**
 * Slot Registration for Vegetation Prime Module
 * Defines all slots that integrate with the Unified Viewer.
 * IMPORTANT: All imports must be from ../exports/ to get VegetationProvider wrapping!
 */

// Import WRAPPED components from exports (each has its own VegetationProvider)
import { TimelineWidget } from '../exports/TimelineWidget';
import { VegetationConfig } from '../exports/VegetationConfig';
import { VegetationAnalytics } from '../exports/VegetationAnalytics';
import { VegetationLayerControl } from '../exports/VegetationLayerControl';
import { VegetationLayer } from '../exports/VegetationLayer';

// Type definitions for slot widgets (matching SDK types)
export interface SlotWidgetDefinition {
  id: string;
  component: string;
  priority: number;
  localComponent: React.ComponentType<any>;
  defaultProps?: Record<string, any>;
  showWhen?: {
    entityType?: string[];
    layerActive?: string[];
  };
}

export type SlotType = 'layer-toggle' | 'context-panel' | 'bottom-panel' | 'entity-tree' | 'map-layer';

export type ModuleViewerSlots = Record<SlotType, SlotWidgetDefinition[]> & {
  moduleProvider?: React.ComponentType<any>;
};

/**
 * Vegetation Prime Slots Configuration
 * These slots integrate the module into the Unified Viewer
 */
export const vegetationPrimeSlots: ModuleViewerSlots = {
  // Map Layer: Logic that interacts with Cesium directly (Raster/Vector)
  'map-layer': [
    {
      id: 'vegetation-cesium-layer',
      component: 'VegetationLayer',
      priority: 10,
      localComponent: VegetationLayer, // Now wrapped with VegetationProvider
      showWhen: {
        entityType: ['AgriParcel']
      }
    }
  ],
  // Layer Toggle: UI Controls (Legend, Opacity)
  'layer-toggle': [
    {
      id: 'vegetation-layer-control',
      component: 'VegetationLayerControl',
      priority: 10,
      localComponent: VegetationLayerControl, // Now wrapped with VegetationProvider
      defaultProps: { visible: true },
      showWhen: {
        entityType: ['AgriParcel']
      }
    }
  ],
  'context-panel': [
    {
      id: 'vegetation-config',
      component: 'VegetationConfig',
      priority: 20,
      localComponent: VegetationConfig, // Now wrapped with VegetationProvider
      defaultProps: { mode: 'panel' },
      showWhen: {
        entityType: ['AgriParcel']
      }
    },
    {
      id: 'vegetation-analytics',
      component: 'VegetationAnalytics',
      priority: 30,
      localComponent: VegetationAnalytics, // Now wrapped with VegetationProvider
      showWhen: {
        entityType: ['AgriParcel']
      }
    }
  ],
  'bottom-panel': [
    {
      id: 'vegetation-timeline',
      component: 'TimelineWidget',
      priority: 10,
      localComponent: TimelineWidget // Now wrapped with VegetationProvider
    }
  ],
  'entity-tree': []
  // moduleProvider removed - each component is now self-contained
};

/**
 * Export as viewerSlots for host integration
 */
export const viewerSlots = vegetationPrimeSlots;
export default vegetationPrimeSlots;
