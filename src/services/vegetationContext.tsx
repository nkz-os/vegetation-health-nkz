import React, { createContext, useContext, useState, ReactNode, useCallback, useEffect } from 'react';
import type { VegetationIndexType } from '../types';

interface DateRange {
  startDate: Date | null;
  endDate: Date | null;
}

interface VegetationContextType {
  selectedEntityId: string | null;
  selectedSceneId: string | null;
  selectedIndex: VegetationIndexType | null;
  selectedDate: Date | null;
  dateRange: DateRange;
  selectedGeometry?: any | null;
  setSelectedEntityId: (id: string | null) => void;
  setSelectedSceneId: (id: string | null) => void;
  setSelectedIndex: (index: VegetationIndexType | null) => void;
  setSelectedDate: (date: Date | null) => void;
  setDateRange: (range: DateRange) => void;
  resetContext: () => void;
}

const VegetationContext = createContext<VegetationContextType | undefined>(undefined);

export const VegetationProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<VegetationIndexType | null>(null);
  const [selectedDate, setSelectedDate] = useState<Date | null>(null);

  // Date range for temporal analysis (default: last 3 months)
  const [dateRange, setDateRange] = useState<DateRange>({
    startDate: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000), // 90 days ago
    endDate: new Date(),
  });

  const [selectedGeometry, setSelectedGeometry] = useState<any | null>(null);

  // --- CRITICAL FIX: Listen for Host Selection Events ---
  useEffect(() => {
    // Handler for entity selection event dispatched by Host App (CesiumMap)
    const handleEntitySelected = (event: CustomEvent<{ entityId: string | null, type?: string, geometry?: any }>) => {
      console.log('[VegetationContext] Received entity selection:', event.detail);
      if (event.detail && event.detail.entityId) {
        setSelectedEntityId(event.detail.entityId);
        // Reset scene specific state when entity changes
        setSelectedSceneId(null);
        // Store geometry if provided (crucial for "Save as Management Zone")
        if (event.detail.geometry) {
          setSelectedGeometry(event.detail.geometry);
        } else {
          setSelectedGeometry(null);
        }

        // Keep index? Maybe default to NDVI?
        if (!selectedIndex) setSelectedIndex('NDVI');
      } else {
        // Deselection?
        // setSelectedEntityId(null); // Optional: clear selection if host clears it
        setSelectedGeometry(null);
      }
    };

    // Attach listener
    window.addEventListener('nekazari:entity-selected', handleEntitySelected as EventListener);

    // Initial check: if loaded after selection, check global context
    const globalContext = (window as any).__nekazariContext;
    if (globalContext && globalContext.selectedEntityId) {
      console.log('[VegetationContext] Initializing from global context:', globalContext.selectedEntityId);
      setSelectedEntityId(globalContext.selectedEntityId);
      if (globalContext.selectedGeometry) {
        setSelectedGeometry(globalContext.selectedGeometry);
      }
    }

    // Cleanup
    return () => {
      window.removeEventListener('nekazari:entity-selected', handleEntitySelected as EventListener);
    };
  }, []); // Run once on mount

  const resetContext = useCallback(() => {
    setSelectedEntityId(null);
    setSelectedSceneId(null);
    setSelectedIndex(null);
    setSelectedDate(null);
    setSelectedGeometry(null);
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
        setSelectedEntityId,
        setSelectedSceneId,
        setSelectedIndex,
        setSelectedDate,
        setDateRange,
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
