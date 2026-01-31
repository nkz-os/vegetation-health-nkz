import axios, { AxiosInstance } from 'axios';
import { VegetationJob, VegetationScene, VegetationConfig, IndexCalculationParams, TimeseriesDataPoint, TimelineStatsResponse, YearComparisonResponse } from '../types';

/**
 * API Client for Vegetation Prime Backend.
 * Uses relative /api/vegetation; platform exposes this path on frontend host per EXTERNAL_MODULE_INSTALLATION.
 */
export class VegetationApiClient {
  private client: AxiosInstance;
  private getToken: () => string | undefined;
  private getTenantId: () => string | undefined;

  constructor(
    getToken: () => string | undefined,
    getTenantId: () => string | undefined,
    baseUrl: string = '/api/vegetation'
  ) {
    this.getToken = getToken;
    this.getTenantId = getTenantId;

    this.client = axios.create({
      baseURL: baseUrl,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Request interceptor for auth
    this.client.interceptors.request.use((config) => {
      const token = this.getToken();
      const tenantId = this.getTenantId();

      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }

      if (tenantId) {
        config.headers['X-Tenant-ID'] = tenantId;
      }

      return config;
    });

    // Response interceptor for error logger
    this.client.interceptors.response.use(
      (response) => response.data,
      (error) => {
        return Promise.reject(error);
      }
    );
  }

  // --- Endpoints ---

  async checkHealth(): Promise<{ status: string }> {
    const response = await this.client.get('/health');
    return response as unknown as { status: string };
  }

  async listScenes(
    entityId?: string,
    startDate?: string,
    endDate?: string,
    limit = 50
  ): Promise<{ scenes: VegetationScene[]; total: number }> {
    return this.getScenes(entityId, startDate, endDate, limit);
  }

  async getScenes(
    entityId?: string,
    startDate?: string,
    endDate?: string,
    limit = 50
  ): Promise<{ scenes: VegetationScene[]; total: number }> {
    const params = new URLSearchParams();
    if (entityId) params.append('entity_id', entityId);
    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);
    params.append('limit', limit.toString());

    const response = await this.client.get(`/scenes?${params.toString()}`);
    return response as unknown as { scenes: VegetationScene[]; total: number };
  }

  async getIndices(
    entityId?: string,
    sceneId?: string,
    indexType?: string,
    format: 'geojson' | 'xyz' = 'geojson'
  ): Promise<any> {
    const params = new URLSearchParams();
    if (entityId) params.append('entity_id', entityId);
    if (sceneId) params.append('scene_id', sceneId);
    if (indexType) params.append('index_type', indexType);
    params.append('format', format);

    const response = await this.client.get(`/indices?${params.toString()}`);
    return response;
  }

  async getTimeseries(
    entityId: string,
    indexType: string,
    startDate?: string,
    endDate?: string
  ): Promise<{ entity_id: string; index_type: string; data_points: TimeseriesDataPoint[] }> {
    const params = new URLSearchParams();
    params.append('entity_id', entityId);
    params.append('index_type', indexType);
    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);

    const response = await this.client.get(`/timeseries?${params.toString()}`);
    return response as unknown as { entity_id: string; index_type: string; data_points: TimeseriesDataPoint[] };
  }

  async calculateIndex(params: IndexCalculationParams): Promise<{ job_id: string; message: string }> {
    const response = await this.client.post('/calculate', params);
    return response as unknown as { job_id: string; message: string };
  }

  async getJobHistogram(
    jobId: string,
    bins: number = 50
  ): Promise<{
    bins: number[];
    counts: number[];
    statistics: {
      mean: number;
      min: number;
      max: number;
      std_dev: number;
      pixel_count: number;
    };
    approximation: boolean;
    note?: string;
  }> {
    const response = await this.client.get(`/jobs/${jobId}/histogram?bins=${bins}`);
    return response as unknown as {
      bins: number[];
      counts: number[];
      statistics: {
        mean: number;
        min: number;
        max: number;
        std_dev: number;
        pixel_count: number;
      };
      approximation: boolean;
      note?: string;
    };
  }

  async getSceneStats(
    entityId: string,
    indexType: string = "NDVI",
    months: number = 12
  ): Promise<TimelineStatsResponse> {
    const params = new URLSearchParams();
    params.append("index_type", indexType);
    params.append("months", months.toString());

    const response = await this.client.get(`/scenes/${encodeURIComponent(entityId)}/stats?${params.toString()}`);
    return response as unknown as TimelineStatsResponse;
  }

  async compareYears(
    entityId: string,
    indexType: string = "NDVI"
  ): Promise<YearComparisonResponse> {
    const params = new URLSearchParams();
    params.append("index_type", indexType);

    const response = await this.client.get(`/scenes/${encodeURIComponent(entityId)}/compare-years?${params.toString()}`);
    return response as unknown as YearComparisonResponse;
  }

  async getConfig(): Promise<VegetationConfig> {
    const response = await this.client.get('/config');
    return response as unknown as VegetationConfig;
  }

  async updateConfig(config: Partial<VegetationConfig>): Promise<{ message: string; config: VegetationConfig }> {
    const response = await this.client.post('/config', config);
    return response as unknown as { message: string; config: VegetationConfig };
  }

  async getCurrentUsage(): Promise<{
    plan: string;
    volume: { used_ha: number; limit_ha: number };
    frequency: { used_jobs_today: number; limit_jobs_today: number };
  }> {
    const response = await this.client.get('/usage/current');
    return response as unknown as {
      plan: string;
      volume: { used_ha: number; limit_ha: number };
      frequency: { used_jobs_today: number; limit_jobs_today: number };
    };
  }

  async getCredentialsStatus(): Promise<{
    available: boolean;
    source: 'platform' | 'module' | null;
    message: string;
    client_id_preview?: string;
  }> {
    try {
      const response = await this.client.get('/config/credentials-status');
      return response as unknown as {
        available: boolean;
        source: 'platform' | 'module' | null;
        message: string;
        client_id_preview?: string;
      };
    } catch (error) {
      console.error('[VegetationApiClient] Error in getCredentialsStatus:', error);
      throw error;
    }
  }

  async getRecentJobs(limit: number = 5): Promise<VegetationJob[]> {
    const response = await this.client.get(`/jobs?limit=${limit}`);
    return response as unknown as VegetationJob[];
  }

  async listJobs(status?: string, limit: number = 50, offset: number = 0): Promise<{ jobs: VegetationJob[]; total: number }> {
    const searchParams = new URLSearchParams();
    if (status) searchParams.append("status", status);
    searchParams.append("limit", limit.toString());
    searchParams.append("offset", offset.toString());

    const response = await this.client.get(`/jobs?${searchParams.toString()}`);
    return response as unknown as { jobs: VegetationJob[]; total: number };
  }

  async getJobDetails(jobId: string): Promise<{
    job: VegetationJob;
    index_stats?: { mean: number; min: number; max: number; std_dev: number; pixel_count: number; };
    timeseries?: any[];
    scene_info?: any;
  }> {
    // Use the /details endpoint which returns { job: {...}, index_stats, ... }
    const response = await this.client.get(`/jobs/${jobId}/details`);
    return response as unknown as {
      job: VegetationJob;
      index_stats?: { mean: number; min: number; max: number; std_dev: number; pixel_count: number; };
      timeseries?: any[];
      scene_info?: any;
    };
  }

  /**
   * Download job result in specified format
   */
  async downloadResult(jobId: string, format: 'geotiff' | 'png' | 'csv'): Promise<Blob> {
    const token = this.getToken();
    const tenantId = this.getTenantId();

    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (tenantId) headers['X-Tenant-ID'] = tenantId;

    const response = await fetch(`/api/vegetation/jobs/${jobId}/download?format=${format}`, {
      method: 'GET',
      headers,
    });

    if (!response.ok) {
      throw new Error(`Download failed: ${response.status}`);
    }

    return response.blob();
  }

  /**
   * Delete a job from history
   */
  async deleteJob(jobId: string): Promise<{ message: string }> {
    const response = await this.client.delete(`/jobs/${jobId}`);
    return response as unknown as { message: string };
  }

  // ==========================================================================
  // Integration Endpoints (N8N, Intelligence Module, Platform)
  // ==========================================================================

  /**
   * Get prediction for an entity (N8N/Intelligence Module ready)
   */
  async getPrediction(
    entityId: string,
    indexType: string = 'NDVI',
    daysAhead: number = 7
  ): Promise<{
    entity_id: string;
    index_type: string;
    model_type: string;
    predictions: Array<{ date: string; predicted_value: number; confidence_lower: number; confidence_upper: number }>;
    webhook_metadata: Record<string, any>;
  } | null> {
    try {
      const params = new URLSearchParams();
      params.append('index_type', indexType);
      params.append('days_ahead', daysAhead.toString());

      const response = await this.client.get(`/prediction/${encodeURIComponent(entityId)}?${params.toString()}`);
      return response as any;
    } catch (error) {
      console.warn('[API] Prediction not available:', error);
      return null;
    }
  }

  /**
   * Get carbon configuration for an entity
   */
  async getCarbonConfig(entityId: string): Promise<{
    entity_id: string;
    strawRemoved: boolean;
    soilType: string;
    tillageType?: string;
    lue_factor: number;
  } | null> {
    try {
      const response = await this.client.get(`/carbon/${encodeURIComponent(entityId)}`);
      return response as any;
    } catch (error) {
      console.warn('[API] Carbon config not found:', error);
      return null;
    }
  }

  /**
   * Save carbon configuration for an entity
   */
  async saveCarbonConfig(
    entityId: string,
    config: { strawRemoved: boolean; soilType: string; tillageType?: string }
  ): Promise<void> {
    await this.client.post(`/carbon/${encodeURIComponent(entityId)}`, config);
  }

  /**
   * Trigger zoning job (VRA Management Zones)
   * Supports N8N callback and Intelligence Module delegation
   */
  async triggerZoning(
    parcelId: string,
    options?: {
      n_zones?: number;
      delegate_to_intelligence?: boolean;
      n8n_callback_url?: string;
    }
  ): Promise<{
    message: string;
    task_id: string;
    parcel_id: string;
    webhook_metadata: Record<string, any>;
  }> {
    const response = await this.client.post(`/jobs/zoning/${encodeURIComponent(parcelId)}`, options || {});
    return response as any;
  }

  /**
   * Get zoning results as GeoJSON
   */
  async getZoningGeoJson(parcelId: string): Promise<{
    type: 'FeatureCollection';
    features: Array<{
      type: 'Feature';
      properties: Record<string, any>;
      geometry: Record<string, any>;
    }>;
  }> {
    const response = await this.client.get(`/jobs/zoning/${encodeURIComponent(parcelId)}/geojson`);
    return response as any;
  }

  /**
   * Get crop recommendation based on species (Crop Intelligence)
   */
  async getCropRecommendation(cropSpecies: string): Promise<{
    default_index: string;
    valid_indices: string[];
  }> {
    const response = await this.client.get(`/logic/recommendation/${encodeURIComponent(cropSpecies)}`);
    return response as any;
  }
  /**
   * Save a geometry as a permanent Management Zone (AgriParcel)
   */
  async saveManagementZone(
    name: string,
    geometry: any,
    parentId?: string,
    attributes?: Record<string, any>
  ): Promise<{ id: string; message: string }> {
    const response = await this.client.post('/entities/roi', {
      name,
      geometry,
      parent_id: parentId,
      attributes
    });
    return response as any;
  }

  /**
   * List all AgriParcel entities for the current tenant
   * Uses the main Nekazari API (NGSI-LD broker via gateway)
   */
  async listTenantParcels(): Promise<any[]> {
    try {
      const token = this.getToken();
      const tenantId = this.getTenantId();

      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      };

      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
      if (tenantId) {
        headers['X-Tenant-ID'] = tenantId;
      }

      // Call the main Nekazari API to get AgriParcel entities
      const response = await fetch('/api/ngsi-ld/v1/entities?type=AgriParcel&limit=100', {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        console.error('[VegetationApi] Failed to fetch parcels:', response.status);
        return [];
      }

      const data = await response.json();
      return Array.isArray(data) ? data : [];
    } catch (error) {
      console.error('[VegetationApi] Error fetching tenant parcels:', error);
      return [];
    }
  }

  /**
   * Create a new monitoring subscription
   */
  async createSubscription(data: {
    entity_id: string;
    geometry: any;
    start_date: string;
    index_types: string[];
    frequency: 'weekly' | 'daily' | 'biweekly';
    is_active: boolean;
  }): Promise<any> {
    const response = await this.client.post('/subscriptions', data);
    return response;
  }

  /**
   * List subscriptions
   */
  async listSubscriptions(): Promise<any[]> {
    const response = await this.client.get('/subscriptions');
    return response as unknown as any[];
  }

  /**
   * Get subscription for an entity (helper)
   */
  async getSubscriptionForEntity(entityId: string): Promise<any | null> {
    const subs = await this.listSubscriptions();
    return subs.find((s: any) => s.entity_id === entityId) || null;
  }
}

// Hook for using API client
import { useMemo } from 'react';

export function useVegetationApi(): VegetationApiClient {
  const getTokenFromHost = (): string | undefined => {
    try {
      const hostAuth = (window as any).__nekazariAuthContext;
      if (hostAuth && typeof hostAuth.getToken === 'function') {
        return hostAuth.getToken();
      }
    } catch (error) {
      // Silent fail
    }
    return undefined;
  };

  const getTenantIdFromHost = (): string | undefined => {
    try {
      const hostAuth = (window as any).__nekazariAuthContext;
      if (hostAuth && hostAuth.tenantId) {
        return hostAuth.tenantId;
      }
    } catch (error) {
      // Silent fail
    }
    return undefined;
  };

  return useMemo(
    () => new VegetationApiClient(getTokenFromHost, getTenantIdFromHost),
    []
  );
}
