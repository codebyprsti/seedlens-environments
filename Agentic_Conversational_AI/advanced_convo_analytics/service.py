"""
In-process service for advanced analytics. Reuses lambda logic with optional config overrides.
Called by the main app orchestrator with agentic config (no env/duplicate logic).
"""
import json
import logging
import os
import sys

# Ensure package is on path when run from main project
_pkg_dir = __file__.rsplit(os.path.sep, 1)[0]
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from database import DatabaseManager
from llm_client import LLMClient
from utils import Utils

logger = logging.getLogger(__name__)

try:
    from prompt_compact import build_compact_user_prompt
    _COMPACT_AVAILABLE = True
except ImportError:
    _COMPACT_AVAILABLE = False

USE_COMPACT_PROMPT = os.environ.get("USE_COMPACT_PROMPT", "true").lower() in ("true", "1", "yes")


def run_advanced_analytics(
    user_query: str,
    db_config: dict,
    schema_name: str = "operations",
    table_name: str = None,
    few_shot_prompt: str = "",
    rules: str = "",
    schema_text: str = "",
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
) -> dict:
    """
    Run the same flow as lambda_handler with provided config (no env dependency).
    Returns dict with statusCode, body (json_output, sql_query) or error.
    """
    if not user_query or not user_query.strip():
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "No query provided"}),
        }
    if not db_config:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Database config required"}),
        }

    # LLM config: use override or fall back to env / advanced_convo_analytics config
    try:
        from config import LLM_CONFIG
        base_url = llm_base_url or LLM_CONFIG.get("base_url", "")
        api_key = llm_api_key if llm_api_key is not None else LLM_CONFIG.get("api_key", "")
        model = llm_model or LLM_CONFIG.get("model", "Meta-Llama-3.3-70B-Instruct")
    except ImportError:
        base_url = llm_base_url
        api_key = llm_api_key or ""
        model = llm_model or "Meta-Llama-3.3-70B-Instruct"

    if not base_url:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "LLM API URL not configured"}),
        }

    db_manager = None
    try:
        db_manager = DatabaseManager(db_config_override=db_config, schema_name_override=schema_name)
        client = LLMClient.from_config(base_url=base_url, api_key=api_key, model=model)

        # Get table structure dynamically if table_name provided
        columns = None
        if table_name:
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                conn = psycopg2.connect(
                    host=db_config.get("host", "localhost"),
                    port=int(db_config.get("port", 5432)),
                    dbname=db_config.get("database") or db_config.get("dbname"),
                    user=db_config.get("user"),
                    password=db_config.get("password"),
                )
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = %s
                        ORDER BY ordinal_position;
                    """, (schema_name, table_name))
                    columns = [row[0] for row in cur.fetchall()]
                conn.close()
                logger.info("Fetched %d columns for %s.%s", len(columns) if columns else 0, schema_name, table_name)
            except Exception as e:
                logger.warning("Could not fetch table structure for %s.%s: %s", schema_name, table_name, e)

        # Build prompt with dynamic schema/table
        if _COMPACT_AVAILABLE and USE_COMPACT_PROMPT and table_name:
            logger.debug("Using compact prompt for %s.%s", schema_name, table_name)
            final_prompt = build_compact_user_prompt(
                user_query.strip(),
                schema_name=schema_name,
                table_name=table_name,
                columns=columns,
                extra_rule="Return only one SQL query; no comments in SQL."
            )
        elif schema_text:
            rule = rules or "return only the last query that is relevant to the prompt."
            final_prompt = f"{schema_text}\n{few_shot_prompt}\n{rules}\nUser: {user_query}\n "
        else:
            # Fallback: use config if available, otherwise generic
            try:
                from config import schema, few_shot_prompt as _fs
                rule = rules or "return only the last query that is relevant to the prompt."
                final_prompt = f"Schema: {schema}\n{_fs}\n{rule}\nUser: {user_query}\n "
            except ImportError:
                if table_name:
                    # Last resort: build minimal prompt with schema.table
                    table_ref = f"{schema_name}.{table_name}"
                    final_prompt = f"Table: {table_ref}\nGenerate SQL query for: {user_query}\n"
                else:
                    return {
                        "statusCode": 500,
                        "body": json.dumps({"error": "Schema/prompt not available and table_name not provided"}),
                    }

        logger.info("Sending prompt to LLM for %s.%s", schema_name, table_name or "unknown")
        response = client.prompt_to_sql(final_prompt)
        message_content = response.choices[0].message.content
        sql_queries = client.get_ai_query(message_content)

        if not sql_queries:
            logger.error("Failed to generate SQL for %s.%s", schema_name, table_name or "unknown")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to generate SQL query"}),
            }

        logger.info("Generated SQL for %s.%s: %s", schema_name, table_name or "unknown", sql_queries[0][:200] if sql_queries else "N/A")
        columns, rows, executed_sql = db_manager.get_results(sql_queries)
        if columns is None or rows is None:
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to execute query"}),
            }

        json_output = Utils.build_json(columns, rows)
        return {
            "statusCode": 200,
            "body": json.dumps({"json_output": json_output, "sql_query": executed_sql}, indent=4),
        }
    except Exception as e:
        logger.exception("Advanced analytics error: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
    finally:
        if db_manager:
            try:
                db_manager.close_connection()
            except Exception:
                pass
