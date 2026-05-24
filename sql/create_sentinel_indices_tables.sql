-- Satellite-specific index tables (operations schema).
-- Does NOT modify operations.crop_indices.
-- Run after create_satellite_raw_observation.sql and crop_indices exists.

CREATE SCHEMA IF NOT EXISTS operations;

-- UUID defaults for ingestion runs (PostgreSQL 13+ has gen_random_uuid() in core)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Ingestion run metadata (checkpoint / restart / monitoring)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operations.satellite_ingestion_run (
    run_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_version    TEXT NOT NULL DEFAULT 'v2',
    mode                TEXT NOT NULL,  -- backfill | incremental | reprocess_raw
    season_id           TEXT,
    kml_source          TEXT,           -- s3 | local
    time_range_start    DATE,
    time_range_end      DATE,
    status              TEXT NOT NULL DEFAULT 'running',  -- running|completed|failed|partial
    files_total         INTEGER DEFAULT 0,
    files_processed     INTEGER DEFAULT 0,
    files_failed        INTEGER DEFAULT 0,
    error_summary       JSONB,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_run_season_status
    ON operations.satellite_ingestion_run (season_id, status, started_at DESC);

CREATE TABLE IF NOT EXISTS operations.satellite_ingestion_checkpoint (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              UUID NOT NULL REFERENCES operations.satellite_ingestion_run(run_id) ON DELETE CASCADE,
    file_name           TEXT NOT NULL,
    location_id         VARCHAR(64),
    satellite           CHAR(2) NOT NULL,  -- S1|S2|S3
    stage               TEXT NOT NULL,   -- raw|harmonize|indices
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending|done|failed|skipped
    last_error          TEXT,
    raw_observation_id  BIGINT,
    processed_row_id    BIGINT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, file_name, satellite, stage)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_checkpoint_run_status
    ON operations.satellite_ingestion_checkpoint (run_id, status);

-- ---------------------------------------------------------------------------
-- Enhance raw observation table (linkage + reprocessing)
-- ---------------------------------------------------------------------------
ALTER TABLE operations.satellite_raw_observation
    ADD COLUMN IF NOT EXISTS run_id UUID,
    ADD COLUMN IF NOT EXISTS satellite CHAR(2),
    ADD COLUMN IF NOT EXISTS season_id TEXT,
    ADD COLUMN IF NOT EXISTS api_type TEXT,
    ADD COLUMN IF NOT EXISTS product_id TEXT,
    ADD COLUMN IF NOT EXISTS interval_start TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS interval_end TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cloud_cover_pct DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS valid_pixel_fraction DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS max_cloud_cover_pct DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS processing_status TEXT DEFAULT 'stored',
    ADD COLUMN IF NOT EXISTS payload_checksum TEXT,
    ADD COLUMN IF NOT EXISTS metadata JSONB;

CREATE INDEX IF NOT EXISTS idx_raw_satellite_date
    ON operations.satellite_raw_observation (satellite, observation_date);
CREATE INDEX IF NOT EXISTS idx_raw_run_id
    ON operations.satellite_raw_observation (run_id);
CREATE INDEX IF NOT EXISTS idx_raw_product_id
    ON operations.satellite_raw_observation (product_id) WHERE product_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Sentinel-2 (optical / vegetation / moisture / water)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operations.sentinel2_indices (
    id                      BIGSERIAL PRIMARY KEY,
    location_id             VARCHAR(64) NOT NULL,
    file_name               TEXT NOT NULL,
    season_id               TEXT,
    acquisition_date        DATE NOT NULL,
    product_id              TEXT,
    scene_cloud_cover_pct   DOUBLE PRECISION,
    valid_pixel_fraction    DOUBLE PRECISION,
    max_cloud_cover_pct     DOUBLE PRECISION NOT NULL DEFAULT 20,
    raw_observation_id      BIGINT REFERENCES operations.satellite_raw_observation(id),
    crop_indices_id         INTEGER,  -- optional lineage to legacy row
    -- Spectral bands (reflectance means); names match operations.crop_indices
    coastal                 DOUBLE PRECISION,
    blue                    DOUBLE PRECISION,
    green                   DOUBLE PRECISION,
    red                     DOUBLE PRECISION,
    rededge1                DOUBLE PRECISION,
    rededge2                DOUBLE PRECISION,
    rededge3                DOUBLE PRECISION,
    nir                     DOUBLE PRECISION,
    narrow_nir              DOUBLE PRECISION,
    cirrus                  DOUBLE PRECISION,
    swir1                   DOUBLE PRECISION,
    swir2                   DOUBLE PRECISION,
    -- Vegetation / crop health
    ndvi                    DOUBLE PRECISION,
    savi                    DOUBLE PRECISION,
    msavi                   DOUBLE PRECISION,
    evi                     DOUBLE PRECISION,
    lai                     DOUBLE PRECISION,
    gci                     DOUBLE PRECISION,
    -- Red edge / chlorophyll
    ndre                    DOUBLE PRECISION,
    ndre2                   DOUBLE PRECISION,
    cire                    DOUBLE PRECISION,
    mcari                   DOUBLE PRECISION,
    -- Moisture / water / stress
    ndmi                    DOUBLE PRECISION,
    ndwi                    DOUBLE PRECISION,
    ndwi_gao                DOUBLE PRECISION,
    mndwi                   DOUBLE PRECISION,
    psri                    DOUBLE PRECISION,
    -- Provenance: api | computed | hybrid
    index_sources           JSONB,
    bands_missing           TEXT[],
    indices_missing         TEXT[],
    pipeline_version        TEXT DEFAULT 'v2',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_s2_loc_file_season_date UNIQUE (location_id, file_name, season_id, acquisition_date)
);

CREATE INDEX IF NOT EXISTS idx_s2_location_date ON operations.sentinel2_indices (location_id, acquisition_date);
CREATE INDEX IF NOT EXISTS idx_s2_season_date ON operations.sentinel2_indices (season_id, acquisition_date);
CREATE INDEX IF NOT EXISTS idx_s2_raw_id ON operations.sentinel2_indices (raw_observation_id);

-- ---------------------------------------------------------------------------
-- Sentinel-1 (SAR / structure / moisture proxy)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operations.sentinel1_indices (
    id                      BIGSERIAL PRIMARY KEY,
    location_id             VARCHAR(64) NOT NULL,
    file_name               TEXT NOT NULL,
    season_id               TEXT,
    acquisition_date        DATE NOT NULL,
    product_id              TEXT,
    raw_observation_id      BIGINT REFERENCES operations.satellite_raw_observation(id),
    crop_indices_id         INTEGER,
    vv                      DOUBLE PRECISION,
    vh                      DOUBLE PRECISION,
    vv_db                   DOUBLE PRECISION,
    vh_db                   DOUBLE PRECISION,
    vh_vv_ratio             DOUBLE PRECISION,
    rvi                     DOUBLE PRECISION,
    cross_pol_ratio         DOUBLE PRECISION,
    dpsvi                   DOUBLE PRECISION,
    index_sources           JSONB,
    bands_missing           TEXT[],
    pipeline_version        TEXT DEFAULT 'v2',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_s1_loc_file_season_date UNIQUE (location_id, file_name, season_id, acquisition_date)
);

CREATE INDEX IF NOT EXISTS idx_s1_location_date ON operations.sentinel1_indices (location_id, acquisition_date);

-- ---------------------------------------------------------------------------
-- Sentinel-3 (thermal / LST)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operations.sentinel3_indices (
    id                      BIGSERIAL PRIMARY KEY,
    location_id             VARCHAR(64) NOT NULL,
    file_name               TEXT NOT NULL,
    season_id               TEXT,
    acquisition_date        DATE NOT NULL,
    product_id              TEXT,
    raw_observation_id      BIGINT REFERENCES operations.satellite_raw_observation(id),
    crop_indices_id         INTEGER,
    s7                      DOUBLE PRECISION,
    s8                      DOUBLE PRECISION,
    s9                      DOUBLE PRECISION,
    lst_k                   DOUBLE PRECISION,
    lst_celsius             DOUBLE PRECISION,
    lst_delta_k             DOUBLE PRECISION,
    index_sources           JSONB,
    bands_missing           TEXT[],
    pipeline_version        TEXT DEFAULT 'v2',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_s3_loc_file_season_date UNIQUE (location_id, file_name, season_id, acquisition_date)
);

CREATE INDEX IF NOT EXISTS idx_s3_location_date ON operations.sentinel3_indices (location_id, acquisition_date);

COMMENT ON TABLE operations.sentinel2_indices IS 'Sentinel-2 L2A derived bands and indices per field-day; cloud filter <= max_cloud_cover_pct';
COMMENT ON TABLE operations.sentinel1_indices IS 'Sentinel-1 GRD SAR metrics per field-day';
COMMENT ON TABLE operations.sentinel3_indices IS 'Sentinel-3 SLSTR thermal bands and LST per field-day';
