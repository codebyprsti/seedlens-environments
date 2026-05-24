-- One observation per KML file per calendar day (not per location alone).
-- The old index uq_crop_indices_season_location_date_area omitted file_name, so only one row
-- per (season, location, index_date, polygon_area) could exist across all KMLs sharing that key.
--
-- Run during a maintenance window. If CREATE UNIQUE INDEX fails, resolve duplicate rows first.

DROP INDEX IF EXISTS operations.uq_crop_indices_season_location_date_area;

CREATE UNIQUE INDEX IF NOT EXISTS uq_crop_indices_season_loc_file_index_variety
  ON operations.crop_indices (season_id, location_id, file_name, index_date, variety_id);
