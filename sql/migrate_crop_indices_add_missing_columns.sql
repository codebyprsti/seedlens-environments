-- Ensure operations.crop_indices has all columns required by the pipeline.
-- Run this if you get "column lst_celsius does not exist" or missing village/town/district etc.

-- 1) Add lst_celsius if missing (older tables may not have it)
ALTER TABLE operations.crop_indices
  ADD COLUMN IF NOT EXISTS lst_celsius DOUBLE PRECISION;

-- 2) Add reverse-geocoded location columns if missing
ALTER TABLE operations.crop_indices
  ADD COLUMN IF NOT EXISTS village VARCHAR(200),
  ADD COLUMN IF NOT EXISTS town VARCHAR(200),
  ADD COLUMN IF NOT EXISTS district VARCHAR(200),
  ADD COLUMN IF NOT EXISTS state VARCHAR(200),
  ADD COLUMN IF NOT EXISTS country VARCHAR(200),
  ADD COLUMN IF NOT EXISTS postcode VARCHAR(50);
