"""
Example usage of the Conversational Database Analytics Agent
"""
from agent import ConversationalAgent

# Example 1: Basic usage
def basic_example():
    """Basic example of using the agent programmatically"""
    
    # Configuration
    llm_api_url = "http://your-llm-api.com/generate"
    db_config = {
        "host": "localhost",
        "port": 5432,
        "database": "your_database",
        "user": "your_user",
        "password": "your_password"
    }
    
    # Initialize agent
    agent = ConversationalAgent(llm_api_url, db_config)
    
    # Set table context
    table_context = agent.set_table_context("public", "users")
    print(f"Table Context: {table_context}")
    
    # Process queries
    queries = [
        "Show me the top 10 users by age",
        "What is the average age of users?",
        "How many users are there in total?"
    ]
    
    for query in queries:
        print(f"\nQuery: {query}")
        result = agent.process_query(query)
        
        if result.get("success"):
            print(f"SQL: {result['sql']}")
            print(f"Rows: {result['row_count']}")
            print(f"Data: {result['data'][:3]}...")  # Show first 3 rows
        else:
            print(f"Error: {result.get('error')}")
    
    # Clean up
    agent.close()


# Example 2: Using with custom error handling
def advanced_example():
    """Advanced example with custom error handling"""
    
    llm_api_url = "http://your-llm-api.com/generate"
    db_config = {
        "host": "localhost",
        "port": 5432,
        "database": "analytics_db",
        "user": "analyst",
        "password": "secure_password"
    }
    
    try:
        agent = ConversationalAgent(llm_api_url, db_config)
        
        # Set table context with error handling
        try:
            context = agent.set_table_context("sales", "transactions")
            print(f"Successfully connected to {context['schema']}.{context['table']}")
            print(f"Columns: {[col['column_name'] for col in context['structure']['columns']]}")
        except Exception as e:
            print(f"Failed to set table context: {e}")
            return
        
        # Process query with detailed response
        query = "What are the total sales by month for the last year?"
        result = agent.process_query(query)
        
        if result.get("success"):
            print(f"\n✓ Query successful!")
            print(f"SQL Generated: {result['sql']}")
            print(f"Records Returned: {result['row_count']}")
            
            # Process data
            for row in result['data'][:5]:
                print(row)
                
            # Get conversation history
            history = agent.get_conversation_history()
            print(f"\nConversation History: {len(history)} queries")
            
        else:
            print(f"\n✗ Query failed: {result.get('error')}")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if agent:
            agent.close()


# Example 3: Interactive CLI
def interactive_cli():
    """Simple interactive CLI for testing the agent"""
    import sys
    
    print("=== Conversational Database Analytics Agent ===\n")
    
    # Get configuration from user
    llm_api_url = input("LLM API URL: ").strip()
    db_host = input("Database Host [localhost]: ").strip() or "localhost"
    db_port = input("Database Port [5432]: ").strip() or "5432"
    db_name = input("Database Name: ").strip()
    db_user = input("Database User: ").strip()
    db_password = input("Database Password: ").strip()
    
    db_config = {
        "host": db_host,
        "port": int(db_port),
        "database": db_name,
        "user": db_user,
        "password": db_password
    }
    
    try:
        agent = ConversationalAgent(llm_api_url, db_config)
        print("\n✓ Agent initialized successfully!\n")
        
        # List schemas
        schemas = agent.db_tool.list_schemas()
        print(f"Available Schemas: {', '.join(schemas)}\n")
        
        # Get schema and table
        schema = input("Select Schema: ").strip()
        tables = agent.db_tool.list_tables(schema)
        print(f"Available Tables: {', '.join(tables)}\n")
        
        table = input("Select Table: ").strip()
        
        # Set table context
        context = agent.set_table_context(schema, table)
        print(f"\n✓ Context set to {schema}.{table}")
        print(f"Columns: {[col['column_name'] for col in context['structure']['columns']]}\n")
        
        # Query loop
        print("Enter your queries (type 'exit' to quit, 'history' to view history):\n")
        
        while True:
            query = input("Query > ").strip()
            
            if query.lower() == 'exit':
                break
            elif query.lower() == 'history':
                history = agent.get_conversation_history()
                for i, item in enumerate(history, 1):
                    print(f"{i}. {item['query']} -> {item['row_count']} rows")
                continue
            elif not query:
                continue
            
            result = agent.process_query(query)
            
            if result.get("success"):
                print(f"\n✓ SQL: {result['sql']}")
                print(f"✓ Rows: {result['row_count']}\n")
                
                # Display first few rows
                for i, row in enumerate(result['data'][:5], 1):
                    print(f"  {i}. {row}")
                
                if result['row_count'] > 5:
                    print(f"  ... and {result['row_count'] - 5} more rows\n")
            else:
                print(f"\n✗ Error: {result.get('error')}\n")
        
    except KeyboardInterrupt:
        print("\n\nExiting...")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        if agent:
            agent.close()
            print("✓ Connection closed")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        interactive_cli()
    else:
        print("Run with --interactive flag for CLI mode")
        print("Or modify this file to run basic_example() or advanced_example()")

