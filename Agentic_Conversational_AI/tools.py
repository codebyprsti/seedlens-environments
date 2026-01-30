"""
Tools for Agentic AI: LLM API Tool and Database Tool
Requires Python 3.10+
Enhanced with advanced analytics features
"""
import requests
import psycopg2
import json
import re
import logging
from psycopg2.extras import RealDictCursor
from psycopg2 import errors

logger = logging.getLogger(__name__)


class LLMTool:
    """
    Tool for interacting with LLM API for text-to-SQL generation
    Supports Ollama, OpenAI-compatible APIs (SambaNova), and generic REST APIs
    """
    
    def __init__(self, api_url: str, api_key: str = None, model: str = None):
        """
        Initialize LLM tool with API endpoint
        
        Args:
            api_url: URL endpoint for the LLM API
            api_key: Optional API key for authenticated APIs
            model: Optional model name override
        """
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        
    def generate_sql(
        self, 
        prompt: str, 
        conversation_history: list[dict[str, str]] | None = None
    ) -> dict[str, any]:
        """
        Generate SQL from natural language using LLM API
        
        Args:
            prompt: The prompt containing table structure and user query
            conversation_history: Optional conversation history for context
            
        Returns:
            Dict containing generated SQL and metadata
        """
        try:
            # Detect API type
            is_ollama = "11434" in self.api_url or "ollama" in self.api_url.lower()
            is_openai_compatible = "openai" in self.api_url.lower() or "sambanova" in self.api_url.lower() or self.api_key is not None
            
            if is_ollama:
                # Ollama API format
                api_url = self.api_url.rstrip('/')
                if not api_url.endswith('/api/generate'):
                    api_url = f"{api_url}/api/generate"
                
                payload = {
                    "model": self.model or "llama2",
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 500
                    }
                }
                headers = {"Content-Type": "application/json"}
                
            elif is_openai_compatible:
                # OpenAI/SambaNova compatible API format
                api_url = self.api_url.rstrip('/')
                if not api_url.endswith('/chat/completions'):
                    api_url = f"{api_url}/chat/completions"
                
                headers = {
                    "Content-Type": "application/json"
                }
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                
                # Build messages from conversation history
                messages = [
                    {"role": "system", "content": "You are a helpful PostgreSQL SQL generation assistant"}
                ]
                
                # Add conversation history if available
                if conversation_history:
                    for hist in conversation_history[-5:]:  # Last 5 for context
                        if "query" in hist:
                            messages.append({"role": "user", "content": hist["query"]})
                        if "sql" in hist:
                            messages.append({"role": "assistant", "content": f"SQL: {hist['sql']}"})
                
                messages.append({"role": "user", "content": prompt})
                
                payload = {
                    "model": self.model or "Meta-Llama-3.3-70B-Instruct",
                    "messages": messages,
                    "temperature": 0.1,
                    "top_p": 0.1
                }
            else:
                # Generic API format
                api_url = self.api_url
            payload = {
                "prompt": prompt,
                "conversation_history": conversation_history or [],
                    "temperature": 0.1,
                "max_tokens": 500
            }
                headers = {"Content-Type": "application/json"}
            
            # Make API call to LLM
            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"LLM API returned status {response.status_code}: {response.text}"
                }
            
            response_data = response.json()
            
            # Extract SQL from response
            if is_openai_compatible:
                # OpenAI/SambaNova format
                message_content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                sql_queries = self._extract_sql_from_content(message_content)
                sql = sql_queries[0] if sql_queries else ""
            else:
            sql = self._extract_sql(response_data)
            
            return {
                "success": True,
                "sql": sql,
                "raw_response": response_data,
                "explanation": response_data.get("explanation", "")
            }
            
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Failed to connect to LLM API: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Error generating SQL: {str(e)}"
            }
    
    def _extract_sql(self, response_data: dict[str, any]) -> str:
        """
        Extract SQL from LLM API response
        
        Args:
            response_data: Response from LLM API
            
        Returns:
            Extracted SQL query
        """
        # Try different common response formats
        if "sql" in response_data:
            sql = response_data["sql"]
        elif "response" in response_data:
            # Ollama and some APIs use "response"
            sql = response_data["response"]
        elif "text" in response_data:
            sql = response_data["text"]
        elif "content" in response_data:
            sql = response_data["content"]
        elif "choices" in response_data and len(response_data["choices"]) > 0:
            # OpenAI-like format
            sql = response_data["choices"][0].get("text", "") or \
                  response_data["choices"][0].get("message", {}).get("content", "")
        else:
            # Try to get the first string value
            sql = str(response_data)
        
        # Clean up SQL
        sql = sql.strip()
        
        # Remove markdown code blocks if present
        if "```sql" in sql:
            sql = sql.split("```sql")[1].split("```")[0].strip()
        elif "```" in sql:
            sql = sql.split("```")[1].split("```")[0].strip()
        
        return sql
    
    def _extract_sql_from_content(self, message_content: str) -> list[str]:
        """
        Extract SQL queries from LLM response message content (OpenAI format)
        
        Args:
            message_content: Response content from OpenAI-compatible API
            
        Returns:
            List of extracted SQL queries
        """
        try:
            # Try to find SQL in code blocks
            queries = re.findall(r"```sql\n(.*?)\n```", message_content, re.IGNORECASE | re.DOTALL)
            
            if not queries:
                # Fallback: find SELECT statements
                queries = re.findall(r"(select\s+.*?;)", message_content, re.IGNORECASE | re.DOTALL)
            
            return [q.strip() for q in queries] if queries else []
        except Exception as e:
            logger.error(f"Error extracting SQL from content: {e}")
            return []


class DatabaseTool:
    """
    Tool for interacting with PostgreSQL database
    """
    
    def __init__(self, db_config: dict[str, str]):
        """
        Initialize database tool with connection config
        
        Args:
            db_config: Database configuration dict with host, port, database, user, password
        """
        self.db_config = db_config
        self.connection: psycopg2.extensions.connection | None = None
        self._connect()
        
    def _connect(self):
        """Establish database connection"""
        try:
            self.connection = psycopg2.connect(
                host=self.db_config.get("host", "localhost"),
                port=self.db_config.get("port", 5432),
                database=self.db_config["database"],
                user=self.db_config["user"],
                password=self.db_config["password"]
            )
        except Exception as e:
            print(f"Failed to connect to database: {e}")
            raise
    
    def get_table_structure(self, schema: str, table: str) -> dict[str, any]:
        """
        Get table structure including columns, types, and constraints
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            Dict with table structure information
        """
        if not self.connection:
            self._connect()
        
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                # Get column information
                cursor.execute("""
                    SELECT 
                        column_name,
                        data_type,
                        is_nullable,
                        column_default,
                        character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position;
                """, (schema, table))
                
                columns = cursor.fetchall()
                
                # Get primary key information
                cursor.execute("""
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid
                        AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = %s::regclass
                        AND i.indisprimary;
                """, (f"{schema}.{table}",))
                
                primary_keys = [row[0] for row in cursor.fetchall()]
                
                # Get sample values for better SQL generation
                cursor.execute(f"""
                    SELECT * FROM {schema}.{table}
                    LIMIT 3;
                """)
                
                sample_data = cursor.fetchall()
                
                return {
                    "columns": [dict(col) for col in columns],
                    "primary_keys": primary_keys,
                    "sample_data": [dict(row) for row in sample_data] if sample_data else []
                }
                
        except Exception as e:
            return {
                "error": f"Failed to get table structure: {str(e)}",
                "columns": [],
                "primary_keys": [],
                "sample_data": []
            }
    
    def execute_query(self, sql: str, schema: str = None) -> dict[str, any]:
        """
        Execute SQL query and return results with advanced error handling
        
        Args:
            sql: SQL query to execute (can be a single query or list of queries)
            schema: Optional schema name to set search_path
            
        Returns:
            Dict with query results in JSON format
        """
        if not self.connection:
            self._connect()
        
        # Handle multiple queries (for retry logic)
        sql_queries = [sql] if isinstance(sql, str) else sql
        if not isinstance(sql_queries, list):
            sql_queries = [sql_queries]
        
        # Try queries in reverse order (most recent first)
        for query in sql_queries[::-1]:
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                # Ensure it's a read-only query
                    sql_lower = query.lower().strip()
                if not sql_lower.startswith("select"):
                    return {
                        "success": False,
                        "error": "Only SELECT queries are allowed for security reasons"
                    }
                
                    # Set search path if schema provided
                    if schema:
                        cursor.execute(f"SET search_path TO {schema};")
                    
                    cursor.execute(query)
                rows = cursor.fetchall()
                
                # Convert to list of dicts (JSON-serializable)
                data = [dict(row) for row in rows]
                
                    # Get column names and types
                    columns = cursor.description if cursor.description else []
                    column_names = [desc[0] for desc in columns]
                
                return {
                    "success": True,
                    "data": data,
                        "columns": column_names,
                        "column_descriptors": columns,  # For utils.build_json
                        "row_count": len(data),
                        "sql": query
                }
                
        except psycopg2.Error as e:
                error_code = getattr(e, 'pgcode', None)
                
                # Handle GROUPING ERROR (42803) - try next query
                if error_code == '42803':
                    logger.warning(f"Grouping error with query, trying next: {e}")
                    self.connection.rollback()
                    continue
                else:
                    # Other errors - return failure
                    self.connection.rollback()
            return {
                "success": False,
                "error": f"Database error: {str(e)}",
                        "error_code": error_code,
                "data": []
            }
        except Exception as e:
                self.connection.rollback()
            return {
                "success": False,
                "error": f"Error executing query: {str(e)}",
                    "data": []
                }
        
        # All queries failed
        return {
            "success": False,
            "error": "All queries failed to execute due to errors.",
                "data": []
            }
    
    def list_schemas(self) -> list[str]:
        """
        List all available schemas in the database
        
        Returns:
            List of schema names
        """
        if not self.connection:
            self._connect()
        
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT schema_name 
                    FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY schema_name;
                """)
                
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f"Error listing schemas: {e}")
            return []
    
    def list_tables(self, schema: str) -> list[str]:
        """
        List all tables in a schema
        
        Args:
            schema: Schema name
            
        Returns:
            List of table names
        """
        if not self.connection:
            self._connect()
        
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_type = 'BASE TABLE'
                    ORDER BY table_name;
                """, (schema,))
                
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f"Error listing tables: {e}")
            return []
    
    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            self.connection = None

