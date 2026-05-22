-- Crop monitoring indices storage (operations schema).
-- Run once to create the table.
-- location_id = village_id (from operations.locations); IDs resolved via database/repository.

CREATE SCHEMA IF NOT EXISTS operations;

CREATE TABLE IF NOT EXISTS operations.crop_indices (
    id SERIAL PRIMARY KEY,
    location_id VARCHAR(20) REFERENCES operations.locations(location_id),
    grower_id VARCHAR(20) REFERENCES operations.growers(grower_id),
    variety_id VARCHAR(20) REFERENCES operations.varieties(variety_id),
    polygon_area DOUBLE PRECISION,
    date_start DATE,
    date_end DATE,
    ndvi DOUBLE PRECISION,
    savi DOUBLE PRECISION,
    ndmi DOUBLE PRECISION,
    ndre DOUBLE PRECISION,
    gci DOUBLE PRECISION,
    psri DOUBLE PRECISION,
    msavi DOUBLE PRECISION,
    evi DOUBLE PRECISION,
    lai DOUBLE PRECISION,
    lst_celsius DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_crop_indices_location ON operations.crop_indices(location_id);
CREATE INDEX IF NOT EXISTS idx_crop_indices_dates ON operations.crop_indices(date_start, date_end);
