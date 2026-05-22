-- Align operations.crop_indices with Excel / pipeline (additive only; no DROP).
-- Run after existing crop_indices migrations.
-- PostgreSQL 9.1+

-- A. Metadata / index columns (skip if already present from partial migrations)
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS extracted_grower TEXT;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS centroid_lat DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS centroid_lon DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS ndwi_gao DOUBLE PRECISION;

-- B. Sentinel-2 band means (no SENT2_ prefix)
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS blue DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS green DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS red DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS rededge1 DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS rededge2 DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS rededge3 DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS nir DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS narrow_nir DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS swir1 DOUBLE PRECISION;
ALTER TABLE operations.crop_indices ADD COLUMN IF NOT EXISTS swir2 DOUBLE PRECISION;

COMMENT ON COLUMN operations.crop_indices.centroid_lat IS 'Polygon centroid latitude, 7 dp';
COMMENT ON COLUMN operations.crop_indices.centroid_lon IS 'Polygon centroid longitude, 7 dp';
COMMENT ON COLUMN operations.crop_indices.ndwi_gao IS 'Gao NDWI / NDMI (SWIR1): (NIR - SWIR1) / (NIR + SWIR1)';
COMMENT ON COLUMN operations.crop_indices.blue IS 'Mean S2 B02';
COMMENT ON COLUMN operations.crop_indices.green IS 'Mean S2 B03';
COMMENT ON COLUMN operations.crop_indices.red IS 'Mean S2 B04';
COMMENT ON COLUMN operations.crop_indices.rededge1 IS 'Mean S2 B05';
COMMENT ON COLUMN operations.crop_indices.rededge2 IS 'Mean S2 B06';
COMMENT ON COLUMN operations.crop_indices.rededge3 IS 'Mean S2 B07';
COMMENT ON COLUMN operations.crop_indices.nir IS 'Mean S2 B08';
COMMENT ON COLUMN operations.crop_indices.narrow_nir IS 'Mean S2 B8A';
COMMENT ON COLUMN operations.crop_indices.swir1 IS 'Mean S2 B11';
COMMENT ON COLUMN operations.crop_indices.swir2 IS 'Mean S2 B12';
