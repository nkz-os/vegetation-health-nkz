import React, { createContext, useContext, useState, ReactNode, useCallback, useEffect, useRef } from 'react';
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

interface VegetationContextType {
  selectedEntityId: string | null;
  selectedSceneId: string | null;
  selectedIndex: VegetationIndexType | null;
  selectedDate: Date | null;
  dateRange: DateRange;
  selectedGeometry?: any | null;
  activeJobId: string | null;
  indexResults: Record<string, IndexResult>;
  setSelectedEntityId: (id: string | null) => void;
  setSelectedSceneId: (id: string | null) => void;
  setSelectedIndex: (index: VegetationIndexType | null) => void;
  setSelectedDate: (date: Date | null) => void;
  setDateRange: (range: DateRange) => void;
  setActiveJobId: (id: string | null) => void;
  setIndexResults: (results: Record<string, IndexResult>) => void;
  resetContext: () => void;
}

const VegetationContext = createContext<VegetationContextType | undefined>(undefined);

/**
 * Hook to read selectedEntityId from the host ViewerContext.
 * The host exposes its React Context via window.__nekazariViewerContextInstance.
 * This is the ONLY reliable way to get entity selection in IIFE modules.
 */
function useHostViewerSync() {
  const ViewerCtx = (window as any).__nekazariViewerContextInstance;
  const hostCtx = ViewerCtx ? React.useContext(ViewerCtx) : null;
  return hostCtx;
}

export const VegetationProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<VegetationIndexType | null>(null);
  const [selectedDate, setSelectedDate] = useState<Date | null>(null);

  const [dateRange, setDateRange] = useState<DateRange>({
    startDate: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000),
    endDate: new Date(),
  });

  const [selectedGeometry, setSelectedGeometry] = useState<any | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [indexResults, setIndexResults] = useState<Record<string, IndexResult>>({});

  // Sync with host ViewerContext (the real source of truth for entity selection)
  const hostCtx = useHostViewerSync();
  const prevHostEntityRef = useRef<string | null>(null);

  useEffect(() => {
    if (!hostCtx) return;

    const hostEntityId = hostCtx.selectedEntityId || null;

    // Only update when the host entity actually changes
    if (hostEntityId !== prevHostEntityRef.current) {
      prevHostEntityRef.current = hostEntityId;

      if (hostEntityId) {
        setSelectedEntityId(hostEntityId);
        setSelectedSceneId(null);
        if (!selectedIndex) setSelectedIndex('NDVI');
      } else {
        setSelectedEntityId(null);
        setSelectedGeometry(null);
      }
    }
  }, [hostCtx?.selectedEntityId]);

  // Also sync date from host if available
  useEffect(() => {
    if (!hostCtx?.currentDate) return;
    setSelectedDate(new Date(hostCtx.currentDate));
  }, [hostCtx?.currentDate]);

  // Fallback: listen for custom events (used when rendered outside unified viewer, e.g. module page)
  useEffect(() => {
    const handleEntitySelected = (event: CustomEvent<{ entityId: string | null, type?: string, geometry?: any }>) => {
      if (event.detail?.entityId) {
        setSelectedEntityId(event.detail.entityId);
        setSelectedSceneId(null);
        setSelectedGeometry(event.detail.geometry || null);
        if (!selectedIndex) setSelectedIndex('NDVI');
      }
    };

    window.addEventListener('nekazari:entity-selected', handleEntitySelected as EventListener);
    return () => {
      window.removeEventListener('nekazari:entity-selected', handleEntitySelected as EventListener);
    };
  }, []);

  const resetContext = useCallback(() => {
    setSelectedEntityId(null);
    setSelectedSceneId(null);
    setSelectedIndex(null);
    setSelectedDate(null);
    setSelectedGeometry(null);
    setActiveJobId(null);
    setIndexResults({});
    setDateRange({
      startDate: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000),
      endDate: new Date(),
    });
  }, []);

  return (
    <VegetationContext.Provider
      value={{
        selectedEntityId,
        selectedSceneId,
        selectedIndex,
        selectedDate,
        dateRange,
        selectedGeometry,
        activeJobId,
        indexResults,
        setSelectedEntityId,
        setSelectedSceneId,
        setSelectedIndex,
        setSelectedDate,
        setDateRange,
        setActiveJobId,
        setIndexResults,
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
