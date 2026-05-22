-- Field locations table: one row per (village, district, state, lat, lon).
-- location_id format: IND-<STATE_CODE>-<NUMBER> (e.g. IND-KA-600001).
-- Run once.

CREATE TABLE IF NOT EXISTS operations.field_locations (
    location_id   VARCHAR(30) PRIMARY KEY,
    village       VARCHAR(200) NOT NULL,
    district      VARCHAR(200),
    state         VARCHAR(200),
    state_code    VARCHAR(20),
    mandal        VARCHAR(200),
    postalcode    VARCHAR(50),
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_field_locations_lookup
    ON operations.field_locations (village, district, state, latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_field_locations_state_code
    ON operations.field_locations (state_code);

COMMENT ON TABLE operations.field_locations IS 'Field locations from Google reverse geocode; centroid lat/lon from polygon';
