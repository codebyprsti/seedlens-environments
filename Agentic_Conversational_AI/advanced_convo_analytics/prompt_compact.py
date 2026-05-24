"""
Compact prompt builder with dynamic schema/table support.
Uses schema_name.table_name from agentic config (not hardcoded).
"""
import logging

logger = logging.getLogger(__name__)


def build_schema_compact(schema_name: str, table_name: str, columns: list[str] = None) -> str:
    """Build minimal schema description: schema.table + column list."""
    table_ref = f"{schema_name}.{table_name}"
    if columns:
        cols_str = ", ".join(columns)
        return f"Table: {table_ref}\nColumns: {cols_str}."
    return f"Table: {table_ref}"


def build_rules_compact(schema_name: str, table_name: str) -> str:
    """Build compressed rules with dynamic table reference."""
    table_ref = f"{schema_name}.{table_name}"
    return f"""Rules: Use only the columns listed. Query only {table_ref}. Season: ILIKE '%RABI%' and/or ILIKE '%24-25%' (use '%21-22%' for 2021-22 etc). SUM/AVG/COUNT; ROW_NUMBER/LAG for ranking or period comparison. ORDER BY every SELECT; top/highest=DESC LIMIT n, bottom/lowest=ASC LIMIT n. Division: use NULLIF(denom,0); ROUND for percentages. No JOINs. Revenue: rate_per_kg*quantity or amount. Per-acre: metric/NULLIF(net_acres,0) or NULLIF(net_tp_acres,0). Acreage efficiency: final_harvestable_area/NULLIF(sowing_acres,0). State analysis: group by state, include state in SELECT. Top growers: return only grower and requested metric; use AVG(rate_per_kg) for rates. Chart: horizontal bar for Top N."""


def build_few_shot_compact(schema_name: str, table_name: str) -> str:
    """Build few-shot examples with dynamic table reference."""
    table_ref = f"{schema_name}.{table_name}"
    return f"""Translate to PostgreSQL SQL. Return only the SQL, no explanation.

1. Top 5 varieties by productivity
SELECT variety, SUM(productivity) AS total_productivity FROM {table_ref} GROUP BY variety ORDER BY total_productivity DESC LIMIT 5;

2. Bottom 10 villages by productivity
SELECT village, SUM(productivity) AS total_productivity FROM {table_ref} GROUP BY village ORDER BY total_productivity ASC LIMIT 10;

3. Varieties by net acres for RABI 2024-25
SELECT variety, SUM(net_acres) AS total_net_acres FROM {table_ref} WHERE season ILIKE '%RABI%' AND season ILIKE '%24-25%' GROUP BY variety ORDER BY total_net_acres DESC;

4. Average rate per kg by variety, RABI 24-25
SELECT variety, AVG(rate_per_kg) AS avg_rate_per_kg, SUM(quantity) AS total_quantity FROM {table_ref} WHERE season ILIKE '%RABI%' AND season ILIKE '%24-25%' GROUP BY variety ORDER BY avg_rate_per_kg DESC;

5. Top 10 states by productivity, RABI 24-25
SELECT state, SUM(productivity) AS total_productivity FROM {table_ref} WHERE season ILIKE '%RABI%' AND season ILIKE '%24-25%' GROUP BY state ORDER BY total_productivity DESC LIMIT 10;

6. Average productivity by variety and state, RABI 24-25
SELECT state, variety, AVG(productivity) AS avg_productivity FROM {table_ref} WHERE season ILIKE '%RABI%' AND season ILIKE '%24-25%' GROUP BY state, variety ORDER BY state, avg_productivity DESC;

7. Top 10 villages per variety by productivity, RABI 24-25
WITH ranked AS (SELECT variety, village, SUM(productivity) AS total_productivity, ROW_NUMBER() OVER (PARTITION BY variety ORDER BY SUM(productivity) DESC) AS rn FROM {table_ref} WHERE season ILIKE '%RABI%' AND season ILIKE '%24-25%' GROUP BY variety, village) SELECT variety, village, total_productivity FROM ranked WHERE rn <= 10 ORDER BY variety, total_productivity DESC;

8. Variety-wise top 10 growers by received quantity
SELECT variety, grower, total_received_qty FROM (SELECT variety, grower, SUM(actual_received_qty) AS total_received_qty, ROW_NUMBER() OVER (PARTITION BY variety ORDER BY SUM(actual_received_qty) DESC) AS rn FROM {table_ref} GROUP BY variety, grower) ranked WHERE rn <= 10 ORDER BY variety, total_received_qty DESC;

9. Top 10 varieties by revenue (rate*quantity), RABI
SELECT variety, SUM(rate_per_kg * quantity) AS total_revenue FROM {table_ref} WHERE season ILIKE '%RABI%' GROUP BY variety ORDER BY total_revenue DESC LIMIT 10;

10. State-wise acreage, RABI 24-25
SELECT state, SUM(sowing_acres) AS total_sowing_acres, SUM(net_acres) AS total_net_acres FROM {table_ref} WHERE season ILIKE '%RABI%' AND season ILIKE '%24-25%' GROUP BY state ORDER BY total_net_acres DESC;

11. Productivity per net acre by variety
SELECT variety, SUM(productivity) AS total_productivity, SUM(net_acres) AS total_net_acres, ROUND((SUM(productivity)/NULLIF(SUM(net_acres),0))::NUMERIC,2) AS productivity_per_acre FROM {table_ref} WHERE season ILIKE '%RABI%' GROUP BY variety ORDER BY productivity_per_acre DESC;

12. Acreage efficiency (harvestable/sowing) by variety and state, RABI 24-25
SELECT variety, state, SUM(sowing_acres) AS total_sowing_acres, SUM(final_harvestable_area) AS total_final_harvestable_area, ROUND((SUM(final_harvestable_area)/NULLIF(SUM(sowing_acres),0))::NUMERIC*100,2) AS harvest_efficiency_pct FROM {table_ref} WHERE season ILIKE '%RABI%' AND season ILIKE '%24-25%' GROUP BY variety, state ORDER BY harvest_efficiency_pct DESC;

13. Varieties for RABI 2021-22
SELECT variety, SUM(productivity) AS total_productivity FROM {table_ref} WHERE season ILIKE '%RABI%' AND season ILIKE '%21-22%' GROUP BY variety ORDER BY total_productivity DESC;"""


def build_compact_user_prompt(
    user_question: str,
    schema_name: str = None,
    table_name: str = None,
    columns: list[str] = None,
    extra_rule: str = ""
) -> str:
    """
    Build minimal-token user message with dynamic schema/table.
    
    Args:
        user_question: User's natural language query
        schema_name: Database schema name (e.g., 'operations'). Defaults to 'operations' if not provided.
        table_name: Table/view name (e.g., 'season_yield_analysis_vw'). Required for dynamic mode.
        columns: Optional list of column names (if None, uses generic description)
        extra_rule: Optional additional rule to append
    
    Note: If schema_name/table_name not provided, uses default 'operations.season_yield_analysis_vw'
          for backward compatibility with Lambda deployments.
    """
    # Backward compatibility: use defaults if not provided
    if not schema_name:
        schema_name = "operations"
    if not table_name:
        table_name = "season_yield_analysis_vw"
        logger.warning("build_compact_user_prompt: Using default table 'operations.season_yield_analysis_vw'. Provide schema_name/table_name for dynamic behavior.")
    
    logger.debug("Building compact prompt for %s.%s", schema_name, table_name)
    schema_text = build_schema_compact(schema_name, table_name, columns)
    rules_text = build_rules_compact(schema_name, table_name)
    few_shot_text = build_few_shot_compact(schema_name, table_name)
    
    parts = [schema_text, rules_text, few_shot_text]
    if extra_rule:
        parts.append(extra_rule.strip())
    parts.append(f"User: {user_question}")
    return "\n".join(parts)
