-- Allow crop_indices.location_id to store field_locations IDs (IND-*) instead of only operations.locations (L_*).
-- Run once before using field_locations in the batch pipeline.

ALTER TABLE operations.crop_indices
  DROP CONSTRAINT IF EXISTS crop_indices_location_id_fkey;

-- Optional: add FK to field_locations (only if all existing location_id values are in field_locations or NULL)
-- ALTER TABLE operations.crop_indices
--   ADD CONSTRAINT crop_indices_location_id_fkey
--   FOREIGN KEY (location_id) REFERENCES operations.field_locations(location_id);
