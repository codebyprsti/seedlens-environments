-- Dynamic observation-date view for crop_indices backfill (index_date when present).

CREATE SCHEMA IF NOT EXISTS operations;

DO $$
DECLARE
    has_index_date boolean;
    view_sql text;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'operations'
          AND table_name = 'crop_indices'
          AND column_name = 'index_date'
    ) INTO has_index_date;

    IF has_index_date THEN
        view_sql := $v$
            CREATE OR REPLACE VIEW operations.v_crop_indices_observation AS
            SELECT c.*,
                   COALESCE(c.index_date, c.analysis_date, c.date_start)::date AS observation_date
            FROM operations.crop_indices c
        $v$;
    ELSE
        view_sql := $v$
            CREATE OR REPLACE VIEW operations.v_crop_indices_observation AS
            SELECT c.*,
                   COALESCE(c.analysis_date, c.date_start)::date AS observation_date
            FROM operations.crop_indices c
        $v$;
    END IF;

    EXECUTE view_sql;
END $$;
