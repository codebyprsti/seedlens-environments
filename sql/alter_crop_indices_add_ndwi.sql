-- Add NDWI (Normalized Difference Water Index) to operations.crop_indices.
-- Formula: (Green - NIR) / (Green + NIR); bands B03, B08.

ALTER TABLE operations.crop_indices
ADD COLUMN IF NOT EXISTS ndwi DOUBLE PRECISION;

COMMENT ON COLUMN operations.crop_indices.ndwi IS 'NDWI = (B03 - B08)/(B03 + B08); water / moisture from Sentinel-2.';
