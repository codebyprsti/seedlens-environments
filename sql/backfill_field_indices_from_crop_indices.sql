-- One-time backfill: operations.crop_indices -> operations.field_indices
-- Adjust column names to match your crop_indices (index_date vs analysis_date, etc.).
-- Run after: sql/create_field_indices_table.sql

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
    ci.location_id,
    ci.season_id,
    ci.file_name,
    COALESCE(
        CASE WHEN ci.index_date IS NOT NULL THEN ci.index_date::date END,
        ci.analysis_date::date,
        ci.date_start
    ) AS index_date,
    ci.grower_id,
    ci.variety_id,
    ci.crop_id,
    ci.crop_name,
    ci.polygon_id,
    ci.polygon_area,
    ci.date_start,
    ci.date_end,
    ci.area_acre,
    ci.distance_km,
    ci.extracted_grower,
    ci.ndvi, ci.savi, ci.ndmi, ci.ndre, ci.gci, ci.psri, ci.msavi, ci.evi, ci.lai,
    ci.ndwi, ci.ndwi_gao,
    ci.blue, ci.green, ci.red, ci.rededge1, ci.rededge2, ci.rededge3,
    ci.nir, ci.narrow_nir, ci.swir1, ci.swir2,
    ci.lst_celsius,
    ci.vv_db,
    ci.vh_db,
    ci.vh_vv_ratio,
    ci.grower_name,
    ci.variety_name,
    'backfill_from_crop_indices'
FROM operations.crop_indices ci
WHERE NOT EXISTS (
    SELECT 1
    FROM operations.field_indices fi
    WHERE fi.location_id IS NOT DISTINCT FROM ci.location_id
      AND fi.season_id IS NOT DISTINCT FROM ci.season_id
      AND fi.file_name IS NOT DISTINCT FROM ci.file_name
      AND fi.index_date IS NOT DISTINCT FROM COALESCE(
          CASE WHEN ci.index_date IS NOT NULL THEN ci.index_date::date END,
          ci.analysis_date::date,
          ci.date_start
      )
);
