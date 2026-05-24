-- Align uniqueness with one row per (field location, season, KML file, observation day, variety).
-- Replaces uq_crop_indices_location_start_variety_file, which used date_start; Sentinel Statistical
-- interval.from can repeat across buckets while analysis_date differs, so only one row could persist.
--
-- Prerequisites: columns location_id, season_id, file_name, analysis_date, variety_id on operations.crop_indices.
-- If your table uses index_date instead of analysis_date, change the CREATE to use index_date or
-- COALESCE(analysis_date, index_date) (both columns must exist).

DROP INDEX IF EXISTS operations.uq_crop_indices_location_start_variety_file;

CREATE UNIQUE INDEX IF NOT EXISTS uq_crop_indices_loc_season_file_analysis_variety
  ON operations.crop_indices (location_id, season_id, file_name, analysis_date, variety_id);
