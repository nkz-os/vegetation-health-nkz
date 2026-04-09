/**
 * TypeScript type definitions for Vegetation Prime module.
 */

export type VegetationIndexType = 'NDVI' | 'EVI' | 'SAVI' | 'GNDVI' | 'NDRE' | 'NDMI' | 'CUSTOM' | 'SAMI' | 'VRA_ZONES';

export type JobType = 'download' | 'process' | 'calculate_index' | 'SENTINEL_INGEST' | 'ZONING';

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface VegetationJob {
  id: string;
  tenant_id: string;
  job_type: JobType;
  status: JobStatus;
  progress_percentage: number;
  progress_message?: string;
  created_at: string;
  updated_at?: string;
  started_at?: string;
  completed_at?: string;
  result?: Record<string, any>;
  error_message?: string;
  entity_id?: string;
  entity_type?: string;
  // Additional fields returned by backend
  entity_name?: string;
  scene_id?: string;
  index_type?: string;
  result_url?: string;
  result_histogram?: {
    bins: number[];
    counts: number[];
  };
  result_stats?: {
    mean?: number;
    min?: number;
    max?: number;
    std_dev?: number;
    pixel_count?: number;
  };
}

export interface VegetationScene {
  id: string;
  scene_id: string;
  sensing_date: string;
  cloud_coverage?: number;
  storage_path?: string;
  bands?: Record<string, string>;
  platform?: string;
  product_type?: string;
}

export interface VegetationIndex {
  id: string;
  scene_id: string;
  index_type: VegetationIndexType;
  mean_value: number;
  min_value: number;
  max_value: number;
  std_dev: number;
  pixel_count: number;
  calculated_at: string;
  result_raster_path?: string;
  result_tiles_path?: string;
}

export interface TimeseriesDataPoint {
  date: string;
  value: number;
  min?: number;
  max?: number;
  std?: number;
}

export interface VegetationConfig {
  tenant_id: string;
  default_index_type: VegetationIndexType;
  cloud_coverage_threshold: number;
  auto_process: boolean;
  storage_type: 's3' | 'minio' | 'local';
  storage_bucket?: string;
  copernicus_client_id?: string;
  copernicus_client_secret?: string;
}

export interface Bounds {
  type: 'Polygon';
  coordinates: number[][][];
}

export interface JobCreateParams {
  job_type: JobType;
  entity_id?: string;
  entity_type?: string;
  bounds?: Bounds;
  start_date?: string;
  end_date?: string;
  parameters?: Record<string, any>;
}

export interface IndexCalculationParams {
  scene_id?: string;
  index_type: VegetationIndexType;
  formula?: string;
  entity_id?: string;
  // Temporal composite options
  start_date?: string;
  end_date?: string;
}

// Slot component props
export interface VegetationLayerControlProps {
  selectedIndex?: VegetationIndexType;
  selectedDate?: string;
  onIndexChange?: (index: VegetationIndexType) => void;
  onDateChange?: (date: string) => void;
}

export interface TimelineWidgetProps {
  entityId?: string;
  indexType?: VegetationIndexType;
  onDateSelect?: (date: string) => void;
}


export interface SceneStats {
  scene_id: string;
  sensing_date: string;
  mean_value: number | null;
  min_value: number | null;
  max_value: number | null;
  std_dev: number | null;
  cloud_coverage: number | null;
  raster_path?: string | null;
}

export interface TimelineStatsResponse {
  entity_id: string;
  index_type: string;
  stats: SceneStats[];
  data_points: Array<{
    date: string;
    mean: number | null;
    min: number | null;
    max: number | null;
    std_dev: number | null;
  }>;
  summary: {
    avg: number | null;
    min: number | null;
    max: number | null;
    count: number;
  };
  months: number;
  period_start: string;
  period_end: string;
}

export interface YearComparisonResponse {
  entity_id: string;
  index_type: string;
  current_year: {
    year: number;
    stats: Array<{
      month: number;
      day: number;
      mean_value: number | null;
      sensing_date: string;
    }>;
  };
  previous_year: {
    year: number;
    stats: Array<{
      month: number;
      day: number;
      mean_value: number | null;
      sensing_date: string;
    }>;
  };
}

// ==========================================================================
// Ferrari Frontend Types
// ==========================================================================

/**
 * Anomaly check request parameters
 */
export interface AnomalyCheckParams {
  entity_id: string;
  index_type: VegetationIndexType;
  start_date?: string;
  end_date?: string;
  threshold_low?: number;
  threshold_high?: number;
}

/**
 * Anomaly check response
 */
export interface AnomalyCheckResponse {
  entity_id: string;
  index_type: string;
  anomalies: Array<{
    date: string;
    value: number;
    anomaly_type: 'low' | 'high' | 'sudden_change';
    severity: 'warning' | 'critical';
    message: string;
  }>;
  thresholds: {
    low: number;
    high: number;
  };
  analysis_period: {
    start: string;
    end: string;
  };
}

/**
 * Alert configuration
 */
export interface AlertConfig {
  entity_id?: string; // null = all entities
  index_type: VegetationIndexType;
  threshold_low?: number;
  threshold_high?: number;
  webhook_url?: string;
  enabled: boolean;
}

/**
 * Alert test response
 */
export interface AlertTestResponse {
  message: string;
  preview: string;
  would_trigger: boolean;
}

/**
 * Alert format response (for N8N integration)
 */
export interface AlertFormatResponse {
  format: 'json';
  example_payload: Record<string, any>;
  webhook_headers: Record<string, string>;
}

/**
 * Formula preview request
 */
export interface FormulaPreviewParams {
  entity_id: string;
  scene_id?: string;
  formula: string;
  bands?: Record<string, string>;
}

/**
 * Formula preview response
 */
export interface FormulaPreviewResponse {
  valid: boolean;
  preview_stats?: {
    mean: number;
    min: number;
    max: number;
    std_dev: number;
  };
  error?: string;
  thumbnail_url?: string;
}

export interface CustomFormula {
  id: string;
  name: string;
  description?: string | null;
  formula: string;
  is_validated: boolean;
  validation_error?: string | null;
  usage_count: number;
  last_used_at?: string | null;
  created_at?: string | null;
}

export interface CustomFormulaValidationResponse {
  valid: boolean;
  formula: string;
  bands: string[];
}

export interface EntityIndexResult {
  job_id: string;
  index_key: string;
  index_type: string;
  is_custom: boolean;
  formula_id?: string | null;
  formula_name?: string | null;
  statistics: {
    mean: number | null;
    min: number | null;
    max: number | null;
    std_dev: number | null;
    pixel_count: number | null;
  };
  raster_path: string | null;
  is_composite: boolean;
  created_at: string | null;
}

/**
 * Prediction response (Intelligence module integration)
 */
export interface PredictionResponse {
  entity_id: string;
  index_type: string;
  model_type: string;
  predictions: Array<{
    date: string;
    predicted_value: number;
    confidence_lower: number;
    confidence_upper: number;
  }>;
  webhook_metadata: Record<string, any>;
}

/**
 * Zoning trigger response
 */
export interface ZoningTriggerResponse {
  message: string;
  task_id: string;
  parcel_id: string;
  webhook_metadata: Record<string, any>;
}

/**
 * Zoning GeoJSON response
 */
export interface ZoningGeoJsonResponse {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    properties: {
      zone_id: number;
      zone_class: string;
      mean_value: number;
      area_ha: number;
      prescription_rate?: number;
    };
    geometry: {
      type: 'Polygon' | 'MultiPolygon';
      coordinates: number[][][] | number[][][][];
    };
  }>;
}

/**
 * Module capabilities for graceful degradation
 */
export interface ModuleCapabilities {
  n8n_available: boolean;
  intelligence_available: boolean;
  isobus_available: boolean;
  features: {
    predictions: boolean;
    alerts_webhook: boolean;
    export_isoxml: boolean;
    send_to_cloud: boolean;
  };
}

/**
 * Crop recommendation response
 */
export interface CropRecommendation {
  default_index: VegetationIndexType;
  valid_indices: VegetationIndexType[];
  crop_species: string;
  recommendations?: string[];
}

