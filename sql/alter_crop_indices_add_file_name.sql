-- Add file_name to operations.crop_indices for batch/time-series uniqueness (file_name, analysis_date).
-- Run once. Safe to run if column already exists (use IF NOT EXISTS where supported).

ALTER TABLE operations.crop_indices
ADD COLUMN IF NOT EXISTS file_name VARCHAR(512) NULL;

COMMENT ON COLUMN operations.crop_indices.file_name IS 'KML/S3 object name for batch runs; uniqueness with analysis_date';

CREATE INDEX IF NOT EXISTS idx_crop_indices_file_analysis
ON operations.crop_indices(file_name, analysis_date)
WHERE file_name IS NOT NULL;
