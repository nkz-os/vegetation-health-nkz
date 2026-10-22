-- 010_add_download_sar_job_type.sql
-- Allow 'download_sar' as a valid job_type in the CHECK constraint.
-- The model was updated to include this type, but the DB constraint
-- needs to be altered separately since SQLAlchemy create_all does not
-- modify existing constraints.

ALTER TABLE vegetation_jobs DROP CONSTRAINT IF EXISTS vegetation_jobs_job_type_check;

ALTER TABLE vegetation_jobs ADD CONSTRAINT vegetation_jobs_job_type_check
    CHECK (job_type IN ('download', 'process', 'calculate_index', 'download_sar'));
