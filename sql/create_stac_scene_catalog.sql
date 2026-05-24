-- STAC / Catalogue metadata per field-day (links raw ↔ processed layers).

CREATE SCHEMA IF NOT EXISTS operations;

CREATE TABLE IF NOT EXISTS operations.stac_scene_catalog (
    id                  BIGSERIAL PRIMARY KEY,
    location_id         VARCHAR(64) NOT NULL,
    file_name           TEXT NOT NULL,
    season_id           TEXT,
    satellite           CHAR(2) NOT NULL DEFAULT 'S2',
    acquisition_date    DATE NOT NULL,
    product_id          TEXT NOT NULL,
    collection_id       TEXT,
    sensing_time        TIMESTAMPTZ,
    cloud_cover_pct     DOUBLE PRECISION,
    orbit_direction     TEXT,
    processing_baseline TEXT,
    bbox                JSONB,
    geometry_footprint  JSONB,
    stac_properties     JSONB,
    raw_observation_id  BIGINT REFERENCES operations.satellite_raw_observation(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (location_id, file_name, season_id, satellite, acquisition_date, product_id)
);

CREATE INDEX IF NOT EXISTS idx_stac_loc_date
    ON operations.stac_scene_catalog (location_id, acquisition_date);
CREATE INDEX IF NOT EXISTS idx_stac_product
    ON operations.stac_scene_catalog (product_id);
