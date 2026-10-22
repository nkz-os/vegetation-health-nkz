/**
 * Hook for fetching and managing vegetation index timeseries data.
 * Used in Analytics page for historical comparisons.
 */

import { useState, useEffect, useCallback } from 'react';
import { useVegetationApi } from '../services/api';
import type { TimeseriesDataPoint } from '../types';

interface TimeseriesOptions {
  entityId: string;
  indexType: string;
  startDate?: string;
  endDate?: string;
}

export function useTimeseries(options: TimeseriesOptions) {
  const api = useVegetationApi();
  const [data, setData] = useState<TimeseriesDataPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTimeseries = useCallback(async () => {
    if (!options.entityId || !options.indexType) {
      setData([]);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const result = await api.getTimeseries(
        options.entityId,
        options.indexType,
        options.startDate,
        options.endDate
      );
      setData(result.data_points || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load timeseries');
      setData([]);
    } finally {
      setLoading(false);
    }
  }, [api, options.entityId, options.indexType, options.startDate, options.endDate]);

  useEffect(() => {
    fetchTimeseries();
  }, [fetchTimeseries]);

  return {
    data,
    loading,
    error,
    refetch: fetchTimeseries,
  };
}














