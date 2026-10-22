-- 013_add_copernicus_analyze_job_type.sql
-- Allow 'copernicus_analyze' as a valid job_type: the season analysis records
-- one such job per request and computes the Copernicus indices in the worker.
-- Expand-only: existing values are kept.

ALTER TABLE vegetation_jobs DROP CONSTRAINT IF EXISTS vegetation_jobs_job_type_check;

ALTER TABLE vegetation_jobs ADD CONSTRAINT vegetation_jobs_job_type_check
    CHECK (job_type IN ('download', 'process', 'calculate_index', 'download_sar', 'copernicus_analyze'));
