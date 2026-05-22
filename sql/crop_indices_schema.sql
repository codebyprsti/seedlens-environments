-- Crop monitoring indices table (operations schema).
-- Resolved IDs: location_id (village), grower_id, variety_id from master tables.
-- analysis_date = date of analysis (typically end of the requested range).

CREATE SCHEMA IF NOT EXISTS operations;

CREATE TABLE IF NOT EXISTS operations.crop_indices (
    id SERIAL PRIMARY KEY,
    location_id VARCHAR(20) REFERENCES operations.locations(location_id),
    grower_id VARCHAR(20) REFERENCES operations.growers(grower_id),
    variety_id VARCHAR(20) REFERENCES operations.varieties(variety_id),
    polygon_id VARCHAR(50) NULL,
    polygon_area DOUBLE PRECISION,
    date_start DATE,
    date_end DATE,
    analysis_date DATE DEFAULT CURRENT_DATE,
    ndvi DOUBLE PRECISION,
    savi DOUBLE PRECISION,
    ndmi DOUBLE PRECISION,
    ndre DOUBLE PRECISION,
    gci DOUBLE PRECISION,
    psri DOUBLE PRECISION,
    msavi DOUBLE PRECISION,
    evi DOUBLE PRECISION,
    lai DOUBLE PRECISION,
    lst_c DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON COLUMN operations.crop_indices.location_id IS 'Resolved from locations (village)';
COMMENT ON COLUMN operations.crop_indices.analysis_date IS 'Date of analysis run';
COMMENT ON COLUMN operations.crop_indices.lst_c IS 'Land surface temperature Celsius';

CREATE INDEX IF NOT EXISTS idx_crop_indices_location ON operations.crop_indices(location_id);
CREATE INDEX IF NOT EXISTS idx_crop_indices_dates ON operations.crop_indices(date_start, date_end);
CREATE INDEX IF NOT EXISTS idx_crop_indices_analysis_date ON operations.crop_indices(analysis_date);
