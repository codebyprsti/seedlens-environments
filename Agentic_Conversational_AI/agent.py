"""
Agentic AI Orchestrator for Conversational Database Analytics
Requires Python 3.10+
Enhanced with advanced analytics features
"""
import json
from tools import LLMTool, DatabaseTool
from utils import Utils


class ConversationalAgent:
    """
    Agent that orchestrates between LLM and Database tools for conversational analytics
    """
    
    def __init__(self, llm_api_url: str, db_config: dict[str, str], 
                 api_key: str = None, model: str = None, 
                 few_shot_prompt: str = None, rules: str = None):
        """
        Initialize agent with LLM API and database configuration
        
        Args:
            llm_api_url: URL endpoint for the LLM API
            db_config: Database configuration dict with host, port, database, user, password
            api_key: Optional API key for authenticated APIs
            model: Optional model name override
            few_shot_prompt: Optional few-shot examples for better SQL generation
            rules: Optional rules/guidelines for SQL generation
        """
        self.llm_tool = LLMTool(llm_api_url, api_key=api_key, model=model)
        self.db_tool = DatabaseTool(db_config)
        self.conversation_history: list[dict[str, str]] = []
        self.current_table_context: dict[str, any] | None = None
        self.few_shot_prompt = few_shot_prompt or ""
        self.rules = rules or ""
        
    def set_table_context(self, schema: str, table: str) -> dict[str, any]:
        """
        Set the current table context by fetching table structure
        
        Args:
            schema: Database schema name
            table: Table name
            
        Returns:
            Dict with table structure information
        """
        table_structure = self.db_tool.get_table_structure(schema, table)
        self.current_table_context = {
            "schema": schema,
            "table": table,
            "structure": table_structure
        }
        return self.current_table_context
    
    def process_query(self, user_query: str) -> dict[str, any]:
        """
        Process user query through the agent workflow
        
        Args:
            user_query: Natural language query from user
            
        Returns:
            Dict containing the response with SQL, data, and metadata
        """
        if not self.current_table_context:
            return {
                "error": "No table context set. Please connect to a schema and table first.",
                "success": False
            }
        
        # Step 1: Use LLM to generate SQL from natural language
        sql_generation_result = self._generate_sql(user_query)
        
        if not sql_generation_result["success"]:
            return sql_generation_result
        
        generated_sql = sql_generation_result["sql"]
        
        # Step 2: Execute SQL on database
        schema = self.current_table_context.get("schema") if self.current_table_context else None
        execution_result = self._execute_sql(generated_sql, schema)
        
        if not execution_result["success"]:
            # Try to get LLM to fix the SQL if there's an error
            fixed_result = self._attempt_sql_fix(
                user_query, 
                generated_sql, 
                execution_result.get("error", "")
            )
            if fixed_result["success"]:
                execution_result = fixed_result
            else:
                return execution_result
        
        # Step 3: Format results with dimensions/measures classification
        column_descriptors = execution_result.get("column_descriptors", [])
        rows = execution_result.get("data", [])
        
        # Convert rows to tuples for utils.build_json if needed
        if column_descriptors and rows:
            formatted_data = Utils.build_json(column_descriptors, rows)
        else:
            formatted_data = {
                "dimensions": [],
                "measures": [],
                "data": execution_result["data"]
            }
        
        # Step 4: Prepare response
        response = {
            "success": True,
            "query": user_query,
            "sql": execution_result.get("sql", generated_sql),
            "data": execution_result["data"],
            "formatted_data": formatted_data,  # With dimensions/measures
            "columns": execution_result.get("columns", []),
            "row_count": len(execution_result["data"]),
            "explanation": sql_generation_result.get("explanation", "")
        }
        
        # Add to conversation history
        self.conversation_history.append({
            "query": user_query,
            "sql": response["sql"],
            "row_count": response["row_count"]
        })
        
        return response
    
    def _generate_sql(self, user_query: str) -> dict[str, any]:
        """
        Generate SQL using LLM tool
        
        Args:
            user_query: Natural language query
            
        Returns:
            Dict with generated SQL and metadata
        """
        # Prepare context for LLM
        table_info = self.current_table_context["structure"]
        schema = self.current_table_context["schema"]
        table = self.current_table_context["table"]
        
        # Create prompt for LLM
        prompt = self._create_sql_generation_prompt(
            user_query, 
            schema, 
            table, 
            table_info
        )
        
        # Call LLM
        result = self.llm_tool.generate_sql(prompt, self.conversation_history)
        
        return result
    
    def _execute_sql(self, sql: str, schema: str = None) -> dict[str, any]:
        """
        Execute SQL using Database tool
        
        Args:
            sql: SQL query to execute (can be list for retry)
            schema: Optional schema name
            
        Returns:
            Dict with query results
        """
        return self.db_tool.execute_query(sql, schema=schema)
    
    def _attempt_sql_fix(self, user_query: str, failed_sql: str, error: str) -> dict[str, any]:
        """
        Attempt to fix failed SQL using LLM
        
        Args:
            user_query: Original user query
            failed_sql: SQL that failed
            error: Error message from database
            
        Returns:
            Dict with execution result of fixed SQL
        """
        table_info = self.current_table_context["structure"]
        schema = self.current_table_context["schema"]
        table = self.current_table_context["table"]
        
        # Create prompt for SQL fix
        fix_prompt = f"""The following SQL query failed with an error. Please fix it.

Original user question: {user_query}

Failed SQL:
{failed_sql}

Error:
{error}

Table: {schema}.{table}
Table Structure:
{json.dumps(table_info, indent=2)}

Please provide a corrected SQL query that will work."""
        
        fix_result = self.llm_tool.generate_sql(fix_prompt, [])
        
        if not fix_result["success"]:
            return {
                "success": False,
                "error": f"Failed to fix SQL. Original error: {error}"
            }
        
        # Try executing the fixed SQL
        fixed_sql = fix_result["sql"]
        execution_result = self.db_tool.execute_query(fixed_sql)
        
        if execution_result["success"]:
            execution_result["sql"] = fixed_sql
            execution_result["fixed"] = True
            
        return execution_result
    
    def _create_sql_generation_prompt(
        self, 
        user_query: str, 
        schema: str, 
        table: str, 
        table_info: dict[str, any]
    ) -> str:
        """
        Create a detailed prompt for SQL generation
        
        Args:
            user_query: User's natural language query
            schema: Database schema
            table: Table name
            table_info: Table structure information
            
        Returns:
            Formatted prompt string
        """
        # Build comprehensive prompt with few-shot examples and rules
        prompt_parts = []
        
        # Add schema information
        prompt_parts.append(f"Schema: {schema}")
        prompt_parts.append(f"Table: {schema}.{table}")
        prompt_parts.append(f"\nTable Structure:\n{json.dumps(table_info, indent=2)}")
        
        # Add few-shot examples if available
        if self.few_shot_prompt:
            prompt_parts.append(f"\n{self.few_shot_prompt}")
        
        # Add rules if available
        if self.rules:
            prompt_parts.append(f"\nRules: {self.rules}")
        
        # Add requirements
        prompt_parts.append(f"""
Requirements:
1. Generate a valid PostgreSQL query
2. Use the fully qualified table name: {schema}.{table}
3. Return ONLY the SQL query, no explanations unless asked
4. Ensure the query is safe and read-only (SELECT statements only)
5. Consider adding LIMIT clause if not specified to avoid large result sets
6. Use gross_amount unless specified otherwise
7. If there is month, include only month name as month
8. Order results based on the date, if there is date included
9. Return only the last query that is relevant to the prompt""")

        # Add user question
        prompt_parts.append(f"\nUser Question: {user_query}")
        prompt_parts.append("\nGenerate the SQL query:")
        
        return "\n".join(prompt_parts)
    
    def get_conversation_history(self) -> list[dict[str, str]]:
        """Get the conversation history"""
        return self.conversation_history
    
    def clear_conversation_history(self):
        """Clear the conversation history"""
        self.conversation_history = []
    
    def close(self):
        """Close database connections"""
        self.db_tool.close()

