-- =============================================================================
-- operations.field_indices — parallel store for lab / 3-file tests
-- =============================================================================
-- Mirrors the columns written by crop_monitoring.database.repository._insert_crop_indices_row
-- so you can INSERT the same payload shape as operations.crop_indices without touching prod rows.
--
-- Apply: psql -f sql/create_field_indices_table.sql
--
-- After the table exists, each successful insert into operations.crop_indices via
-- crop_monitoring.database.repository.insert_crop_indices also inserts a mirror row
-- into operations.field_indices (disable with env FIELD_INDICES_MIRROR=0).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS operations;

CREATE TABLE IF NOT EXISTS operations.field_indices (
    id SERIAL PRIMARY KEY,

    -- Keys (align with insert_validator + batch pipeline)
    location_id   VARCHAR(30),
    season_id     VARCHAR(50),
    file_name     TEXT,
    -- Observation date: use the same column name as crop_indices in your DB (index_date OR analysis_date).
    -- If your crop_indices uses index_date, keep this name and map in application code.
    index_date    DATE,

    grower_id     VARCHAR(20),
    variety_id    VARCHAR(20),
    crop_id       VARCHAR(20),
    crop_name     TEXT,

    polygon_id    VARCHAR(80),
    polygon_area  DOUBLE PRECISION,

    date_start    DATE,
    date_end      DATE,

    area_acre     DOUBLE PRECISION,
    distance_km   DOUBLE PRECISION,

    extracted_grower   TEXT,
    centroid_lat     DOUBLE PRECISION,
    centroid_lon     DOUBLE PRECISION,

    -- Spectral indices
    ndvi   DOUBLE PRECISION,
    savi   DOUBLE PRECISION,
    ndmi   DOUBLE PRECISION,
    ndre   DOUBLE PRECISION,
    gci    DOUBLE PRECISION,
    psri   DOUBLE PRECISION,
    msavi  DOUBLE PRECISION,
    evi    DOUBLE PRECISION,
    lai    DOUBLE PRECISION,
    ndwi   DOUBLE PRECISION,
    ndwi_gao DOUBLE PRECISION,

    -- Bands (Statistical API)
    blue       DOUBLE PRECISION,
    green      DOUBLE PRECISION,
    red        DOUBLE PRECISION,
    rededge1   DOUBLE PRECISION,
    rededge2   DOUBLE PRECISION,
    rededge3   DOUBLE PRECISION,
    nir        DOUBLE PRECISION,
    narrow_nir DOUBLE PRECISION,
    swir1      DOUBLE PRECISION,
    swir2      DOUBLE PRECISION,

    -- SAR + LST (same names as repository insert; add vv/vh aliases in DB if you use short names)
    lst_celsius DOUBLE PRECISION,
    vv_db       DOUBLE PRECISION,
    vh_db       DOUBLE PRECISION,
    vh_vv_ratio DOUBLE PRECISION,

    grower_name   TEXT,
    variety_name  TEXT,

    -- Test / audit
    batch_tag     VARCHAR(80) DEFAULT 'test_3files',
    created_at    TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Optional FK when location_id always exists in field_locations (comment out if your DB uses mixed IDs)
-- ALTER TABLE operations.field_indices
--   ADD CONSTRAINT fk_field_indices_location
--   FOREIGN KEY (location_id) REFERENCES operations.field_locations (location_id);

CREATE INDEX IF NOT EXISTS idx_field_indices_loc_season_date
    ON operations.field_indices (location_id, season_id, index_date);

CREATE INDEX IF NOT EXISTS idx_field_indices_file_season
    ON operations.field_indices (file_name, season_id);

COMMENT ON TABLE operations.field_indices IS
    'Field-level satellite indices (test / staging); same row shape as crop_indices inserts from batch pipeline.';

-- -----------------------------------------------------------------------------
-- Example: copy last N rows from crop_indices into field_indices for comparison
-- (adjust column list if your crop_indices uses analysis_date vs index_date, vv vs vv_db, etc.)
-- -----------------------------------------------------------------------------
/*
INSERT INTO operations.field_indices (
    location_id, season_id, file_name, index_date,
    grower_id, variety_id, crop_id, crop_name,
    polygon_id, polygon_area, date_start, date_end,
    area_acre, distance_km, extracted_grower,
    ndvi, savi, ndmi, ndre, gci, psri, msavi, evi, lai, ndwi, ndwi_gao,
    blue, green, red, rededge1, rededge2, rededge3, nir, narrow_nir, swir1, swir2,
    lst_celsius, vv_db, vh_db, vh_vv_ratio,
    grower_name, variety_name, batch_tag
)
SELECT
    location_id, season_id, file_name,
    COALESCE(index_date::date, analysis_date::date) AS index_date,
    grower_id, variety_id, crop_id, crop_name,
    polygon_id, polygon_area, date_start, date_end,
    area_acre, distance_km, extracted_grower,
    ndvi, savi, ndmi, ndre, gci, psri, msavi, evi, lai, ndwi, ndwi_gao,
    blue, green, red, rededge1, rededge2, rededge3, nir, narrow_nir, swir1, swir2,
    lst_celsius, vv_db, vh_db, vh_vv_ratio,
    grower_name, variety_name,
    'copy_from_crop_indices'
FROM operations.crop_indices
WHERE season_id = 'RABI_25_26'
  AND file_name IN (
      'MOTKAPALL CHELLA RAJU USRH24 WOSRINIVASULU.kml',
      'Pandurungi Sunil Dhal Usrh24 Harihar Dhal.kml',
      'Raipur Nirod Kuamr Khatua Usrh24 Kshetramohan Khatua.kml'
  );
*/
