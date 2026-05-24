-- One-time segregation: copy data FROM operations.crop_indices INTO satellite-specific tables.
-- Does NOT modify, truncate, or delete crop_indices.
-- Prerequisite: sql/create_sentinel_indices_tables.sql
--
-- Observation date column: uses index_date if present, else analysis_date.

CREATE SCHEMA IF NOT EXISTS operations;

-- Helper view: run sql/backfill_view_dynamic.sql first (handles index_date vs analysis_date).
-- Prerequisite view: operations.v_crop_indices_observation

-- ---------------------------------------------------------------------------
-- Sentinel-2 backfill
-- ---------------------------------------------------------------------------
INSERT INTO operations.sentinel2_indices (
    location_id, file_name, season_id, acquisition_date,
    crop_indices_id,
    blue, green, red, rededge1, rededge2, rededge3, nir, narrow_nir, swir1, swir2,
    ndvi, savi, msavi, evi, lai, gci,
    ndre, ndmi, ndwi, ndwi_gao, psri,
    max_cloud_cover_pct,
    index_sources,
    pipeline_version
)
SELECT
    v.location_id,
    v.file_name,
    v.season_id,
    v.observation_date,
    v.id,
    v.blue,
    v.green,
    v.red,
    v.rededge1,
    v.rededge2,
    v.rededge3,
    v.nir,
    v.narrow_nir,
    v.swir1,
    v.swir2,
    v.ndvi,
    v.savi,
    v.msavi,
    v.evi,
    v.lai,
    v.gci,
    v.ndre,
    v.ndmi,
    COALESCE(v.ndwi, NULL),
    v.ndwi_gao,
    v.psri,
    20,
    jsonb_build_object(
        'ndvi', 'migrated_from_crop_indices',
        'bands', 'partial_legacy_columns'
    ),
    'backfill_v1'
FROM operations.v_crop_indices_observation v
WHERE v.observation_date IS NOT NULL
  AND v.location_id IS NOT NULL
  AND v.file_name IS NOT NULL
  AND (
      v.ndvi IS NOT NULL OR v.nir IS NOT NULL OR v.blue IS NOT NULL
      OR v.ndmi IS NOT NULL OR v.evi IS NOT NULL
  )
ON CONFLICT (location_id, file_name, season_id, acquisition_date) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Sentinel-1 backfill (SAR columns)
-- ---------------------------------------------------------------------------
INSERT INTO operations.sentinel1_indices (
    location_id, file_name, season_id, acquisition_date,
    crop_indices_id,
    vv, vh, vv_db, vh_db, vh_vv_ratio,
    index_sources,
    pipeline_version
)
SELECT
    v.location_id,
    v.file_name,
    v.season_id,
    v.observation_date,
    v.id,
    CASE
        WHEN COALESCE(v.vv_db, v.vv) IS NOT NULL AND COALESCE(v.vv_db, v.vv) < 0
            THEN POWER(10.0, COALESCE(v.vv_db, v.vv) / 10.0)
        WHEN v.vv IS NOT NULL AND v.vv > 0 THEN v.vv
        ELSE NULL
    END,
    CASE
        WHEN COALESCE(v.vh_db, v.vh) IS NOT NULL AND COALESCE(v.vh_db, v.vh) < 0
            THEN POWER(10.0, COALESCE(v.vh_db, v.vh) / 10.0)
        WHEN v.vh IS NOT NULL AND v.vh > 0 THEN v.vh
        ELSE NULL
    END,
    COALESCE(v.vv_db, CASE WHEN v.vv IS NOT NULL AND v.vv < 0 THEN v.vv ELSE 10.0 * LOG(10.0, v.vv) END),
    COALESCE(v.vh_db, CASE WHEN v.vh IS NOT NULL AND v.vh < 0 THEN v.vh ELSE 10.0 * LOG(10.0, v.vh) END),
    COALESCE(v.vh_vv_ratio, v.vh_vv),
    jsonb_build_object('source', 'migrated_from_crop_indices'),
    'backfill_v1'
FROM operations.v_crop_indices_observation v
WHERE v.observation_date IS NOT NULL
  AND v.location_id IS NOT NULL
  AND v.file_name IS NOT NULL
  AND (
      COALESCE(v.vv_db, v.vv) IS NOT NULL
      OR COALESCE(v.vh_db, v.vh) IS NOT NULL
  )
ON CONFLICT (location_id, file_name, season_id, acquisition_date) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Sentinel-3 backfill (LST)
-- ---------------------------------------------------------------------------
INSERT INTO operations.sentinel3_indices (
    location_id, file_name, season_id, acquisition_date,
    crop_indices_id,
    lst_celsius,
    index_sources,
    pipeline_version
)
SELECT
    v.location_id,
    v.file_name,
    v.season_id,
    v.observation_date,
    v.id,
    COALESCE(v.lst_celsius, v.lst_c),
    jsonb_build_object('lst_celsius', 'migrated_from_crop_indices'),
    'backfill_v1'
FROM operations.v_crop_indices_observation v
WHERE v.observation_date IS NOT NULL
  AND v.location_id IS NOT NULL
  AND v.file_name IS NOT NULL
  AND COALESCE(v.lst_celsius, v.lst_c) IS NOT NULL
ON CONFLICT (location_id, file_name, season_id, acquisition_date) DO NOTHING;

-- Summary counts
SELECT 'sentinel2_indices' AS tbl, COUNT(*) AS rows FROM operations.sentinel2_indices
UNION ALL
SELECT 'sentinel1_indices', COUNT(*) FROM operations.sentinel1_indices
UNION ALL
SELECT 'sentinel3_indices', COUNT(*) FROM operations.sentinel3_indices;
