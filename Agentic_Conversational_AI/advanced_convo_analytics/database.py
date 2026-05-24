import psycopg2
from psycopg2 import errors
import logging
import base64
import re

logger = logging.getLogger()
logger.setLevel(logging.INFO)

try:
    from config import DATABASE_CONFIG
    from config import schema_name
except ImportError:
    DATABASE_CONFIG = None
    schema_name = "public"


class DatabaseManager:
    """Optional db_config_override: use when running in-process with orchestrator config."""

    def __init__(self, db_config_override=None, schema_name_override=None):
        config = db_config_override or DATABASE_CONFIG
        if not config:
            raise ValueError("Database config required (pass db_config_override or set env/config)")
        self.conn = psycopg2.connect(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 5432)),
            dbname=config.get("database") or config.get("dbname"),
            user=config.get("user"),
            password=config.get("password"),
        )
        self.schema_name = schema_name_override or schema_name

    def get_connection(self):
        return self.conn

    def get_results(self, sql_query):
      """Execute SQL query and fetch results."""
      for query in sql_query[::-1]:  # Try queries in reverse order
        cur = self.conn.cursor()
        try:
          cur.execute(f"SET search_path TO {self.schema_name};")
          cur.execute(query)
          rows = cur.fetchall()
          columns = cur.description
          cur.close()
          return columns, rows, query

        except psycopg2.Error as e:
          self.conn.rollback()  

          # Handle GROUPING ERROR
          if e.pgcode == '42803':
            logger.error(f"GroupingError: {e}")
            cur.close()
            continue
          else:
            logger.error(f"Unhandled DB error: {e}")
            cur.close()
            raise

      raise ValueError("All queries failed to execute due to errors.")

    def close_connection(self):
        self.conn.close()

    def read_and_decode(self, domain_name: str = None):
        """
        Load prompts from database by domain_name (customer_name).
        Returns prompts WITHOUT hardcoded table references - these will be replaced dynamically.
        
        Args:
            domain_name: Domain/customer name to lookup in database
            
        Returns:
            tuple: (few_shot_prompt, schema_structure, rules)
            - All hardcoded table references are replaced with placeholders
        """
        if not domain_name:
            logger.warning("read_and_decode: domain_name not provided, returning empty prompts")
            return "", "", ""
        
        try:
            cur = self.conn.cursor()
            # Query to fetch base64-encoded prompts from database
            # Adjust table/column names based on your actual schema
            query = """
                SELECT few_shot_prompt, schema_structure, rules
                FROM agentic_prompts  -- Adjust table name as needed
                WHERE domain_name = %s OR customer_name = %s
                LIMIT 1
            """
            cur.execute(query, (domain_name, domain_name))
            row = cur.fetchone()
            cur.close()
            
            if not row:
                logger.warning(f"read_and_decode: No prompts found for domain_name={domain_name}")
                return "", "", ""
            
            few_shot_b64, schema_b64, rules_b64 = row
            
            # Decode base64 if present
            def decode_if_b64(text):
                if not text:
                    return ""
                try:
                    # Try base64 decode
                    decoded = base64.b64decode(text).decode('utf-8')
                    logger.debug(f"Decoded base64 prompt (length={len(decoded)})")
                    return decoded
                except Exception:
                    # Not base64, return as-is
                    return text if text else ""
            
            few_shot = decode_if_b64(few_shot_b64) if few_shot_b64 else ""
            schema = decode_if_b64(schema_b64) if schema_b64 else ""
            rules = decode_if_b64(rules_b64) if rules_b64 else ""
            
            logger.info(f"read_and_decode: Loaded prompts for domain_name={domain_name}")
            logger.debug(f"Few-shot length: {len(few_shot)}, Schema length: {len(schema)}, Rules length: {len(rules)}")
            
            return few_shot, schema, rules
            
        except psycopg2.Error as e:
            logger.error(f"read_and_decode: Database error: {e}")
            # Return empty prompts on error
            return "", "", ""
        except Exception as e:
            logger.exception(f"read_and_decode: Unexpected error: {e}")
            return "", "", ""
