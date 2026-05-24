-- Sentinel-2 cloud quality columns + GNDVI/MSI (pixel-masked pipeline v3).

ALTER TABLE operations.sentinel2_indices
    ADD COLUMN IF NOT EXISTS gndvi DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS msi DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS valid_pixel_percentage DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS cloud_pixel_percentage DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS shadow_pixel_percentage DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS masked_pixel_percentage DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS usable_scene BOOLEAN,
    ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION;

ALTER TABLE operations.sentinel2_indices
    ALTER COLUMN max_cloud_cover_pct SET DEFAULT 60;

COMMENT ON COLUMN operations.sentinel2_indices.valid_pixel_percentage IS
    'Fraction of polygon pixels clear after SCL/dataMask (not tile-level cloud filter)';
COMMENT ON COLUMN operations.sentinel2_indices.usable_scene IS
    'False when valid_pixel_percentage < S2_MIN_VALID_PIXEL_PCT (default 30)';

ALTER TABLE operations.satellite_raw_observation
    ADD COLUMN IF NOT EXISTS valid_pixel_percentage DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS cloud_pixel_percentage DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS usable_scene BOOLEAN,
    ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION;
