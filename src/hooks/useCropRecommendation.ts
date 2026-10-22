/**
 * useCropRecommendation Hook (Phase F4 Frontend)
 * Fetches vegetation index recommendations based on crop species
 * from the backend /api/logic/recommendation/{species} endpoint.
 */

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from './useAuth';

interface CropRecommendation {
  default_index: string;
  valid_indices: string[];
}

interface UseCropRecommendationResult {
  recommendation: CropRecommendation | null;
  loading: boolean;
  error: string | null;
  refetch: (species: string) => Promise<void>;
}

export function useCropRecommendation(cropSpecies?: string): UseCropRecommendationResult {
  const { token } = useAuth();
  const [recommendation, setRecommendation] = useState<CropRecommendation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRecommendation = useCallback(async (species: string) => {
    if (!species || !token) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch(
        `/api/vegetation/logic/recommendation/${encodeURIComponent(species)}`,
        {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
        }
      );
      
      if (!response.ok) {
        throw new Error(`Failed to fetch recommendation: ${response.status}`);
      }
      
      const data = await response.json();
      setRecommendation(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      // Fallback to default
      setRecommendation({
        default_index: 'NDVI',
        valid_indices: ['NDVI', 'NDMI', 'SAVI'],
      });
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (cropSpecies) {
      fetchRecommendation(cropSpecies);
    }
  }, [cropSpecies, fetchRecommendation]);

  return {
    recommendation,
    loading,
    error,
    refetch: fetchRecommendation,
  };
}

export default useCropRecommendation;
