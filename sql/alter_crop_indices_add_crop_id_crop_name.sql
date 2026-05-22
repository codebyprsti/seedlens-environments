-- Add crop_id and crop_name to operations.crop_indices for variety->crop linkage.
-- Run once: psql or apply via your migration process.

ALTER TABLE operations.crop_indices
  ADD COLUMN IF NOT EXISTS crop_id VARCHAR(20) REFERENCES operations.crops(crop_id),
  ADD COLUMN IF NOT EXISTS crop_name VARCHAR(100);

COMMENT ON COLUMN operations.crop_indices.crop_id IS 'Crop linked via variety (operations.crops)';
COMMENT ON COLUMN operations.crop_indices.crop_name IS 'Denormalized crop name for display';
