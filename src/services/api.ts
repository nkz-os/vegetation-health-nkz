import axios, { AxiosInstance } from 'axios';
import {
  VegetationJob,
  VegetationScene,
  VegetationConfig,
  IndexCalculationParams,
  TimeseriesDataPoint,
  TimelineStatsResponse,
  YearComparisonResponse,
  AnomalyCheckParams,
  AnomalyCheckResponse,
  AlertConfig,
  AlertTestResponse,
  AlertFormatResponse,
  FormulaPreviewParams,
  FormulaPreviewResponse,
  ModuleCapabilities,
  CustomFormula,
  CustomFormulaValidationResponse,
  EntityIndexResult,
  CropSeason,
} from '../types';

/**
 * API Client for Vegetation Prime Backend.
 * Uses relative /api/vegetation; platform exposes this path on frontend host per EXTERNAL_MODULE_INSTALLATION.
 */
export class VegetationApiClient {
  private client: AxiosInstance;
  private orionClient: AxiosInstance;
  private getTenantId: () => string | undefined;

  constructor(
    getTenantId: () => string | undefined,
    baseUrl: string = '/api/vegetation'
  ) {
    this.getTenantId = getTenantId;

    this.client = axios.create({
      baseURL: baseUrl,
      withCredentials: true,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Separate client for Orion-LD queries (different base URL)
    this.orionClient = axios.create({
      baseURL: '/',
      withCredentials: true,
      headers: {
        'Accept': 'application/json',
      },
    });

    // Shared interceptor: inject X-Tenant-ID and mobile Bearer token.
    // Auth is handled by httpOnly cookie (withCredentials: true).
    const addTenantId = (config: any) => {
      const tenantId = this.getTenantId();
      if (tenantId) {
        config.headers['X-Tenant-ID'] = tenantId;
      }
      // If mobile token exists (WebView context), add Bearer fallback
      const mobileToken = (window as any).__nekazariMobileToken;
      if (mobileToken && !config.headers['Authorization']) {
        config.headers['Authorization'] = `Bearer ${mobileToken}`;
      }
      return config;
    };

    this.client.interceptors.request.use(addTenantId);
    this.orionClient.interceptors.request.use(addTenantId);

    // Response interceptor: unwrap data for vegetation API client
    this.client.interceptors.response.use(
      (response) => response.data,
      (error) => Promise.reject(error)
    );

    // Orion client returns full response (caller handles .data)
  }

  // --- Endpoints ---

  async checkHealth(): Promise<{ status: string }> {
    const response = await this.client.get('/health');
    return response as unknown as { status: string };
  }

  /** Phase 4: TiTiler proxy — get presigned tile URL template for Cesium. */
  async getViewerUrl(sceneId: string, indexType: string): Promise<{ tileUrlTemplate: string; expiresAt: string }> {
    const params = new URLSearchParams({ scene_id: sceneId, index_type: indexType });
    const response = await this.client.get(`/viewer-url?${params.toString()}`);
    return response as unknown as { tileUrlTemplate: string; expiresAt: string };
  }

  /** Phase 6: Sparse timeline — lightweight availability metadata (§12.8.1). */
  async getEntityDataStatus(entityId: string): Promise<import('../types').EntityDataStatus> {
    const response = await this.client.get(
      `/entities/${encodeURIComponent(entityId)}/data-status`
    );
    return response as any;
  }

  async getScenesAvailable(
    entityId: string,
    indexType?: string,
    startDate?: string,
    endDate?: string
  ): Promise<{
    entity_id: string;
    index_type: string;
    count: number;
    timeline: Array<{
      scene_id: string;
      id: string;
      date: string;
      acquisition_datetime: string | null;
      local_cloud_pct: number | null;
      mean_value: number | null;
    }>;
  }> {
    const params = new URLSearchParams();
    if (indexType) params.append('index_type', indexType);
    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);
    const response = await this.client.get(
      `/entities/${encodeURIComponent(entityId)}/scenes/available?${params.toString()}`
    );
    return response as any;
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
    const data = response as unknown as { jobs: VegetationJob[]; total: number };
    return Array.isArray(data?.jobs) ? data.jobs : [];
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
    const tenantId = this.getTenantId();

    const headers: Record<string, string> = {};
    if (tenantId) headers['X-Tenant-ID'] = tenantId;

    const response = await this.client.get(`/jobs/${jobId}/download?format=${format}`, {
      responseType: 'blob',
    });
    return response as unknown as Blob;
  }

  /**
   * Delete a job from history
   */
  async deleteJob(jobId: string): Promise<{ message: string }> {
    const response = await this.client.delete(`/jobs/${jobId}`);
    return response as unknown as { message: string };
  }

  // ==========================================================================
  // One-Click Analysis (new simplified flow)
  // ==========================================================================

  /**
   * Analyze a parcel: downloads best Sentinel-2 scene + calculates all indices.
   * This is the primary entry point for the simplified UI.
   */
  async analyzeParcel(params: {
    entity_id: string;
    start_date?: string;
    end_date?: string;
    indices?: string[];
    custom_formulas?: string[];
  }): Promise<{
    job_id: string;
    message: string;
    indices: string[];
    custom_formulas?: Array<{
      formula_id: string;
      formula_name: string;
      formula_expression: string;
      index_key: string;
    }>;
    date_range: { start: string; end: string };
  }> {
    const response = await this.client.post('/analyze', params);
    return response as any;
  }

  /**
   * Get latest completed results per index type for an entity.
   * Returns a map of index_type -> { job_id, statistics, raster_path }.
   */
  async getEntityResults(
    entityId: string,
    options?: { sceneId?: string | null }
  ): Promise<{
    entity_id: string;
    scene_id?: string | null;
    indices: Record<string, EntityIndexResult>;
    active_jobs: number;
    has_results: boolean;
  }> {
    const params = new URLSearchParams();
    if (options?.sceneId) {
      params.set('scene_id', options.sceneId);
    }
    const q = params.toString();
    const path = `/results/${encodeURIComponent(entityId)}${q ? `?${q}` : ''}`;
    const response = await this.client.get(path);
    return response as any;
  }

  async listCustomFormulas(): Promise<{ items: CustomFormula[]; total: number }> {
    const response = await this.client.get('/custom-formulas');
    return response as unknown as { items: CustomFormula[]; total: number };
  }

  async validateCustomFormula(formula: string): Promise<CustomFormulaValidationResponse> {
    const response = await this.client.post('/custom-formulas/validate', { formula });
    return response as unknown as CustomFormulaValidationResponse;
  }

  async createCustomFormula(params: {
    name: string;
    formula: string;
    description?: string;
  }): Promise<CustomFormula> {
    const response = await this.client.post('/custom-formulas', params);
    return response as unknown as CustomFormula;
  }

  async deleteCustomFormula(formulaId: string): Promise<{ deleted: boolean; id: string }> {
    const response = await this.client.delete(`/custom-formulas/${encodeURIComponent(formulaId)}`);
    return response as unknown as { deleted: boolean; id: string };
  }

  // ==========================================================================
  // Crop Season API
  // ==========================================================================

  /**
   * Create a new crop season for a parcel (replaces v1 subscription wizard).
   * POST /api/vegetation/crop-seasons/{entity_id}
   */
  async createCropSeason(
    entityId: string,
    data: {
      crop_type: string;
      start_date: string;
      end_date?: string | null;
      monitoring_enabled: boolean;
    }
  ): Promise<{ id: string; message: string }> {
    const response = await this.client.post(
      `/crop-seasons/${encodeURIComponent(entityId)}`,
      data
    );
    return response as unknown as { id: string; message: string };
  }

  /**
   * List existing crop seasons for a parcel.
   * GET /api/vegetation/crop-seasons/{entity_id}
   */
  async listCropSeasons(entityId: string): Promise<CropSeason[]> {
    const response = await this.client.get(`/crop-seasons/${encodeURIComponent(entityId)}`);
    return response as unknown as CropSeason[];
  }

  // ==========================================================================
  // Integration Endpoints (N8N, Intelligence Module, Platform)
  // ==========================================================================

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
   * List all AgriParcel entities for the current tenant
   * Uses the main Nekazari API (NGSI-LD broker via gateway)
   */


  /**
   * List all AgriParcel entities for the current tenant
   * Uses the main Nekazari API (NGSI-LD broker via gateway)
   */
  async listTenantParcels(): Promise<any[]> {
    try {
      const response = await this.orionClient.get('/ngsi-ld/v1/entities', {
        params: { type: 'AgriParcel' },
      });
      return Array.isArray(response.data) ? response.data : [];
    } catch (error) {
      console.error('[VegetationApi] Error fetching tenant parcels:', error);
      return [];
    }
  }

  /**
   * Get a single entity by ID
   */
  async getEntity(entityId: string): Promise<any> {
    try {
      const response = await this.orionClient.get(`/ngsi-ld/v1/entities/${entityId}`);
      return response.data;
    } catch (error) {
      console.error('[VegetationApi] Error fetching entity:', error);
      throw error;
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

  /**
   * Update a subscription (toggle is_active, change frequency, etc.)
   */
  async updateSubscription(subId: string, updates: {
    is_active?: boolean;
    frequency?: string;
    index_types?: string[];
    status?: string;
    start_date?: string;
    /** Clears last_run_at so scheduler rescans from start_date */
    reset_monitoring_cursor?: boolean;
  }): Promise<any> {
    const response = await this.client.patch(`/subscriptions/${subId}`, updates);
    return response;
  }

  /**
   * Delete a subscription
   */
  async deleteSubscription(subId: string): Promise<{ message: string }> {
    const response = await this.client.delete(`/subscriptions/${subId}`);
    return response as unknown as { message: string };
  }

  // ==========================================================================
  // Ferrari Frontend - Export Methods
  // ==========================================================================

  /**
   * Export prescription map as GeoJSON
   */
  async exportPrescriptionGeojson(parcelId: string): Promise<Blob> {
    const response = await this.client.get(`/export/${encodeURIComponent(parcelId)}/geojson`, {
      responseType: 'blob',
    });
    return response as unknown as Blob;
  }

  /**
   * Export prescription map as Shapefile (zip)
   */
  async exportPrescriptionShapefile(parcelId: string): Promise<Blob> {
    const response = await this.client.get(`/export/${encodeURIComponent(parcelId)}/shapefile`, {
      responseType: 'blob',
    });
    return response as unknown as Blob;
  }

  /**
   * Export prescription map as CSV
   */
  async exportPrescriptionCsv(parcelId: string): Promise<Blob> {
    const response = await this.client.get(`/export/${encodeURIComponent(parcelId)}/csv`, {
      responseType: 'blob',
    });
    return response as unknown as Blob;
  }

  // ==========================================================================
  // Ferrari Frontend - Anomaly Detection
  // ==========================================================================

  /**
   * Check for anomalies in vegetation indices
   */
  async checkAnomalies(params: AnomalyCheckParams): Promise<AnomalyCheckResponse> {
    try {
      const response = await this.client.post('/anomalies/check', params);
      return response as unknown as AnomalyCheckResponse;
    } catch (error) {
      console.warn('[API] Anomaly check failed:', error);
      throw error;
    }
  }

  // ==========================================================================
  // Module Capabilities
  // ==========================================================================

  /**
   * Get module capabilities for graceful degradation
   */
  async getCapabilities(): Promise<ModuleCapabilities> {
    const response = await this.client.get('/capabilities');
    return response as unknown as ModuleCapabilities;
  }

  // ==========================================================================
  // Ferrari Frontend - Alerts Configuration
  // ==========================================================================

  /**
   * Configure vegetation alerts
   */
  async configureAlerts(config: AlertConfig): Promise<{ message: string }> {
    const response = await this.client.post('/alerts/configure', config);
    return response as unknown as { message: string };
  }

  /**
   * Test alert for an entity
   */
  async testAlert(entityId: string): Promise<AlertTestResponse> {
    const response = await this.client.get(`/alerts/test/${encodeURIComponent(entityId)}`);
    return response as unknown as AlertTestResponse;
  }

  /**
   * Get alert format example for N8N integration
   */
  async getAlertFormat(entityId: string): Promise<AlertFormatResponse> {
    const response = await this.client.get(`/alerts/format/${encodeURIComponent(entityId)}`);
    return response as unknown as AlertFormatResponse;
  }

  // ==========================================================================
  // Ferrari Frontend - Formula Preview
  // ==========================================================================

  /**
   * Preview custom formula calculation
   */
  async calculatePreview(params: FormulaPreviewParams): Promise<FormulaPreviewResponse> {
    try {
      const response = await this.client.post('/calculate/preview', params);
      return response as unknown as FormulaPreviewResponse;
    } catch (error) {
      console.warn('[API] Formula preview failed:', error);
      return {
        valid: false,
        error: error instanceof Error ? error.message : 'Preview failed'
      };
    }
  }

}

// Hook for using API client
import { useMemo } from 'react';

// Auth is handled via httpOnly cookie (withCredentials: true).

// Get tenant ID from window.__nekazariAuthContext (set by host).
const getTenantId = (): string | undefined => {
  const hostAuth = (window as any).__nekazariAuthContext;
  if (hostAuth && hostAuth.tenantId) {
    return hostAuth.tenantId;
  }
  return undefined;
};

// IMPORTANT: Always use relative path. The frontend domain (nekazari.robotika.cloud)
// has /api/vegetation routed to vegetation-prime-api via ingress.
// Using the host's VITE_API_URL (nkz.robotika.cloud) would be cross-origin
// and the httpOnly cookie won't be sent → 401 on every request.
export function useVegetationApi(): VegetationApiClient {
  return useMemo(
    () => new VegetationApiClient(getTenantId, '/api/vegetation'),
    []
  );
}
