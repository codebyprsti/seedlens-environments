import json
import logging
from database import DatabaseManager
from llm_client import LLMClient
from utils import Utils
from config import schema, few_shot_prompt

# Setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """AWS Lambda function entry point."""
    cors_header = {
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
    }

    logger.info("Lambda handler invoked.")
    logger.info(f"Event received: {json.dumps(event)}")

    try:
        # Extract query from event
        if "body" in event and event["body"]:  # For API Gateway
            parsed_data = json.loads(event["body"])
            logger.info("Parsed data from event body.")
        else:  # For local testing
            parsed_data = event
            logger.info("Parsed data from direct event.")

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

        # Initialize DatabaseManager and LLMClient
        db_manager = DatabaseManager()
        client = LLMClient()
        logger.info("Initialized DatabaseManager and LLMClient.")

        rule = (
            "use gross_amount unless specified. "
            "if there is month, include only month name as month. "
            "order results based on the date, if there is date included. "
            "return only the last query that is relavant to the prompt"
        )

        final_user_prompt = f"Schema: {schema}\n{few_shot_prompt}\n{rule}\nUser: {question}\n "
        logger.info("Final prompt prepared for LLM.")

        # Generate SQL Query
        response = client.prompt_to_sql(final_user_prompt)
        message_content = response.choices[0].message.content
        logger.info(f"LLM raw output: {message_content}")

        sql_query = client.get_ai_query(message_content)
        logger.info(f"Generated SQL query: {sql_query}")

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

        logger.info(f"Query executed successfully. Columns: {columns}")

        json_output = Utils.build_json(columns, rows)
        logger.info("Output formatted into JSON.")

        db_manager.close_connection()
        logger.info("Database connection closed.")

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