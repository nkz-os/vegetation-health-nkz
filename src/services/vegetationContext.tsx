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

  // --- CRITICAL FIX: Listen for Host Events (Selection & Time) ---
  useEffect(() => {
    // 1. Entity Selection
    const handleEntitySelected = (event: CustomEvent<{ entityId: string | null, type?: string, geometry?: any }>) => {
      if (event.detail?.entityId) {
        setSelectedEntityId(event.detail.entityId);
        setSelectedSceneId(null);
        setSelectedGeometry(event.detail.geometry || null);
        if (!selectedIndex) setSelectedIndex('NDVI');
      }
    };

    // 2. Global Time Synchronization
    const handleTimeChanged = (event: CustomEvent<{ date: string | Date }>) => {
      const newDate = event.detail?.date ? new Date(event.detail.date) : null;
      if (newDate) {
        setSelectedDate(newDate);
      }
    };

    window.addEventListener('nekazari:entity-selected', handleEntitySelected as EventListener);
    window.addEventListener('nekazari:time-changed', handleTimeChanged as EventListener);

    // Initial sync from global host context
    const globalContext = (window as any).__nekazariContext;
    if (globalContext) {
      if (globalContext.selectedEntityId) setSelectedEntityId(globalContext.selectedEntityId);
      if (globalContext.currentDate) setSelectedDate(new Date(globalContext.currentDate));
    }

    return () => {
      window.removeEventListener('nekazari:entity-selected', handleEntitySelected as EventListener);
      window.removeEventListener('nekazari:time-changed', handleTimeChanged as EventListener);
    };
  }, []);

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
