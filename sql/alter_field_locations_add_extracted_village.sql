-- Add extracted_village: village from KML Placemark <name> (first token, normalized).
-- Enables comparison with reverse-geocode village for validation.
-- Run once. (If column exists, skip or use IF NOT EXISTS where supported.)

ALTER TABLE operations.field_locations
ADD COLUMN extracted_village TEXT;

COMMENT ON COLUMN operations.field_locations.extracted_village IS 'Village extracted from KML Placemark name (first token); compare with village from reverse geocode';
