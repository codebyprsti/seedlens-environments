-- Migration: Allow multiple polygons per location_id
-- 1. Remove UNIQUE(location_id)
-- 2. Add polygon_index INT
-- 3. Add polygon_geojson TEXT
-- 4. Replace primary key with (location_id, polygon_index)

-- Add new columns (nullable first for existing rows)
ALTER TABLE operations.location_polygons
    ADD COLUMN IF NOT EXISTS polygon_index INT,
    ADD COLUMN IF NOT EXISTS polygon_geojson TEXT;

-- Backfill: set polygon_index = 0 for existing single-polygon rows
UPDATE operations.location_polygons
SET polygon_index = 0
WHERE polygon_index IS NULL;

-- Make polygon_index NOT NULL
ALTER TABLE operations.location_polygons
    ALTER COLUMN polygon_index SET NOT NULL;

-- Default for future inserts (safety)
ALTER TABLE operations.location_polygons
    ALTER COLUMN polygon_index SET DEFAULT 0;

-- Drop old constraints (names may vary; run one at a time if needed)
ALTER TABLE operations.location_polygons
    DROP CONSTRAINT IF EXISTS unique_location_polygon;

ALTER TABLE operations.location_polygons
    DROP CONSTRAINT IF EXISTS location_polygons_pkey;

-- Add composite primary key
ALTER TABLE operations.location_polygons
    ADD PRIMARY KEY (location_id, polygon_index);

-- Index for listing polygons by location
CREATE INDEX IF NOT EXISTS idx_location_polygons_location_id_index
    ON operations.location_polygons(location_id, polygon_index);

COMMENT ON COLUMN operations.location_polygons.polygon_index IS
    'Zero-based index of polygon for this location (MultiPolygon/FeatureCollection split)';
COMMENT ON COLUMN operations.location_polygons.polygon_geojson IS
    'GeoJSON text of the polygon for display/export';
