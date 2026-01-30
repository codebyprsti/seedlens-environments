import psycopg2
from psycopg2 import errors
import logging
from config import DATABASE_CONFIG
from config import schema_name

logger = logging.getLogger()
logger.setLevel(logging.INFO)

class DatabaseManager:
    def __init__(self):
        self.conn = psycopg2.connect(**DATABASE_CONFIG)

    def get_connection(self):
        return self.conn

    def get_results(self, sql_query):
      """Execute SQL query and fetch results."""
      for query in sql_query[::-1]:  # Try queries in reverse order
        cur = self.conn.cursor()
        try:
          cur.execute(f"SET search_path TO {schema_name};")
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
