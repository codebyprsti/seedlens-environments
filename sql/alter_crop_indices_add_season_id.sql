-- Add season_id to operations.crop_indices for season tracking (e.g. RABI_25_26).
-- Run after create_crop_indices_table.sql and any prior alters.

ALTER TABLE operations.crop_indices
ADD COLUMN IF NOT EXISTS season_id TEXT;

COMMENT ON COLUMN operations.crop_indices.season_id IS 'Crop season identifier (e.g. RABI_25_26); identifies observations per season.';
