## Copernicus metadata structure (reference)

This repo consumes two Sentinel Hub API shapes:

### 1) Statistical API (zonal stats)

- Response contains `data[]` entries, each with:
  - `interval.from`, `interval.to`
  - `outputs.<output_id>.<band_key>.stats.{mean,min,max,stDev,noDataCount,sampleCount}`

In this codebase:
- `crop_monitoring/statistical_client.py` defines the `STAT_INDEX_NAMES` order for `B0..B8`.
- `scripts/run_crop_analysis_s3_batch.py` defines the `S2_BAND_IDS` order for `B0..B9`.

### 2) Process API (rasters)

- Returns arrays (H,W,C) or (H,W) for an AOI and time interval.
- Stored/handled as in-memory arrays in pipeline code; summary stats are often computed for logging.

### Safety

Tests should only validate **mappings and parsing**. They must not call network APIs or write to DB by default.

