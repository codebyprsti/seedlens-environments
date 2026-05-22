-- Add Sentinel-1 SAR metrics to operations.crop_indices.
-- Run after create_crop_indices_table.sql and any prior alters.

ALTER TABLE operations.crop_indices
ADD COLUMN IF NOT EXISTS vv_db DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS vh_db DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS vh_vv_ratio DOUBLE PRECISION;
