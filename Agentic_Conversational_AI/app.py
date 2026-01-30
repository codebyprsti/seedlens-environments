"""
Flask Web Server for Conversational Database Analytics
All configuration is loaded from config.yaml file
"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from agent import ConversationalAgent
from config import Config
import os
import sys

app = Flask(__name__, static_folder='static')
CORS(app)

# Initialize agent from config file at startup
agent: ConversationalAgent = None


@app.route('/')
def index():
    """Serve the main web page"""
    return send_from_directory('static', 'index.html')


@app.route('/api/config', methods=['GET'])
def get_config():
    """
    Get current configuration (read-only)
    Returns configuration status without sensitive data
    """
    if not agent:
        return jsonify({
            "configured": False,
            "error": "Agent not initialized. Check config.yaml file."
        }), 400
    
    try:
        return jsonify({
            "configured": True,
            "schema": Config.DEFAULT_SCHEMA,
            "table": Config.DEFAULT_TABLE,
            "llm_configured": bool(Config.LLM_API_URL),
            "db_configured": bool(Config.DB_CONFIG),
            "message": "Configuration loaded from config.yaml"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/schemas', methods=['GET'])
def list_schemas():
    """List all available database schemas"""
    if not agent:
        return jsonify({"error": "Agent not configured. Please configure first."}), 400
    
    try:
        schemas = agent.db_tool.list_schemas()
        return jsonify({
            "success": True,
            "schemas": schemas
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/tables/<schema>', methods=['GET'])
def list_tables(schema):
    """List all tables in a schema"""
    if not agent:
        return jsonify({"error": "Agent not configured. Please configure first."}), 400
    
    try:
        tables = agent.db_tool.list_tables(schema)
        return jsonify({
            "success": True,
            "schema": schema,
            "tables": tables
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/table/context', methods=['GET'])
def get_table_context():
    """
    Get the current table context (loaded from config)
    """
    if not agent:
        return jsonify({"error": "Agent not configured. Check config.yaml file."}), 400
    
    try:
        if not agent.current_table_context:
            return jsonify({
                "success": False,
                "error": "No table context set in config.yaml"
            }), 400
        
        return jsonify({
            "success": True,
            "context": agent.current_table_context
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/query', methods=['POST'])
def process_query():
    """
    Process a natural language query
    
    Expected JSON payload:
    {
        "query": "Show me the top 10 users by age"
    }
    """
    if not agent:
        return jsonify({"error": "Agent not configured. Please configure first."}), 400
    
    try:
        data = request.json
        user_query = data.get('query')
        
        if not user_query:
            return jsonify({"error": "Query is required"}), 400
        
        result = agent.process_query(user_query)
        
        if result.get("success"):
            return jsonify(result)
        else:
            return jsonify(result), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/history', methods=['GET'])
def get_history():
    """Get conversation history"""
    if not agent:
        return jsonify({"error": "Agent not configured. Please configure first."}), 400
    
    try:
        history = agent.get_conversation_history()
        return jsonify({
            "success": True,
            "history": history
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/history', methods=['DELETE'])
def clear_history():
    """Clear conversation history"""
    if not agent:
        return jsonify({"error": "Agent not configured. Please configure first."}), 400
    
    try:
        agent.clear_conversation_history()
        return jsonify({
            "success": True,
            "message": "History cleared"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/query/advanced', methods=['POST'])
def process_advanced_query():
    """
    Process a natural language query with advanced analytics formatting
    
    Expected JSON payload:
    {
        "query": "Show me the top 10 products by sales",
        "schema": "operations"  # Optional, uses default if not provided
    }
    
    Returns formatted data with dimensions and measures classification
    """
    if not agent:
        return jsonify({"error": "Agent not configured. Please configure first."}), 400
    
    try:
        data = request.json
        user_query = data.get('query')
        schema_override = data.get('schema')
        
        if not user_query:
            return jsonify({"error": "Query is required"}), 400
        
        # Set schema context if provided
        if schema_override and agent.current_table_context:
            table = agent.current_table_context.get('table')
            if table:
                agent.set_table_context(schema_override, table)
        
        result = agent.process_query(user_query)
        
        if result.get("success"):
            return jsonify({
                "success": True,
                "query": result.get("query"),
                "sql": result.get("sql"),
                "data": result.get("data"),
                "formatted_data": result.get("formatted_data", {}),  # With dimensions/measures
                "columns": result.get("columns", []),
                "row_count": result.get("row_count", 0),
                "explanation": result.get("explanation", "")
            })
        else:
            return jsonify(result), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/analytics/query', methods=['POST'])
def analytics_query():
    """
    Advanced analytics endpoint - similar to lambda_function.py
    
    Expected JSON payload:
    {
        "user_query": "get percentage revenue of top 10 products"
    }
    """
    if not agent:
        return jsonify({"error": "Agent not configured. Please configure first."}), 400
    
    try:
        data = request.json
        user_query = data.get('user_query') or data.get('query')
        
        if not user_query:
            return jsonify({"error": "user_query is required"}), 400
        
        result = agent.process_query(user_query)
        
        if result.get("success"):
            formatted_data = result.get("formatted_data", {})
            return jsonify({
                "statusCode": 200,
                "json_output": formatted_data,
                "sql_query": result.get("sql")
            })
        else:
            return jsonify({
                "statusCode": 500,
                "error": result.get("error", "Failed to process query")
            }), 500
            
    except Exception as e:
        return jsonify({
            "statusCode": 500,
            "error": str(e)
        }), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "agent_configured": agent is not None
    })


def initialize_agent():
    """Initialize agent from config file"""
    global agent
    
    try:
        # Load configuration
        Config.load_config('config.yaml')
        
        # Check required configuration
        if not Config.LLM_API_URL:
            print("ERROR: LLM_API_URL not configured in config.yaml")
            return False
        
        if not Config.DB_CONFIG:
            print("ERROR: Database configuration not found in config.yaml")
            return False
        
        if not Config.DEFAULT_TABLE:
            print("WARNING: DEFAULT_TABLE not configured. You'll need to specify it later.")
        
        # Initialize agent with advanced features
        agent = ConversationalAgent(
            Config.LLM_API_URL, 
            Config.DB_CONFIG,
            api_key=Config.LLM_API_KEY if Config.LLM_API_KEY else None,
            model=Config.LLM_MODEL if Config.LLM_MODEL else None,
            few_shot_prompt=Config.FEW_SHOT_PROMPT if Config.FEW_SHOT_PROMPT else None,
            rules=Config.SQL_RULES if Config.SQL_RULES else None
        )
        print(f"Agent initialized successfully")
        print(f"   - Database: {Config.DB_CONFIG['database']}")
        print(f"   - LLM API: {Config.LLM_API_URL}")
        if Config.LLM_API_KEY:
            print(f"   - API Key: Configured")
        if Config.LLM_MODEL:
            print(f"   - Model: {Config.LLM_MODEL}")
        
        # Set table context if configured
        if Config.DEFAULT_SCHEMA and Config.DEFAULT_TABLE:
            context = agent.set_table_context(Config.DEFAULT_SCHEMA, Config.DEFAULT_TABLE)
            print(f"Table context set: {Config.DEFAULT_SCHEMA}.{Config.DEFAULT_TABLE}")
            print(f"   - Columns: {len(context['structure']['columns'])} columns found")
        
        return True
        
    except FileNotFoundError:
        print("ERROR: config.yaml not found!")
        print("   Please create config.yaml from config.yaml.example")
        return False
    except Exception as e:
        print(f"ERROR: Could not initialize agent: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print("=" * 60)
    print("Conversational Database Analytics Agent")
    print("=" * 60)
    
    # Initialize agent from config file
    if not initialize_agent():
        print("\nFailed to initialize. Please check your config.yaml file.")
        print("   See config.yaml.example for reference.")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print(f"Starting server on http://{Config.HOST}:{Config.PORT}")
    print("=" * 60 + "\n")
    
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG
    )

