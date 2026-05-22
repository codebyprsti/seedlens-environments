-- Add extracted_grower to operations.crop_indices (grower name from KML metadata extraction).
-- Run once.

ALTER TABLE operations.crop_indices
  ADD COLUMN IF NOT EXISTS extracted_grower VARCHAR(200);

COMMENT ON COLUMN operations.crop_indices.extracted_grower IS 'Grower name extracted from KML placemark (entity extractor); grower_id is resolved master.';
