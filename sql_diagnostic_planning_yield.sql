-- SQL diagnostic queries: planning vs yield key mismatches
-- Run these in your DB client to find keys that exist in one table but not the other.
-- Keys are (season, crop, variety, village) normalized as LOWER(TRIM(...)).

-- 1) Planning keys NOT in yield (distinct planning combinations with no matching yield)
-- Use normalized names: LOWER(TRIM(season)), etc.
SELECT DISTINCT
    LOWER(TRIM(scp.season))   AS season_norm,
    LOWER(TRIM(scp.crop))     AS crop_norm,
    LOWER(TRIM(scp.variety))  AS variety_norm,
    LOWER(TRIM(COALESCE(scp.village, ''))) AS village_norm
FROM operations.supply_chain_planning scp
EXCEPT
SELECT DISTINCT
    LOWER(TRIM(y.season_name)),
    LOWER(TRIM(y.crop_name)),
    LOWER(TRIM(y.variety_name)),
    LOWER(TRIM(COALESCE(y.village, '')))
FROM operations.yield_inspection_view y;

-- 2) Yield keys NOT in planning (distinct yield combinations with no matching planning)
SELECT DISTINCT
    LOWER(TRIM(y.season_name))   AS season_norm,
    LOWER(TRIM(y.crop_name))     AS crop_norm,
    LOWER(TRIM(y.variety_name))  AS variety_norm,
    LOWER(TRIM(COALESCE(y.village, ''))) AS village_norm
FROM operations.yield_inspection_view y
EXCEPT
SELECT DISTINCT
    LOWER(TRIM(scp.season)),
    LOWER(TRIM(scp.crop)),
    LOWER(TRIM(scp.variety)),
    LOWER(TRIM(COALESCE(scp.village, '')))
FROM operations.supply_chain_planning scp;

-- 3) Total row count in planning (verification)
SELECT COUNT(*) AS total_planning_rows FROM operations.supply_chain_planning;

-- 4) Distinct (season, crop, variety, village) in planning
SELECT COUNT(*) AS distinct_planning_keys
FROM (
    SELECT DISTINCT season, crop, variety, village
    FROM operations.supply_chain_planning
) t;
