/**
 * useJobPolling — Shared analysis + polling logic.
 *
 * Extracted from VegetationAnalytics and VegetationLayerControl to eliminate
 * dual code path divergence (audit friction point #4).
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { useVegetationApi } from '../services/api';
import { useVegetationContext } from '../services/vegetationContext';
import { useTranslation } from '@nekazari/sdk';

interface UseJobPollingResult {
  startAnalysis: (options?: {
    startDate?: string;
    endDate?: string;
    customFormulaIds?: string[];
    localCloudThreshold?: number;
  }) => Promise<void>;
  cancelAnalysis: () => Promise<void>;
  isAnalyzing: boolean;
  analysisError: string | null;
  analysisProgress: string;
  usageToday: number;
  usageLimit: number;
}

export function useJobPolling(): UseJobPollingResult {
  const { t } = useTranslation();
  const api = useVegetationApi();
  const {
    selectedEntityId,
    setActiveJobId,
    setIndexResults,
    setSelectedIndex,
  } = useVegetationContext();

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analysisProgress, setAnalysisProgress] = useState('');
  const [usageToday, setUsageToday] = useState(0);
  const [usageLimit, setUsageLimit] = useState(50);

  const analysisJobIdRef = useRef<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cancelledRef = useRef(false);

  const clearPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      clearPolling();
    };
  }, [clearPolling]);

  const cancelAnalysis = useCallback(async () => {
    cancelledRef.current = true;
    clearPolling();

    const jobId = analysisJobIdRef.current;
    if (jobId) {
      try {
        await api.deleteJob(jobId);
      } catch {
        // Job may already be done
      }
    }

    analysisJobIdRef.current = null;
    setIsAnalyzing(false);
    setAnalysisProgress('');
  }, [api, clearPolling]);

  const startAnalysis = useCallback(async (options?: {
    startDate?: string;
    endDate?: string;
    customFormulaIds?: string[];
    localCloudThreshold?: number;
  }) => {
    if (!selectedEntityId) return;

    setIsAnalyzing(true);
    setAnalysisError(null);
    setAnalysisProgress(t('analyticsPage.processingHistoricDesc', 'Downloading satellite imagery...'));
    cancelledRef.current = false;

    // Check usage limits
    try {
      const usage = await api.getCurrentUsage();
      setUsageToday(usage.frequency.used_jobs_today);
      setUsageLimit(usage.frequency.limit_jobs_today);
    } catch {
      // Non-blocking
    }

    try {
      const result = await api.analyzeParcel({
        entity_id: selectedEntityId,
        start_date: options?.startDate,
        end_date: options?.endDate,
        custom_formulas: options?.customFormulaIds,
        local_cloud_threshold: options?.localCloudThreshold,
      });

      if (cancelledRef.current) return;

      analysisJobIdRef.current = result.job_id;

      // Poll for completion
      const poll = async () => {
        if (cancelledRef.current) return;

        try {
          const data = await api.getEntityResults(selectedEntityId);
          const activeCount = data.active_jobs || 0;

          if (activeCount === 0 && data.has_results) {
            // All done
            clearPolling();
            analysisJobIdRef.current = null;
            setIndexResults(data.indices);

            // Select first available index
            const keys = Object.keys(data.indices);
            if (keys.length > 0) {
              const idx = keys.includes('NDVI') ? 'NDVI' : keys[0];
              setSelectedIndex(idx);
              setActiveJobId(data.indices[idx].job_id);
            }

            setIsAnalyzing(false);
            setAnalysisProgress('');
            return;
          }

          if (data.has_results) {
            // Partial results
            setIndexResults(data.indices);
            const doneCount = Object.keys(data.indices).length;
            setAnalysisProgress(t('analyticsPage.processingHistoric', 'Processing {{done}}/5 indices...', { done: doneCount }));
          } else {
            setAnalysisProgress(t('analyticsPage.processingHistoric', 'Processing historical data...'));
          }
        } catch {
          // Transient error, keep polling
        }
      };

      poll(); // Immediate first poll
      pollTimerRef.current = setInterval(poll, 3000);
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || '';

      let message: string;
      if (status === 404 && detail.toLowerCase().includes('no scenes')) {
        message = t('errors.noScenesFound', 'No satellite imagery available for these dates. Try a wider date range or wait for the next Sentinel-2 pass (every 5 days).');
      } else if (status === 503) {
        message = t('errors.noCredentials', 'Copernicus credentials not configured. Contact your administrator.');
      } else if (status === 422 && detail.toLowerCase().includes('geometry')) {
        message = t('errors.noGeometry', 'This parcel has no geometry defined.');
      } else {
        message = typeof detail === 'string' && detail
          ? detail
          : t('errors.calculationFailed', 'Analysis failed. Please try again.');
      }

      setAnalysisError(message);
      setIsAnalyzing(false);
      analysisJobIdRef.current = null;
    }
  }, [selectedEntityId, api, t, setActiveJobId, setIndexResults, setSelectedIndex, clearPolling]);

  return {
    startAnalysis,
    cancelAnalysis,
    isAnalyzing,
    analysisError,
    analysisProgress,
    usageToday,
    usageLimit,
  };
}
