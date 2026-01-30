-- SQL Schema for operations.location_polygons table
-- This table stores village-level polygon boundaries fetched from Bhuvan API

-- Ensure PostGIS extension is enabled
CREATE EXTENSION IF NOT EXISTS postgis;

-- Create the location_polygons table
CREATE TABLE IF NOT EXISTS operations.location_polygons (
    -- Primary key: location_id (foreign key to operations.locations)
    location_id VARCHAR(20) PRIMARY KEY,
    
    -- Administrative details (denormalized for quick access)
    village VARCHAR(100) NOT NULL,
    mandal VARCHAR(100),
    district VARCHAR(100) NOT NULL,
    state VARCHAR(100),
    
    -- Polygon geometry stored as PostGIS geometry
    -- Supports both Polygon and MultiPolygon types
    -- Using GEOMETRY instead of POLYGON to support MultiPolygon
    polygon_geom GEOMETRY(GEOMETRY, 4326) NOT NULL,
    
    -- Source identifier
    source VARCHAR(50) NOT NULL DEFAULT 'bhuvan',
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Foreign key constraint
    CONSTRAINT fk_location_polygons_location_id 
        FOREIGN KEY (location_id) 
        REFERENCES operations.locations(location_id) 
        ON DELETE CASCADE,
    
    -- Ensure one polygon per location_id
    CONSTRAINT unique_location_polygon UNIQUE (location_id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_location_polygons_village 
    ON operations.location_polygons(LOWER(village));

CREATE INDEX IF NOT EXISTS idx_location_polygons_district 
    ON operations.location_polygons(LOWER(district));

CREATE INDEX IF NOT EXISTS idx_location_polygons_state 
    ON operations.location_polygons(LOWER(state));

-- Spatial index for geometry queries
CREATE INDEX IF NOT EXISTS idx_location_polygons_geom 
    ON operations.location_polygons USING GIST (polygon_geom);

-- Composite index for common query patterns
CREATE INDEX IF NOT EXISTS idx_location_polygons_admin 
    ON operations.location_polygons(LOWER(village), LOWER(mandal), LOWER(district), LOWER(state));

-- Add comment to table
COMMENT ON TABLE operations.location_polygons IS 
    'Stores village-level polygon boundaries fetched from Bhuvan API. One polygon per location_id.';

-- Add comments to columns
COMMENT ON COLUMN operations.location_polygons.location_id IS 
    'Foreign key to operations.locations.location_id';
COMMENT ON COLUMN operations.location_polygons.polygon_geom IS 
    'PostGIS geometry (Polygon or MultiPolygon) in WGS84 (EPSG:4326)';
COMMENT ON COLUMN operations.location_polygons.source IS 
    'Source of the polygon data (e.g., "bhuvan")';

-- Create function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_location_polygons_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to automatically update updated_at
CREATE TRIGGER trigger_update_location_polygons_updated_at
    BEFORE UPDATE ON operations.location_polygons
    FOR EACH ROW
    EXECUTE FUNCTION update_location_polygons_updated_at();

-- Grant permissions (adjust as needed for your database setup)
-- GRANT SELECT, INSERT, UPDATE ON operations.location_polygons TO your_app_user;

