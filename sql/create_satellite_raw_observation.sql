-- Raw satellite API payloads (JSONB) for audit and reprocessing.
-- Note: Multiple rows per location_id are expected (time series, sources). Surrogate PK is required.

CREATE SCHEMA IF NOT EXISTS operations;

CREATE TABLE IF NOT EXISTS operations.satellite_raw_observation (
    id BIGSERIAL PRIMARY KEY,
    location_id VARCHAR(64) NOT NULL,
    file_name TEXT NOT NULL,
    source TEXT NOT NULL,
    observation_date DATE,
    raw_response JSONB,
    bands JSONB,
    indices JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_location_id
    ON operations.satellite_raw_observation (location_id);

CREATE INDEX IF NOT EXISTS idx_raw_file_name
    ON operations.satellite_raw_observation (file_name);

CREATE INDEX IF NOT EXISTS idx_raw_date
    ON operations.satellite_raw_observation (observation_date);
