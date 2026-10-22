/**
 * Hook for managing vegetation index calculations with Polling.
 */

import { useState, useCallback } from 'react';
import { useVegetationApi } from '../services/api';
import { useVegetationContext } from '../services/vegetationContext';
import type { VegetationIndexType } from '../types';

interface CalculationOptions {
  sceneId?: string;
  indexType: VegetationIndexType;
  entityId?: string;
  formula?: string;
  startDate?: string;
  endDate?: string;
}

interface CalculationState {
  isCalculating: boolean;
  jobId: string | null;
  error: string | null;
  success: boolean;
}

export function useIndexCalculation() {
  const api = useVegetationApi();
  const { selectedSceneId, selectedEntityId } = useVegetationContext();
  const [state, setState] = useState<CalculationState>({
    isCalculating: false,
    jobId: null,
    error: null,
    success: false,
  });

  const calculateIndex = useCallback(
    async (options?: Partial<CalculationOptions>) => {
      const calculationOptions: CalculationOptions = {
        sceneId: options?.sceneId || selectedSceneId || undefined,
        indexType: options?.indexType || 'NDVI',
        entityId: options?.entityId || selectedEntityId || undefined,
        formula: options?.formula,
        startDate: options?.startDate,
        endDate: options?.endDate,
      };

      if (!calculationOptions.sceneId && (!calculationOptions.startDate || !calculationOptions.endDate)) {
        setState({
          isCalculating: false,
          jobId: null,
          error: 'Please select a scene OR provide a date range used.',
          success: false,
        });
        return null;
      }

      setState({
        isCalculating: true,
        jobId: null,
        error: null,
        success: false,
      });

      try {
        const result = await api.calculateIndex({
          scene_id: calculationOptions.sceneId,
          index_type: calculationOptions.indexType,
          entity_id: calculationOptions.entityId,
          formula: calculationOptions.formula,
          start_date: calculationOptions.startDate,
          end_date: calculationOptions.endDate,
        });

        // POLL FOR COMPLETION
        const jobId = result.job_id;
        let attempts = 0;
        const maxAttempts = 60; // 60s timeout (increased for complex calculations)
        let lastStatus = 'pending';

        while (attempts < maxAttempts) {
          await new Promise(r => setTimeout(r, 1000));
          try {
            const jobDetails = await api.getJobDetails(jobId);

            if (!jobDetails || !jobDetails.job) {
              console.warn('Poll: job details not available yet, retrying...');
              attempts++;
              continue;
            }

            lastStatus = jobDetails.job.status;

            if (lastStatus === 'completed') {
              // Success!
              break;
            } else if (lastStatus === 'failed') {
              const errorMsg = jobDetails.job.error_message || 'El cálculo falló en el servidor';
              throw new Error(errorMsg);
            }
            // If pending/processing, continue
          } catch (pollError: any) {
            // If it's a thrown error from failed status, rethrow it
            if (pollError.message && !pollError.message.includes('Poll')) {
              throw pollError;
            }
            console.warn('Poll error:', pollError.message);
          }
          attempts++;
        }

        if (attempts >= maxAttempts && lastStatus !== 'completed') {
          throw new Error(`El cálculo tardó demasiado (último estado: ${lastStatus}). Revisa los logs del servidor.`);
        }

        setState({
          isCalculating: false,
          jobId: jobId,
          error: null,
          success: true,
        });

        return jobId;
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Failed to calculate index';
        setState({
          isCalculating: false,
          jobId: null,
          error: errorMessage,
          success: false,
        });
        return null;
      }
    },
    [api, selectedSceneId, selectedEntityId]
  );

  const resetState = useCallback(() => {
    setState({
      isCalculating: false,
      jobId: null,
      error: null,
      success: false,
    });
  }, []);

  return {
    calculateIndex,
    resetState,
    ...state,
  };
}
