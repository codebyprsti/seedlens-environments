-- Add field-related columns to operations.crop_indices and enforce unique observation.
-- Run after create_field_locations_table.sql if crop_indices.location_id will reference field_locations.

-- New columns for KML-extracted and geocoded village
ALTER TABLE operations.crop_indices
  ADD COLUMN IF NOT EXISTS area_acre         DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS distance_km       DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS extracted_village VARCHAR(200);

COMMENT ON COLUMN operations.crop_indices.area_acre         IS 'Area in acres from KML';
COMMENT ON COLUMN operations.crop_indices.distance_km       IS 'Distance in km from KML';
COMMENT ON COLUMN operations.crop_indices.extracted_village IS 'Village name from KML (declared); village = geocoded village';

-- Unique constraint: one observation per (location_id, start_date, variety_id, file_name)
-- Use date_start as start_date; create unique index only if column exists.
-- Prefer sql/alter_crop_indices_unique_natural_key.sql: date_start can collide across Sentinel buckets;
-- the pipeline now sets date_start = analysis_date and keys uniqueness on analysis_date + file_name.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'operations' AND table_name = 'crop_indices' AND column_name = 'date_start'
  ) THEN
    CREATE UNIQUE INDEX IF NOT EXISTS uq_crop_indices_location_start_variety_file
      ON operations.crop_indices (location_id, date_start, variety_id, file_name);
  END IF;
END $$;
