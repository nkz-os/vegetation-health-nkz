/**
 * Slot Registration for Vegetation Prime Module
 * Defines all slots that integrate with the Unified Viewer.
 * 
 * All widgets include explicit moduleId for proper host integration.
 */

// Import UNWRAPPED components (Host's SlotRenderer will wrap with moduleProvider)
import { TimelineWidget } from '../components/slots/TimelineWidget';
import { VegetationConfig } from '../components/VegetationConfig';
import { VegetationAnalytics } from '../components/VegetationAnalytics';
import VegetationLayerControl from '../components/slots/VegetationLayerControl';
import { VegetationLayer } from '../components/slots/VegetationLayer';
import { VegetationProvider } from '../services/vegetationContext';

// Module identifier - used for all slot widgets
const MODULE_ID = 'vegetation-prime';

// Type definitions for slot widgets (matching SDK types)
export interface SlotWidgetDefinition {
  id: string;
  /** Module ID that owns this widget. REQUIRED for remote modules. */
  moduleId: string;
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
  moduleProvider?: React.ComponentType<{ children: React.ReactNode }>;
};

/**
 * Vegetation Prime Slots Configuration
 * All widgets explicitly declare moduleId for proper provider wrapping.
 */
export const vegetationPrimeSlots: ModuleViewerSlots = {
  'map-layer': [
    {
      id: 'vegetation-prime-cesium-layer',
      moduleId: MODULE_ID,
      component: 'VegetationLayer',
      priority: 10,
      localComponent: VegetationLayer
    }
  ],
  'layer-toggle': [
    {
      id: 'vegetation-prime-layer-control',
      moduleId: MODULE_ID,
      component: 'VegetationLayerControl',
      priority: 10,
      localComponent: VegetationLayerControl,
      defaultProps: { visible: true },
      showWhen: { entityType: ['AgriParcel'] }
    }
  ],
  'context-panel': [
    {
      id: 'vegetation-prime-config',
      moduleId: MODULE_ID,
      component: 'VegetationConfig',
      priority: 20,
      localComponent: VegetationConfig,
      defaultProps: { mode: 'panel' },
      showWhen: { entityType: ['AgriParcel'] }
    },
    {
      id: 'vegetation-prime-analytics',
      moduleId: MODULE_ID,
      component: 'VegetationAnalytics',
      priority: 30,
      localComponent: VegetationAnalytics,
      showWhen: { entityType: ['AgriParcel'] }
    }
  ],
  'bottom-panel': [
    {
      id: 'vegetation-prime-timeline',
      moduleId: MODULE_ID,
      component: 'TimelineWidget',
      priority: 10,
      localComponent: TimelineWidget
    }
  ],
  'entity-tree': [],

  // Host's SlotRenderer wraps all widgets with this provider
  moduleProvider: VegetationProvider
};

/**
 * Export as viewerSlots for host integration
 */
export const viewerSlots = vegetationPrimeSlots;
export default vegetationPrimeSlots;
