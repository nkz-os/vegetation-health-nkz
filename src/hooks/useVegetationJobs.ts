/**
 * Hook for managing vegetation jobs (download, process, calculate_index)
 * Extracted from ConfigPage and AnalyticsPage for reusability
 */

import { useState, useEffect, useCallback } from 'react';
import { useVegetationApi } from '../services/api';
import type { VegetationJob } from '../types';

interface UseVegetationJobsOptions {
  statusFilter?: string;
  limit?: number;
  autoRefresh?: boolean;
  refreshInterval?: number;
}

export function useVegetationJobs(options: UseVegetationJobsOptions = {}) {
  const {
    statusFilter = 'all',
    limit = 50,
    autoRefresh = false,
    refreshInterval = 30000, // 30 seconds
  } = options;

  const api = useVegetationApi();
  const [jobs, setJobs] = useState<VegetationJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.listJobs(statusFilter !== 'all' ? statusFilter : undefined, limit, 0);
      setJobs(data?.jobs || []);
    } catch (err) {
      console.error('[useVegetationJobs] Error loading jobs:', err);
      setError(err instanceof Error ? err.message : 'Failed to load jobs');
      setJobs([]);
    } finally {
      setLoading(false);
    }
  }, [api, statusFilter, limit]);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  // Auto-refresh if enabled
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(() => {
      loadJobs();
    }, refreshInterval);

    return () => clearInterval(interval);
  }, [autoRefresh, refreshInterval, loadJobs]);

  // Statistics
  const statistics = {
    total: jobs.length,
    completed: jobs.filter(j => j.status === 'completed').length,
    failed: jobs.filter(j => j.status === 'failed').length,
    running: jobs.filter(j => j.status === 'running').length,
    pending: jobs.filter(j => j.status === 'pending').length,
  };

  return {
    jobs,
    loading,
    error,
    statistics,
    refresh: loadJobs,
  };
}










