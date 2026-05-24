import json
import logging
import re
from database import DatabaseManager
from llm_client import LLMClient
from utils import Utils

# Setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def replace_hardcoded_tables(text: str, schema_name: str, table_name: str) -> str:
    """
    Replace hardcoded table references (e.g., operations.season_yield_analysis_vw) 
    with dynamic schema.table in prompts.
    
    Args:
        text: Prompt text that may contain hardcoded table references
        schema_name: Runtime schema name (e.g., 'operations_demo')
        table_name: Runtime table name (e.g., 'season_crop_yield')
        
    Returns:
        Text with hardcoded tables replaced by {schema_name}.{table_name}
    """
    if not text or not schema_name or not table_name:
        return text
    
    dynamic_table_ref = f"{schema_name}.{table_name}"
    
    # Common hardcoded patterns to replace
    patterns = [
        r'operations\.season_yield_analysis_vw',  # Exact match
        r'operations\.season_yield_analysis_vw\b',  # Word boundary
        r'season_yield_analysis_vw\b',  # Just table name (if schema context is clear)
        r'FROM\s+operations\.season_yield_analysis_vw',  # FROM clause
        r'JOIN\s+operations\.season_yield_analysis_vw',  # JOIN clause
    ]
    
    result = text
    for pattern in patterns:
        # Replace with dynamic reference, preserving case
        result = re.sub(pattern, dynamic_table_ref, result, flags=re.IGNORECASE)
    
    # Also replace any generic "operations.table" patterns if schema_name is different
    if schema_name != "operations":
        result = re.sub(
            r'\boperations\.(\w+)',
            f'{schema_name}.\\1',
            result,
            flags=re.IGNORECASE
        )
    
    return result


def build_dynamic_prompt(
    user_query: str,
    schema_name: str,
    table_name: str,
    few_shot_prompt: str = "",
    schema_structure: str = "",
    rules: str = "",
    customer_name: str = None
) -> str:
    """
    Build dynamic prompt with runtime schema/table, replacing any hardcoded references.
    
    Args:
        user_query: User's natural language query
        schema_name: Runtime schema name
        table_name: Runtime table name
        few_shot_prompt: Few-shot examples (may contain hardcoded tables)
        schema_structure: Schema structure text (may contain hardcoded tables)
        rules: Rules text (may contain hardcoded tables)
        customer_name: Customer/domain name for logging
        
    Returns:
        Complete prompt with dynamic schema.table references
    """
    # Replace hardcoded tables in all prompt components
    few_shot_clean = replace_hardcoded_tables(few_shot_prompt, schema_name, table_name)
    schema_clean = replace_hardcoded_tables(schema_structure, schema_name, table_name)
    rules_clean = replace_hardcoded_tables(rules, schema_name, table_name)
    
    # Build dynamic schema header
    dynamic_schema_header = f"Schema: {schema_name}\nTable: {schema_name}.{table_name}"
    
    # Guardrail instruction to prevent hardcoded tables
    guardrail = f"""
CRITICAL: Always use the runtime schema and table: {schema_name}.{table_name}
DO NOT use any hardcoded table names like 'operations.season_yield_analysis_vw' or 'season_yield_analysis_vw'.
All SQL queries MUST reference: {schema_name}.{table_name}
"""
    
    # Build final prompt
    prompt_parts = [
        dynamic_schema_header,
        guardrail,
    ]
    
    if schema_clean:
        prompt_parts.append(f"\nTable Structure:\n{schema_clean}")
    
    if few_shot_clean:
        prompt_parts.append(f"\n{few_shot_clean}")
    
    if rules_clean:
        prompt_parts.append(f"\nRules: {rules_clean}")
    
    prompt_parts.append(f"\nUser: {user_query}")
    
    final_prompt = "\n".join(prompt_parts)
    
    logger.debug(f"Built dynamic prompt for {schema_name}.{table_name} (customer={customer_name})")
    return final_prompt

def lambda_handler(event, context):
    """
    AWS Lambda function entry point with dynamic schema/table support.
    
    Accepts schema_name and table_name from agentic APIs to override DB config.
    Replaces hardcoded table references in prompts with runtime schema.table.
    """
    cors_header = {
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
    }

    logger.info("Lambda handler invoked.")
    logger.debug(f"Event received: {json.dumps(event)}")

    try:
        # Extract query from event
        if "body" in event and event["body"]:  # For API Gateway
            parsed_data = json.loads(event["body"])
            logger.info("Parsed data from event body.")
        else:  # For local testing
            parsed_data = event
            logger.info("Parsed data from direct event.")

        # Extract runtime schema/table from agentic API request (OVERRIDES DB config)
        schema_name = parsed_data.get('schema_name')
        table_name = parsed_data.get('table_name')
        
        # Extract customer/domain info for loading prompts from DB
        customer_name = parsed_data.get('customer_name')
        domain_name = parsed_data.get('domain_name') or customer_name
        
        # Extract user query
        question = parsed_data.get(
            "user_query",
            "get percentage revenue of top 10 products compared to total revenue based on net_sales. calculate for last 1 year. include total revenue of all products in last 1 year"
        )

        if not question:
            logger.warning("No query provided in request.")
            return {
                'statusCode': 400,
                'body': json.dumps({"error": "No query provided"}),
                'headers': cors_header
            }

        logger.info(f"User query: {question}")
        logger.info(f"Lambda config: customer_name={customer_name}, domain_name={domain_name}")
        logger.info(f"Lambda runtime schema/table: schema_name={schema_name}, table_name={table_name}")

        # Initialize DatabaseManager for loading prompts
        database_manager = DatabaseManager()
        
        # Load prompts from DB (may contain hardcoded table references)
        few_shot_prompt = ""
        schema_structure = ""
        rules = ""
        
        if domain_name:
            logger.info(f"Loading prompts from DB for domain_name={domain_name}")
            few_shot_prompt, schema_structure, rules = database_manager.read_and_decode(domain_name)
            logger.info(f"Loaded prompts: few_shot_len={len(few_shot_prompt)}, schema_len={len(schema_structure)}, rules_len={len(rules)}")
        else:
            logger.warning("No domain_name provided, using empty prompts")

        # CRITICAL: Use runtime schema/table from agentic API, not from DB config
        if not schema_name or not table_name:
            logger.warning("schema_name or table_name not provided in request. Falling back to defaults.")
            schema_name = schema_name or "operations"
            table_name = table_name or "season_yield_analysis_vw"
            logger.warning(f"Using fallback: schema_name={schema_name}, table_name={table_name}")

        # Initialize DatabaseManager and LLMClient for execution
        db_manager = DatabaseManager(schema_name_override=schema_name)
        client = LLMClient()
        logger.info("Initialized DatabaseManager and LLMClient.")

        # Build dynamic prompt with runtime schema/table (replaces hardcoded references)
        final_user_prompt = build_dynamic_prompt(
            user_query=question,
            schema_name=schema_name,
            table_name=table_name,
            few_shot_prompt=few_shot_prompt,
            schema_structure=schema_structure,
            rules=rules,
            customer_name=customer_name
        )
        
        logger.info("Final prompt prepared for LLM.")
        logger.debug(f"Final prompt preview (first 500 chars): {final_user_prompt[:500]}")

        # Generate SQL Query
        response = client.prompt_to_sql(final_user_prompt)
        message_content = response.choices[0].message.content
        logger.info(f"LLM raw output: {message_content[:500]}")

        sql_query = client.get_ai_query(message_content)
        logger.info(f"Generated SQL query: {sql_query}")

        # Guardrail: Verify SQL uses correct schema.table
        if sql_query:
            sql_str = sql_query[0] if isinstance(sql_query, list) else str(sql_query)
            expected_table_ref = f"{schema_name}.{table_name}"
            
            # Check if SQL contains hardcoded table references
            hardcoded_patterns = [
                r'operations\.season_yield_analysis_vw',
                r'\bseason_yield_analysis_vw\b',
            ]
            
            for pattern in hardcoded_patterns:
                if re.search(pattern, sql_str, re.IGNORECASE):
                    logger.error(f"GUARDRAIL VIOLATION: Generated SQL contains hardcoded table reference: {pattern}")
                    logger.error(f"Expected: {expected_table_ref}, Found hardcoded table in SQL")
                    # Attempt to fix by replacing
                    sql_str = re.sub(pattern, expected_table_ref, sql_str, flags=re.IGNORECASE)
                    if isinstance(sql_query, list):
                        sql_query[0] = sql_str
                    else:
                        sql_query = sql_str
                    logger.warning(f"Auto-corrected SQL to use {expected_table_ref}")
            
            # Verify correct table is used
            if expected_table_ref.lower() not in sql_str.lower():
                logger.warning(f"Generated SQL may not use expected table {expected_table_ref}")
                logger.warning(f"SQL: {sql_str[:200]}")

        if not sql_query:
            logger.error("Failed to generate SQL query.")
            return {
                'statusCode': 500,
                'body': json.dumps({"error": "Failed to generate SQL query"}),
                'headers': cors_header
            }

        # Execute SQL Query
        columns, rows, query = db_manager.get_results(sql_query)
        if columns is None or rows is None:
            logger.error("Query execution returned no results.")
            return {
                'statusCode': 500,
                'body': json.dumps({"error": "Failed to execute query"}),
                'headers': cors_header
            }

        logger.info(f"Query executed successfully for {schema_name}.{table_name}. Columns: {columns}")

        json_output = Utils.build_json(columns, rows)
        logger.info("Output formatted into JSON.")

        db_manager.close_connection()
        database_manager.close_connection()
        logger.info("Database connections closed.")

        return {
            'statusCode': 200,
            'body': json.dumps({"json_output": json_output, "sql_query": query}, indent=4),
            'headers': cors_header
        }

    except Exception as e:
        logger.exception("Exception occurred during Lambda execution.")
        return {
            'statusCode': 500,
            'body': json.dumps({"error": str(e)}),
            'headers': cors_header
        }