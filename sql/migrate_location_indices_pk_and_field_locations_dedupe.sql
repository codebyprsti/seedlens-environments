-- =============================================================================
-- field_locations dedupe + crop_indices centroids + location_indices composite PK
-- =============================================================================
-- PostgreSQL. Full backup first. Run pre-checks, extend UPDATE list for your FKs.
--
-- Order matters:
--   1) Backfill crop_indices centroids (NULL only).
--   2) Collapse duplicate field_locations centroids; remap location_id everywhere;
--      then delete extra field_locations rows.
--   3) Remove duplicate rows on location_indices natural key (after remap, rows
--      may collide), then add PRIMARY KEY (season_id, location_id, index_date).
--
-- index_date type: PK uses the column as stored. If it is TIMESTAMP and you only
-- want one row per calendar day, add a generated date column and use that in PK
-- instead (see bottom comment).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- PRE-CHECKS
-- -----------------------------------------------------------------------------
-- SELECT season_id, location_id, index_date, COUNT(*) FROM operations.location_indices
--   GROUP BY 1,2,3 HAVING COUNT(*) > 1;
-- SELECT ROUND(latitude::numeric,7), ROUND(longitude::numeric,7), COUNT(*)
--   FROM operations.field_locations GROUP BY 1,2 HAVING COUNT(*) > 1;
-- SELECT conname, conrelid::regclass FROM pg_constraint
--   WHERE confrelid = 'operations.field_locations'::regclass AND contype = 'f';

BEGIN;

-- =============================================================================
-- PART 1 — crop_indices: centroids from masters (NULLs only)
-- =============================================================================
UPDATE operations.crop_indices ci
SET centroid_lat = l.latitude, centroid_lon = l.longitude
FROM operations.locations l
WHERE ci.location_id = l.location_id
  AND (ci.centroid_lat IS NULL OR ci.centroid_lon IS NULL);

UPDATE operations.crop_indices ci
SET centroid_lat = fl.latitude, centroid_lon = fl.longitude
FROM operations.field_locations fl
WHERE ci.location_id = fl.location_id
  AND (ci.centroid_lat IS NULL OR ci.centroid_lon IS NULL);

-- =============================================================================
-- PART 2 — field_locations: remap duplicate centroids → MIN(location_id)
-- =============================================================================
CREATE TEMP TABLE fl_centroid_map ON COMMIT DROP AS
WITH pts AS (
    SELECT
        location_id,
        latitude,
        longitude,
        ROUND(latitude::numeric, 7) AS rlat,
        ROUND(longitude::numeric, 7) AS rlon
    FROM operations.field_locations
),
canon AS (
    SELECT rlat, rlon, MIN(location_id) AS canonical_location_id
    FROM pts
    GROUP BY rlat, rlon
)
SELECT p.location_id AS old_location_id, c.canonical_location_id
FROM pts p
JOIN canon c USING (rlat, rlon)
WHERE p.location_id <> c.canonical_location_id;

UPDATE operations.crop_indices ci
SET location_id = m.canonical_location_id
FROM fl_centroid_map m
WHERE ci.location_id = m.old_location_id;

UPDATE operations.location_indices li
SET location_id = m.canonical_location_id
FROM fl_centroid_map m
WHERE li.location_id = m.old_location_id;

-- Add more UPDATE ... FROM fl_centroid_map for any other child tables.

DELETE FROM operations.field_locations fl
USING fl_centroid_map m
WHERE fl.location_id = m.old_location_id;

-- =============================================================================
-- PART 3 — field_locations: composite PRIMARY KEY
-- =============================================================================
ALTER TABLE operations.field_locations
    DROP CONSTRAINT IF EXISTS field_locations_pkey;

ALTER TABLE operations.field_locations
    ADD CONSTRAINT field_locations_pkey PRIMARY KEY (location_id, latitude, longitude);

-- =============================================================================
-- PART 4 — location_indices: dedupe natural key, then composite PK
-- =============================================================================
-- Align PARTITION BY with your PK: if index_date is DATE, use index_date only;
-- if TIMESTAMP and you need one row per day, use index_date::date in PARTITION BY
-- AND add a generated column + PK on that (see notes below).
DELETE FROM operations.location_indices li
WHERE li.id IN (
    SELECT id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY season_id, location_id, index_date
                ORDER BY id
            ) AS rn
        FROM operations.location_indices
    ) x
    WHERE x.rn > 1
);

ALTER TABLE operations.location_indices
    DROP CONSTRAINT IF EXISTS location_indices_pkey;

ALTER TABLE operations.location_indices
    ADD CONSTRAINT location_indices_pkey PRIMARY KEY (season_id, location_id, index_date);

COMMIT;

-- =============================================================================
-- Notes
-- =============================================================================
-- * "Without removing data" for the PK: PostgreSQL requires unique triples. The
--   only safe approach is to merge duplicate keys (delete extra rows after
--   keeping MIN(id) or a chosen winner). No full table truncate.
-- * If location_indices.id is referenced by FKs, do not DROP that PK; instead
--   keep id as PK and add:
--     CREATE UNIQUE INDEX uq_location_indices_natural
--       ON operations.location_indices (season_id, location_id, index_date);
-- * TIMESTAMP index_date + one row per calendar day:
--     ALTER TABLE operations.location_indices
--       ADD COLUMN index_day date GENERATED ALWAYS AS (index_date::date) STORED;
--     -- dedupe PARTITION BY season_id, location_id, index_day
--     -- PRIMARY KEY (season_id, location_id, index_day)
-- =============================================================================
