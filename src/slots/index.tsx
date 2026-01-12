/**
 * Slot Registration for Vegetation Prime Module
 * Defines all slots that integrate with the Unified Viewer.
 * Uses shared moduleProvider pattern supported by Host.
 */

import { TimelineWidget } from '../components/slots/TimelineWidget';
import { VegetationConfig } from '../components/VegetationConfig';
import { VegetationAnalytics } from '../components/VegetationAnalytics';
import { VegetationProvider } from '../services/vegetationContext';
import VegetationLayerControl from '../components/slots/VegetationLayerControl';
import { VegetationLayer } from '../components/slots/VegetationLayer'; // Map Logic (Cesium)

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
      localComponent: VegetationLayer,
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
      localComponent: VegetationLayerControl,
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
      localComponent: VegetationConfig,
      defaultProps: { mode: 'panel' },
      showWhen: {
        entityType: ['AgriParcel']
      }
    },
    {
      id: 'vegetation-analytics',
      component: 'VegetationAnalytics',
      priority: 30,
      localComponent: VegetationAnalytics,
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
      localComponent: TimelineWidget
    }
  ],
  'entity-tree': [],
  
  // SHARED PROVIDER: Host will wrap all widgets with this provider
  moduleProvider: VegetationProvider
};

/**
 * Export as viewerSlots for host integration
 */
export const viewerSlots = vegetationPrimeSlots;
export default vegetationPrimeSlots;
