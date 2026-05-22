-- SQL Schema for operations.location_polygons table
-- This table stores village-level polygon boundaries fetched from Bhuvan API

-- Ensure PostGIS extension is enabled
CREATE EXTENSION IF NOT EXISTS postgis;

-- Create the location_polygons table (multiple polygons per location)
CREATE TABLE IF NOT EXISTS operations.location_polygons (
    location_id VARCHAR(20) NOT NULL,
    polygon_index INT NOT NULL,

    village VARCHAR(100) NOT NULL,
    mandal VARCHAR(100),
    district VARCHAR(100) NOT NULL,
    state VARCHAR(100),

    polygon_geom GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    polygon_geojson JSONB,

    source VARCHAR(50) NOT NULL DEFAULT 'bhuvan',

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (location_id, polygon_index),
    CONSTRAINT fk_location_polygons_location_id
        FOREIGN KEY (location_id)
        REFERENCES operations.locations(location_id)
        ON DELETE CASCADE
);

-- Spatial index (critical for performance)
CREATE INDEX IF NOT EXISTS idx_location_polygons_geom
    ON operations.location_polygons USING GIST (polygon_geom);

CREATE INDEX IF NOT EXISTS idx_location_polygons_location
    ON operations.location_polygons (location_id);

CREATE INDEX IF NOT EXISTS idx_location_polygons_admin
    ON operations.location_polygons (LOWER(village), LOWER(mandal), LOWER(district), LOWER(state));

COMMENT ON TABLE operations.location_polygons IS
    'Stores village-level polygon boundaries from Bhuvan API. Supports multiple polygons per location.';

COMMENT ON COLUMN operations.location_polygons.polygon_geom IS
    'PostGIS MULTIPOLYGON in WGS84 (EPSG:4326)';
COMMENT ON COLUMN operations.location_polygons.polygon_geojson IS
    'Raw GeoJSON for debugging / API replay';

-- Auto update updated_at
CREATE OR REPLACE FUNCTION update_location_polygons_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_location_polygons_updated_at
    BEFORE UPDATE ON operations.location_polygons
    FOR EACH ROW
    EXECUTE PROCEDURE update_location_polygons_updated_at();

-- Grant permissions (adjust as needed for your database setup)
-- GRANT SELECT, INSERT, UPDATE ON operations.location_polygons TO your_app_user;

