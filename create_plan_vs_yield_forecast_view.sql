-- Create unified analytics view: operations.plan_vs_yield_forecast_view
-- Joins: season_crop_yield (actuals), supply_chain_planning (plan), seed_forecast (forecast), master tables.
-- Run this script in your DB (e.g. psql or pgAdmin) so GET /api/v1/yield/summary and /yield/inspection work.

DROP VIEW IF EXISTS operations.plan_vs_yield_forecast_view;

CREATE VIEW operations.plan_vs_yield_forecast_view AS
SELECT
    y.season_id,
    y.crop_id,
    y.variety_id,
    y.location_id,
    s.season_name,
    c.crop_name,
    v.variety_name,
    b.village,
    b.taluka_mandal AS mandal,
    b.district,
    b.state,
    y.lot_id,
    -- Plan (from supply_chain_planning)
    scp.net_acres_current::double precision AS planned_net_acres,
    scp.production_allocation::double precision AS planned_production_allocation,
    scp.productivity::double precision AS planned_productivity,
    -- Actuals (yield first, then planning actuals as fallback)
    COALESCE(y.net_acreage_area::double precision, scp.actual_net_acres::double precision) AS actual_net_acres,
    COALESCE(y.productivity_of_packed_seed::double precision, scp.actual_productivity::double precision) AS actual_productivity,
    COALESCE(y.sum_of_received_qty::double precision, scp.actual_received_qty::double precision) AS actual_received_qty,
    COALESCE(y.packed_qty::double precision, scp.actual_packaged_qty::double precision) AS actual_packed_qty,
    COALESCE(y.amount_inr::double precision, scp.actual_amount::double precision) AS actual_amount,
    -- Forecast
    sf.stage_forecast1::double precision AS stage_forecast1,
    sf.stage_forecast2::double precision AS stage_forecast2,
    sf.stage_forecast3::double precision AS stage_forecast3,
    sf.stage_forecast4::double precision AS stage_forecast4,
    sf.stage_forecast5::double precision AS stage_forecast5,
    sf.stage_forecast6::double precision AS stage_forecast6
FROM operations.season_crop_yield y
LEFT JOIN operations.season_crop_inspection_base b
    ON b.season_id = y.season_id AND b.crop_id = y.crop_id AND b.variety_id = y.variety_id AND b.lot_id = y.lot_id
LEFT JOIN operations.seasons s ON s.season_id = y.season_id
LEFT JOIN operations.crops c ON c.crop_id = y.crop_id
LEFT JOIN operations.varieties v ON v.variety_id = y.variety_id
LEFT JOIN operations.supply_chain_planning scp
    ON LOWER(TRIM(scp.season)) = LOWER(TRIM(s.season_name))
   AND LOWER(TRIM(scp.crop)) = LOWER(TRIM(c.crop_name))
   AND LOWER(TRIM(scp.variety)) = LOWER(TRIM(v.variety_name))
   AND LOWER(TRIM(COALESCE(scp.village, ''))) = LOWER(TRIM(COALESCE(b.village, '')))
LEFT JOIN operations.seed_forecast sf
    ON sf.season_id = y.season_id AND sf.crop_id = y.crop_id AND sf.variety_id = y.variety_id
   AND sf.lot_id = y.lot_id AND sf.grower_id = y.grower_id;

-- Optional: grant to app user
-- GRANT SELECT ON operations.plan_vs_yield_forecast_view TO your_app_user;
