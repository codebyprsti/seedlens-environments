-- Align sentinel{1,2,3}_indices band/SAR column names with operations.crop_indices.
-- Run once on lab/prod after backup.
-- Idempotent: only renames when source exists and target absent.

CREATE SCHEMA IF NOT EXISTS operations;

-- ---------------------------------------------------------------------------
-- Sentinel-2: b02 → blue, b03 → green, … (see migrate_crop_indices_field_locations_alignment.sql)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    pairs TEXT[][] := ARRAY[
        ARRAY['b02', 'blue'],
        ARRAY['b03', 'green'],
        ARRAY['b04', 'red'],
        ARRAY['b05', 'rededge1'],
        ARRAY['b06', 'rededge2'],
        ARRAY['b07', 'rededge3'],
        ARRAY['b08', 'nir'],
        ARRAY['b8a', 'narrow_nir'],
        ARRAY['b11', 'swir1'],
        ARRAY['b12', 'swir2'],
        ARRAY['b01', 'coastal'],
        ARRAY['b09', 'cirrus']
    ];
    p TEXT[];
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'operations' AND table_name = 'sentinel2_indices'
    ) THEN
        RETURN;
    END IF;
    FOREACH p SLICE 1 IN ARRAY pairs LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'operations' AND table_name = 'sentinel2_indices'
              AND column_name = p[1]
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'operations' AND table_name = 'sentinel2_indices'
              AND column_name = p[2]
        ) THEN
            EXECUTE format(
                'ALTER TABLE operations.sentinel2_indices RENAME COLUMN %I TO %I',
                p[1], p[2]
            );
        END IF;
    END LOOP;
END $$;

COMMENT ON COLUMN operations.sentinel2_indices.blue IS 'Mean S2 B02 (crop_indices name)';
COMMENT ON COLUMN operations.sentinel2_indices.green IS 'Mean S2 B03';
COMMENT ON COLUMN operations.sentinel2_indices.nir IS 'Mean S2 B08';

-- ---------------------------------------------------------------------------
-- Sentinel-1: ensure vv/vh = linear backscatter; vv_db/vh_db = dB (crop_indices names)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'operations' AND table_name = 'sentinel1_indices'
    ) THEN
        RETURN;
    END IF;

    -- vv/vh columns mistaken for dB → convert to linear; preserve dB in vv_db/vh_db
    UPDATE operations.sentinel1_indices SET
        vv_db = COALESCE(vv_db, vv),
        vv = CASE
            WHEN vv IS NOT NULL AND vv < 0 THEN POWER(10.0, vv / 10.0)
            WHEN vv IS NOT NULL AND vv > 0 AND vv <= 1.0 THEN vv
            WHEN vv_db IS NOT NULL AND (vv IS NULL OR vv < 0) THEN POWER(10.0, vv_db / 10.0)
            ELSE vv
        END
    WHERE vv IS NOT NULL OR vv_db IS NOT NULL;

    UPDATE operations.sentinel1_indices SET
        vh_db = COALESCE(vh_db, vh),
        vh = CASE
            WHEN vh IS NOT NULL AND vh < 0 THEN POWER(10.0, vh / 10.0)
            WHEN vh IS NOT NULL AND vh > 0 AND vh <= 1.0 THEN vh
            WHEN vh_db IS NOT NULL AND (vh IS NULL OR vh < 0) THEN POWER(10.0, vh_db / 10.0)
            ELSE vh
        END
    WHERE vh IS NOT NULL OR vh_db IS NOT NULL;

    -- Recompute dB from linear where missing
    UPDATE operations.sentinel1_indices SET
        vv_db = 10.0 * LOG(10.0, vv)
    WHERE vv IS NOT NULL AND vv > 0 AND vv_db IS NULL;

    UPDATE operations.sentinel1_indices SET
        vh_db = 10.0 * LOG(10.0, vh)
    WHERE vh IS NOT NULL AND vh > 0 AND vh_db IS NULL;

    UPDATE operations.sentinel1_indices SET
        vh_vv_ratio = vh / vv
    WHERE vv IS NOT NULL AND vh IS NOT NULL AND vv > 0 AND vh_vv_ratio IS NULL;

END $$;

COMMENT ON COLUMN operations.sentinel1_indices.vv IS 'VV linear backscatter (sigma0 power)';
COMMENT ON COLUMN operations.sentinel1_indices.vh IS 'VH linear backscatter (sigma0 power)';
COMMENT ON COLUMN operations.sentinel1_indices.vv_db IS 'VV dB — same semantics as crop_indices.vv_db';
COMMENT ON COLUMN operations.sentinel1_indices.vh_db IS 'VH dB — same semantics as crop_indices.vh_db';

-- ---------------------------------------------------------------------------
-- Sentinel-3: lst_celsius already matches crop_indices; optional band aliases
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'operations' AND table_name = 'sentinel3_indices'
    ) THEN
        RETURN;
    END IF;
    -- crop_indices has lst_celsius only (no S7/S8/S9 names); keep s7/s8/s9 as-is
    COMMENT ON COLUMN operations.sentinel3_indices.lst_celsius IS 'LST °C — same as crop_indices.lst_celsius';
END $$;
