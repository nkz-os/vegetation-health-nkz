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
  WeatherData,
  WeatherInterpretation,
  WeatherSensor,
  FormulaPreviewParams,
  FormulaPreviewResponse,
  SendToCloudResponse,
  ModuleCapabilities
} from '../types';

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
  // Helper to get API URL
  // Helper to get API URL
  private getBaseApiUrl(): string {
    if (typeof window !== 'undefined' && (window as any).__ENV__ && (window as any).__ENV__.API_URL) {
      return (window as any).__ENV__.API_URL;
    }
    // Fallback for local dev or if env not set
    return '';
  }

  /**
   * List all AgriParcel entities for the current tenant
   * Uses the main Nekazari API (NGSI-LD broker via gateway)
   */
  async listTenantParcels(): Promise<any[]> {
    try {
      const token = this.getToken();

      // Minimal headers - match what the host sends
      const headers: Record<string, string> = {};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      // Use absolute URL from env if available
      const baseUrl = this.getBaseApiUrl();
      const url = `${baseUrl}/ngsi-ld/v1/entities?type=AgriParcel`;

      const response = await fetch(url, {
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
   * Get a single entity by ID
   */
  async getEntity(entityId: string): Promise<any> {
    try {
      const token = this.getToken();
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const baseUrl = this.getBaseApiUrl();
      const url = `${baseUrl}/ngsi-ld/v1/entities/${entityId}`;

      const response = await fetch(url, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        // Log error body if possible
        try {
          const errText = await response.text();
          console.error(`[VegetationApi] Fetch entity failed ${response.status}:`, errText.substring(0, 200));
        } catch (e) { }
        throw new Error(`Failed to fetch entity: ${response.status}`);
      }
      return response.json();
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

  // ==========================================================================
  // Ferrari Frontend - Export Methods
  // ==========================================================================

  /**
   * Export prescription map as GeoJSON
   */
  async exportPrescriptionGeojson(parcelId: string): Promise<Blob> {
    const token = this.getToken();
    const tenantId = this.getTenantId();

    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (tenantId) headers['X-Tenant-ID'] = tenantId;

    const response = await fetch(`/api/vegetation/export/${encodeURIComponent(parcelId)}/geojson`, {
      method: 'GET',
      headers,
    });

    if (!response.ok) {
      throw new Error(`Export GeoJSON failed: ${response.status}`);
    }

    return response.blob();
  }

  /**
   * Export prescription map as Shapefile (zip)
   */
  async exportPrescriptionShapefile(parcelId: string): Promise<Blob> {
    const token = this.getToken();
    const tenantId = this.getTenantId();

    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (tenantId) headers['X-Tenant-ID'] = tenantId;

    const response = await fetch(`/api/vegetation/export/${encodeURIComponent(parcelId)}/shapefile`, {
      method: 'GET',
      headers,
    });

    if (!response.ok) {
      throw new Error(`Export Shapefile failed: ${response.status}`);
    }

    return response.blob();
  }

  /**
   * Export prescription map as CSV
   */
  async exportPrescriptionCsv(parcelId: string): Promise<Blob> {
    const token = this.getToken();
    const tenantId = this.getTenantId();

    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (tenantId) headers['X-Tenant-ID'] = tenantId;

    const response = await fetch(`/api/vegetation/export/${encodeURIComponent(parcelId)}/csv`, {
      method: 'GET',
      headers,
    });

    if (!response.ok) {
      throw new Error(`Export CSV failed: ${response.status}`);
    }

    return response.blob();
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
  // Ferrari Frontend - Weather Data
  // ==========================================================================

  /**
   * Get weather data for an entity
   */
  async getWeather(entityId: string): Promise<WeatherData> {
    const response = await this.client.get(`/weather/${encodeURIComponent(entityId)}`);
    return response as unknown as WeatherData;
  }

  /**
   * Get weather interpretation for an entity
   */
  async getWeatherInterpretation(entityId: string): Promise<WeatherInterpretation> {
    const response = await this.client.get(`/weather/${encodeURIComponent(entityId)}/interpret`);
    return response as unknown as WeatherInterpretation;
  }

  /**
   * Get weather sensors for an entity
   */
  async getWeatherSensors(entityId: string): Promise<WeatherSensor[]> {
    const response = await this.client.get(`/weather/${encodeURIComponent(entityId)}/sensors`);
    return response as unknown as WeatherSensor[];
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

  // ==========================================================================
  // Ferrari Frontend - Send to Cloud (N8N)
  // ==========================================================================

  /**
   * Send prescription map to machinery cloud via N8N
   * This goes through our backend which proxies to N8N (security: CORS + credentials)
   */
  async sendToCloud(
    parcelId: string,
    payload?: {
      prescription_type?: string;
      zones?: any;
      metadata?: Record<string, any>;
    }
  ): Promise<SendToCloudResponse> {
    try {
      const response = await this.client.post('/export/n8n', {
        parcel_id: parcelId,
        ...payload
      });
      return response as unknown as SendToCloudResponse;
    } catch (error) {
      console.warn('[API] Send to cloud failed:', error);
      throw error;
    }
  }

  // ==========================================================================
  // Ferrari Frontend - Module Capabilities (Graceful Degradation)
  // ==========================================================================

  /**
   * Get module capabilities for graceful degradation
   * Returns which optional integrations (N8N, Intelligence, ISOBUS) are available
   */
  async getCapabilities(): Promise<ModuleCapabilities> {
    try {
      const response = await this.client.get('/capabilities');
      return response as unknown as ModuleCapabilities;
    } catch (error) {
      // If endpoint doesn't exist, return conservative defaults
      console.warn('[API] Capabilities check failed, using defaults:', error);
      return {
        n8n_available: false,
        intelligence_available: false,
        isobus_available: false,
        features: {
          predictions: false,
          alerts_webhook: false,
          export_isoxml: false,
          send_to_cloud: false
        }
      };
    }
  }

  /**
   * Check if ISOBUS module is available
   * Uses host-provided callback or route check
   */
  isIsobusAvailable(): boolean {
    try {
      // Check if host provides ISOBUS callback
      if (typeof (window as any).__nekazariOpenISOBUS === 'function') {
        return true;
      }
      // Check if host has ISOBUS route registered
      const hostModules = (window as any).__nekazariModules;
      if (hostModules && Array.isArray(hostModules)) {
        return hostModules.some((m: any) => m.id === 'isobus' || m.id === 'nkz-isobus');
      }
      return false;
    } catch {
      return false;
    }
  }
}

// Hook for using API client
import { useMemo } from 'react';

// Get auth token like catastro module does - from window.keycloak
const getAuthToken = (): string | undefined => {
  if (typeof window === 'undefined') return undefined;

  // Try Keycloak instance first (same as working catastro module)
  const keycloakInstance = (window as any).keycloak;
  if (keycloakInstance && keycloakInstance.token) {
    return keycloakInstance.token;
  }

  // Fallback to __nekazariAuthContext
  const hostAuth = (window as any).__nekazariAuthContext;
  if (hostAuth && typeof hostAuth.getToken === 'function') {
    return hostAuth.getToken();
  }

  // Last fallback to localStorage
  const storedToken = localStorage.getItem('auth_token');
  if (storedToken) return storedToken;

  return undefined;
};

// Get tenant ID from token (same as working catastro module)
const getTenantId = (): string | undefined => {
  // Try __nekazariAuthContext first
  const hostAuth = (window as any).__nekazariAuthContext;
  if (hostAuth && hostAuth.tenantId) {
    return hostAuth.tenantId;
  }

  // Decode from token
  const token = getAuthToken();
  if (!token) return undefined;

  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(
      window.atob(base64)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    const decoded = JSON.parse(jsonPayload);
    return decoded['tenant-id'] || decoded.tenant_id || decoded.tenantId || decoded.tenant || undefined;
  } catch (e) {
    console.warn('[VegetationApi] Failed to decode token for tenant', e);
    return undefined;
  }
};

// Helper to get base API URL (repeated here for closure access if needed, or better expose static)
const getApiBaseUrl = () => {
  if (typeof window !== 'undefined' && (window as any).__ENV__ && (window as any).__ENV__.API_URL) {
    return (window as any).__ENV__.API_URL;
  }
  return '';
};

export function useVegetationApi(): VegetationApiClient {
  return useMemo(
    () => {
      const baseUrl = getApiBaseUrl();
      // If baseUrl is present (e.g. https://nkz.artotxiki.com), append /api/vegetation
      // otherwise default to relative path /api/vegetation
      const apiPath = baseUrl ? `${baseUrl}/api/vegetation` : '/api/vegetation';
      return new VegetationApiClient(getAuthToken, getTenantId, apiPath);
    },
    []
  );
}
