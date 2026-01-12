/**
 * useVegetationApi Hook
 * 
 * Re-exports the centralized API client from services/api.ts
 * This file exists for backward compatibility with imports from hooks/
 * 
 * Preferred import: import { useVegetationApi } from '../services/api';
 */

export { useVegetationApi, VegetationApiClient } from '../services/api';
