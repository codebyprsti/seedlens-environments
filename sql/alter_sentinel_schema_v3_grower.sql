-- Sentinel schema v3: grower linkage, standardized metadata, time-series uniqueness.
-- Rollback-safe: additive first; drops only legacy/redundant columns.
-- Does NOT modify operations.crop_indices.

-- ---------------------------------------------------------------------------
-- Standardized metadata columns
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY['sentinel1_indices', 'sentinel2_indices', 'sentinel3_indices'])
    LOOP
        EXECUTE format('
            ALTER TABLE operations.%I
                ADD COLUMN IF NOT EXISTS satellite_source CHAR(2),
                ADD COLUMN IF NOT EXISTS cloud_coverage DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS orbit_direction TEXT,
                ADD COLUMN IF NOT EXISTS processing_level TEXT
        ', t);
    END LOOP;
END $$;

UPDATE operations.sentinel1_indices SET satellite_source = 'S1' WHERE satellite_source IS NULL;
UPDATE operations.sentinel2_indices SET satellite_source = 'S2' WHERE satellite_source IS NULL;
UPDATE operations.sentinel3_indices SET satellite_source = 'S3' WHERE satellite_source IS NULL;

UPDATE operations.sentinel2_indices
SET cloud_coverage = scene_cloud_cover_pct
WHERE cloud_coverage IS NULL AND scene_cloud_cover_pct IS NOT NULL;

UPDATE operations.sentinel1_indices SET processing_level = COALESCE(processing_level, 'GRD');
UPDATE operations.sentinel2_indices SET processing_level = COALESCE(processing_level, 'L2A');
UPDATE operations.sentinel3_indices SET processing_level = COALESCE(processing_level, 'L1B');

-- Ensure observation_date populated
UPDATE operations.sentinel1_indices SET observation_date = acquisition_date WHERE observation_date IS NULL;
UPDATE operations.sentinel2_indices SET observation_date = acquisition_date WHERE observation_date IS NULL;
UPDATE operations.sentinel3_indices SET observation_date = acquisition_date WHERE observation_date IS NULL;

-- ---------------------------------------------------------------------------
-- Drop redundant / legacy columns (safe if absent)
-- ---------------------------------------------------------------------------
ALTER TABLE operations.sentinel1_indices DROP COLUMN IF EXISTS crop_indices_id;
ALTER TABLE operations.sentinel2_indices DROP COLUMN IF EXISTS crop_indices_id;
ALTER TABLE operations.sentinel3_indices DROP COLUMN IF EXISTS crop_indices_id;

ALTER TABLE operations.sentinel2_indices DROP COLUMN IF EXISTS valid_pixel_fraction;

-- ---------------------------------------------------------------------------
-- Uniqueness: surrogate PK (id) + daily time series per satellite
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_s1_loc_file_season_date') THEN
        ALTER TABLE operations.sentinel1_indices DROP CONSTRAINT uq_s1_loc_file_season_date;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_s2_loc_file_season_date') THEN
        ALTER TABLE operations.sentinel2_indices DROP CONSTRAINT uq_s2_loc_file_season_date;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_s3_loc_file_season_date') THEN
        ALTER TABLE operations.sentinel3_indices DROP CONSTRAINT uq_s3_loc_file_season_date;
    END IF;
END $$;

DROP INDEX IF EXISTS operations.uq_s1_loc_season_obs_internal;
DROP INDEX IF EXISTS operations.uq_s2_loc_season_obs_internal;
DROP INDEX IF EXISTS operations.uq_s3_loc_season_obs_internal;

CREATE UNIQUE INDEX IF NOT EXISTS uq_s1_loc_season_acq_sat
    ON operations.sentinel1_indices (location_id, season_id, acquisition_date, satellite_source);
CREATE UNIQUE INDEX IF NOT EXISTS uq_s2_loc_season_acq_sat
    ON operations.sentinel2_indices (location_id, season_id, acquisition_date, satellite_source);
CREATE UNIQUE INDEX IF NOT EXISTS uq_s3_loc_season_acq_sat
    ON operations.sentinel3_indices (location_id, season_id, acquisition_date, satellite_source);

CREATE INDEX IF NOT EXISTS idx_s1_grower_id ON operations.sentinel1_indices (grower_id);
CREATE INDEX IF NOT EXISTS idx_s2_grower_id ON operations.sentinel2_indices (grower_id);
CREATE INDEX IF NOT EXISTS idx_s3_grower_id ON operations.sentinel3_indices (grower_id);

COMMENT ON COLUMN operations.sentinel2_indices.cloud_coverage IS
    'Scene-level cloud cover %% (from STAC); pixel mask in valid_pixel_percentage';
COMMENT ON COLUMN operations.sentinel2_indices.satellite_source IS 'S1 | S2 | S3';
