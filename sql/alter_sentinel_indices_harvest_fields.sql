-- Harvest batch: grower linkage, observation_date, time-series-safe uniqueness.
-- Does NOT modify operations.crop_indices.

-- ---------------------------------------------------------------------------
-- Field registry (cached mappings — no Google API)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operations.harvest_field_registry (
    internal_id         VARCHAR(32) PRIMARY KEY,
    location_id         VARCHAR(64) NOT NULL,
    season_id           TEXT NOT NULL DEFAULT 'RABI_25_26',
    file_name           TEXT NOT NULL,
    legacy_file_name    TEXT,
    grower_name         TEXT,
    grower_id           VARCHAR(64),
    kml_path            TEXT,
    state_code          CHAR(2),
    validation_status   TEXT NOT NULL DEFAULT 'ok',
    validation_notes    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_harvest_registry_loc_season
    ON operations.harvest_field_registry (location_id, season_id);

-- ---------------------------------------------------------------------------
-- Sentinel-1/2/3: grower + internal_id + observation_date
-- ---------------------------------------------------------------------------
ALTER TABLE operations.sentinel1_indices
    ADD COLUMN IF NOT EXISTS internal_id VARCHAR(32),
    ADD COLUMN IF NOT EXISTS grower_name TEXT,
    ADD COLUMN IF NOT EXISTS grower_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS observation_date DATE;

ALTER TABLE operations.sentinel2_indices
    ADD COLUMN IF NOT EXISTS internal_id VARCHAR(32),
    ADD COLUMN IF NOT EXISTS grower_name TEXT,
    ADD COLUMN IF NOT EXISTS grower_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS observation_date DATE;

ALTER TABLE operations.sentinel3_indices
    ADD COLUMN IF NOT EXISTS internal_id VARCHAR(32),
    ADD COLUMN IF NOT EXISTS grower_name TEXT,
    ADD COLUMN IF NOT EXISTS grower_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS observation_date DATE;

-- Backfill observation_date from acquisition_date
UPDATE operations.sentinel1_indices SET observation_date = acquisition_date WHERE observation_date IS NULL;
UPDATE operations.sentinel2_indices SET observation_date = acquisition_date WHERE observation_date IS NULL;
UPDATE operations.sentinel3_indices SET observation_date = acquisition_date WHERE observation_date IS NULL;

-- Raw table: internal_id + grower for audit
ALTER TABLE operations.satellite_raw_observation
    ADD COLUMN IF NOT EXISTS internal_id VARCHAR(32),
    ADD COLUMN IF NOT EXISTS grower_name TEXT,
    ADD COLUMN IF NOT EXISTS grower_id VARCHAR(64);

-- Time-series uniqueness: location + season + observation day + parcel (internal_id)
-- Drop legacy constraints if present (idempotent via DO block)
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_s1_loc_season_obs_internal
    ON operations.sentinel1_indices (location_id, season_id, observation_date, internal_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_s2_loc_season_obs_internal
    ON operations.sentinel2_indices (location_id, season_id, observation_date, internal_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_s3_loc_season_obs_internal
    ON operations.sentinel3_indices (location_id, season_id, observation_date, internal_id);

CREATE INDEX IF NOT EXISTS idx_s2_grower ON operations.sentinel2_indices (grower_id);
CREATE INDEX IF NOT EXISTS idx_s2_internal ON operations.sentinel2_indices (internal_id);

COMMENT ON COLUMN operations.sentinel2_indices.observation_date IS
    'Calendar day of observation (time series); same as acquisition_date';
