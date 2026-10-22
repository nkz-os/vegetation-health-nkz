/**
 * Hook for managing vegetation scenes
 * Extracted from AnalyticsPage for reusability
 */

import { useState, useEffect, useCallback } from 'react';
import { useVegetationApi } from '../services/api';
import type { VegetationScene } from '../types';

interface UseVegetationScenesOptions {
  entityId?: string | null;
  startDate?: string;
  endDate?: string;
  limit?: number;
  autoRefresh?: boolean;
}

export function useVegetationScenes(options: UseVegetationScenesOptions = {}) {
  const {
    entityId,
    startDate,
    endDate,
    limit = 50,
    autoRefresh = false,
  } = options;

  const api = useVegetationApi();
  const [scenes, setScenes] = useState<VegetationScene[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadScenes = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.listScenes(entityId || undefined, startDate, endDate, limit);
      setScenes(data.scenes || []);
    } catch (err) {
      console.error('[useVegetationScenes] Error loading scenes:', err);
      setError(err instanceof Error ? err.message : 'Failed to load scenes');
      setScenes([]);
    } finally {
      setLoading(false);
    }
  }, [api, entityId, startDate, endDate, limit]);

  useEffect(() => {
    loadScenes();
  }, [loadScenes]);

  // Auto-refresh if enabled
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(() => {
      loadScenes();
    }, 30000); // 30 seconds

    return () => clearInterval(interval);
  }, [autoRefresh, loadScenes]);

  return {
    scenes,
    loading,
    error,
    refresh: loadScenes,
  };
}










