-- Add reverse-geocoded location fields to operations.crop_indices.
-- Run after create_crop_indices_table.sql.
-- These are filled from polygon centroid via Nominatim (fallback: KML metadata).

ALTER TABLE operations.crop_indices
  ADD COLUMN IF NOT EXISTS village VARCHAR(200),
  ADD COLUMN IF NOT EXISTS town VARCHAR(200),
  ADD COLUMN IF NOT EXISTS district VARCHAR(200),
  ADD COLUMN IF NOT EXISTS state VARCHAR(200),
  ADD COLUMN IF NOT EXISTS country VARCHAR(200),
  ADD COLUMN IF NOT EXISTS postcode VARCHAR(50);

COMMENT ON COLUMN operations.crop_indices.village IS 'From reverse geocode (centroid) or KML metadata';
COMMENT ON COLUMN operations.crop_indices.district IS 'From reverse geocode (e.g. county/district)';
COMMENT ON COLUMN operations.crop_indices.state IS 'From reverse geocode';
