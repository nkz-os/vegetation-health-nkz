import React, { createContext, useContext, useState, ReactNode, useCallback, useEffect, useRef, useSyncExternalStore } from 'react';
import { useViewerOptional } from '@nekazari/sdk';
import type { VegetationIndexType } from '../types';

interface DateRange {
  startDate: Date | null;
  endDate: Date | null;
}

interface IndexResult {
  job_id: string;
  index_type: string;
  statistics: {
    mean: number | null;
    min: number | null;
    max: number | null;
    std_dev: number | null;
    pixel_count: number | null;
  };
  raster_path: string | null;
  is_composite: boolean;
  created_at: string | null;
}

// =============================================================================
// Global Shared Store — singleton across all VegetationProvider instances
// =============================================================================
// Each slot (map-layer, context-panel, bottom-panel, layer-toggle) gets its own
// VegetationProvider instance. This store synchronizes shared state between them.

interface SharedState {
  selectedIndex: VegetationIndexType | null;
  activeJobId: string | null;
  activeRasterPath: string | null;
  indexResults: Record<string, IndexResult>;
  selectedSceneId: string | null;
  selectedDate: Date | null;
  layerOpacity: number; // 0-100
  layerVisible: boolean;
}

interface VegetationStore {
  state: SharedState;
  _listeners: Set<() => void>;
  _version: number;
}

const STORE_KEY = '__vegetationPrimeStore';

function getStore(): VegetationStore {
  if (!(window as any)[STORE_KEY]) {
    (window as any)[STORE_KEY] = {
      state: {
        selectedIndex: null,
        activeJobId: null,
        activeRasterPath: null,
        indexResults: {},
        selectedSceneId: null,
        selectedDate: null,
        layerOpacity: 75,
        layerVisible: true,
      },
      _listeners: new Set(),
      _version: 0,
    } as VegetationStore;
  }
  return (window as any)[STORE_KEY];
}

function updateStore(partial: Partial<SharedState>) {
  const store = getStore();
  store.state = { ...store.state, ...partial };
  store._version++;
  store._listeners.forEach(l => l());
}

function subscribeStore(listener: () => void): () => void {
  const store = getStore();
  store._listeners.add(listener);
  return () => { store._listeners.delete(listener); };
}

function getStoreSnapshot(): SharedState {
  return getStore().state;
}

// =============================================================================
// VegetationContext — local context for each provider instance
// =============================================================================

interface VegetationContextType {
  selectedEntityId: string | null;
  selectedSceneId: string | null;
  selectedIndex: VegetationIndexType | null;
  selectedDate: Date | null;
  dateRange: DateRange;
  selectedGeometry?: any | null;
  activeJobId: string | null;
  activeRasterPath: string | null;
  indexResults: Record<string, IndexResult>;
  layerOpacity: number;
  layerVisible: boolean;
  setSelectedEntityId: (id: string | null) => void;
  setSelectedSceneId: (id: string | null) => void;
  setSelectedIndex: (index: VegetationIndexType | null) => void;
  setSelectedDate: (date: Date | null) => void;
  setDateRange: (range: DateRange) => void;
  setActiveJobId: (id: string | null) => void;
  setActiveRasterPath: (path: string | null) => void;
  setIndexResults: (results: Record<string, IndexResult>) => void;
  setLayerOpacity: (opacity: number) => void;
  setLayerVisible: (visible: boolean) => void;
  resetContext: () => void;
}

const VegetationContext = createContext<VegetationContextType | undefined>(undefined);

export const VegetationProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  // Read shared state from the global store (reactive across all provider instances)
  const sharedState = useSyncExternalStore(subscribeStore, getStoreSnapshot);

  // Local-only state
  const [selectedEntityId, setSelectedEntityIdLocal] = useState<string | null>(null);
  const [selectedGeometry, setSelectedGeometry] = useState<any | null>(null);
  const [dateRange, setDateRange] = useState<DateRange>({
    startDate: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000),
    endDate: new Date(),
  });

  // Shared state setters — update the global store so all instances sync
  const setSelectedIndex = useCallback((index: VegetationIndexType | null) => {
    updateStore({ selectedIndex: index });
  }, []);

  const setActiveJobId = useCallback((id: string | null) => {
    updateStore({ activeJobId: id });
  }, []);

  const setActiveRasterPath = useCallback((path: string | null) => {
    updateStore({ activeRasterPath: path });
  }, []);

  const setIndexResults = useCallback((results: Record<string, IndexResult>) => {
    updateStore({ indexResults: results });
  }, []);

  const setSelectedSceneId = useCallback((id: string | null) => {
    updateStore({ selectedSceneId: id });
  }, []);

  const setSelectedDate = useCallback((date: Date | null) => {
    updateStore({ selectedDate: date });
  }, []);

  const setLayerOpacity = useCallback((opacity: number) => {
    updateStore({ layerOpacity: opacity });
  }, []);

  const setLayerVisible = useCallback((visible: boolean) => {
    updateStore({ layerVisible: visible });
  }, []);

  // Wrapper for setSelectedEntityId (local + clear shared state on change)
  const setSelectedEntityId = useCallback((id: string | null) => {
    setSelectedEntityIdLocal(id);
    if (!id) {
      setSelectedGeometry(null);
    }
  }, []);

  // ==========================================================================
  // Sync entity selection from host ViewerContext (unified viewer page)
  // ==========================================================================
  const hostViewer = useViewerOptional();
  const prevHostEntityRef = useRef<string | null>(null);

  useEffect(() => {
    if (!hostViewer) return;

    const hostEntityId = hostViewer.selectedEntityId || null;

    if (hostEntityId !== prevHostEntityRef.current) {
      prevHostEntityRef.current = hostEntityId;

      if (hostEntityId) {
        setSelectedEntityIdLocal(hostEntityId);
        updateStore({ selectedSceneId: null });
        if (!sharedState.selectedIndex) {
          updateStore({ selectedIndex: 'NDVI' });
        }
      } else {
        setSelectedEntityIdLocal(null);
        setSelectedGeometry(null);
      }
    }
  }, [hostViewer?.selectedEntityId, sharedState.selectedIndex]);

  // Sync date from host viewer if available
  useEffect(() => {
    if (!hostViewer?.currentDate) return;
    updateStore({ selectedDate: new Date(hostViewer.currentDate) });
  }, [hostViewer?.currentDate]);

  // Fallback: listen for custom events (module's own page, outside unified viewer)
  useEffect(() => {
    const handleEntitySelected = (event: CustomEvent<{ entityId: string | null, type?: string, geometry?: any }>) => {
      if (event.detail?.entityId) {
        setSelectedEntityIdLocal(event.detail.entityId);
        updateStore({ selectedSceneId: null });
        setSelectedGeometry(event.detail.geometry || null);
        if (!getStoreSnapshot().selectedIndex) {
          updateStore({ selectedIndex: 'NDVI' });
        }
      }
    };

    window.addEventListener('nekazari:entity-selected', handleEntitySelected as EventListener);
    return () => {
      window.removeEventListener('nekazari:entity-selected', handleEntitySelected as EventListener);
    };
  }, []);

  const resetContext = useCallback(() => {
    setSelectedEntityIdLocal(null);
    setSelectedGeometry(null);
    updateStore({
      selectedIndex: null,
      activeJobId: null,
      activeRasterPath: null,
      indexResults: {},
      selectedSceneId: null,
      selectedDate: null,
      layerOpacity: 75,
      layerVisible: true,
    });
    setDateRange({
      startDate: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000),
      endDate: new Date(),
    });
  }, []);

  return (
    <VegetationContext.Provider
      value={{
        selectedEntityId,
        selectedSceneId: sharedState.selectedSceneId,
        selectedIndex: sharedState.selectedIndex,
        selectedDate: sharedState.selectedDate,
        dateRange,
        selectedGeometry,
        activeJobId: sharedState.activeJobId,
        activeRasterPath: sharedState.activeRasterPath,
        indexResults: sharedState.indexResults,
        layerOpacity: sharedState.layerOpacity,
        layerVisible: sharedState.layerVisible,
        setSelectedEntityId,
        setSelectedSceneId,
        setSelectedIndex,
        setSelectedDate,
        setDateRange,
        setActiveJobId,
        setActiveRasterPath,
        setIndexResults,
        setLayerOpacity,
        setLayerVisible,
        resetContext,
      }}
    >
      {children}
    </VegetationContext.Provider>
  );
};

export const useVegetationContext = () => {
  const context = useContext(VegetationContext);
  if (context === undefined) {
    throw new Error('useVegetationContext must be used within a VegetationProvider');
  }
  return context;
};
