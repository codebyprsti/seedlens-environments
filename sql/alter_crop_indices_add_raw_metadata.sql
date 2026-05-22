-- Add raw metadata columns to operations.crop_indices for debugging and traceability.
-- Run after create_crop_indices_table.sql and alter_crop_indices_add_geocoded_location.sql (or migrate_crop_indices_add_missing_columns.sql).
-- Keeps existing: location_id, grower_id, variety_id.

-- Raw metadata from KML/filename (extracted village, grower, variety)
ALTER TABLE operations.crop_indices
  ADD COLUMN IF NOT EXISTS grower_name VARCHAR(200),
  ADD COLUMN IF NOT EXISTS variety_name VARCHAR(200);

-- Ensure geocoded/location columns exist (idempotent)
ALTER TABLE operations.crop_indices
  ADD COLUMN IF NOT EXISTS village VARCHAR(200),
  ADD COLUMN IF NOT EXISTS town VARCHAR(200),
  ADD COLUMN IF NOT EXISTS district VARCHAR(200),
  ADD COLUMN IF NOT EXISTS state VARCHAR(200),
  ADD COLUMN IF NOT EXISTS country VARCHAR(200),
  ADD COLUMN IF NOT EXISTS postcode VARCHAR(50);

COMMENT ON COLUMN operations.crop_indices.grower_name IS 'Raw grower name from KML/filename metadata';
COMMENT ON COLUMN operations.crop_indices.variety_name IS 'Raw variety name from KML/filename metadata';
COMMENT ON COLUMN operations.crop_indices.village IS 'Village from reverse geocode or KML metadata';
