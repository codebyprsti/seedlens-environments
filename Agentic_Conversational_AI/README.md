# Conversational Database Analytics Agent

An agentic AI solution for conversational analytics on PostgreSQL databases. This system uses an LLM to convert natural language queries into SQL and executes them on your database, presenting results with interactive visualizations.

**Requirements:** Python 3.10+ (Python 3.11 or 3.12 recommended)

## Architecture

```
┌──────────────┐
│   Web UI     │
│  (Frontend)  │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Flask API  │
│   (app.py)   │
└──────┬───────┘
       │
       ▼
┌──────────────────┐
│ Agent Orchestrator│
│    (agent.py)     │
└────┬─────────┬───┘
     │         │
     ▼         ▼
┌─────────┐ ┌──────────┐
│LLM Tool │ │ DB Tool  │
│(API)    │ │(SQL Exec)│
└─────────┘ └──────────┘
```

## Components

### 1. Agent Orchestrator (`agent.py`)
- Main orchestration logic that decides which tools to use
- Manages conversation history and context
- Handles error recovery and SQL fixing

### 2. Tools (`tools.py`)
- **LLMTool**: Connects to REST API for text-to-SQL generation
- **DatabaseTool**: Connects to PostgreSQL database for SQL execution and metadata retrieval

### 3. Web Server (`app.py`)
- Flask-based REST API
- Endpoints for configuration, schema/table selection, and query processing

### 4. Web UI (`static/index.html`)
- Modern, responsive interface
- Real-time chat interface
- Interactive charts using Chart.js
- Data table visualization

## Features

✅ **Agentic Design**: Intelligent tool selection and error recovery  
✅ **Database Agnostic**: Designed to work with PostgreSQL (easily adaptable)  
✅ **Natural Language to SQL**: LLM-powered query generation  
✅ **Interactive Visualizations**: Automatic chart generation from results  
✅ **Conversation History**: Maintains context across queries  
✅ **Error Handling**: Automatic SQL error detection and correction  
✅ **Security**: Read-only queries enforced  

## Installation

1. **Clone or navigate to the project directory**

```bash
cd /path/to/Agent
```

2. **Install Python dependencies**

```bash
pip install -r requirements.txt
```

3. **Configure the application**

Create your configuration file from the example:

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` with your settings:

```yaml
# LLM API Configuration
llm_api_url: "http://your-llm-api.com/generate"

# Database Configuration
database:
  host: localhost
  port: 5432
  name: your_database
  user: your_user
  password: your_password

# Table Context
table_context:
  schema: public
  table: your_table
```

## Usage

### Starting the Server

```bash
python app.py
```

You should see initialization output:

```
============================================================
Conversational Database Analytics Agent
============================================================
✅ Agent initialized successfully
   - Database: your_database
   - LLM API: http://your-llm-api.com/generate
✅ Table context set: public.your_table
   - Columns: 10 columns found

============================================================
🚀 Starting server on http://0.0.0.0:5000
============================================================
```

The server will start on `http://localhost:5000`

### Using the Web Interface

1. **Open your browser** and navigate to `http://localhost:5000`

2. **View Configuration Status**:
   - The sidebar shows your configuration loaded from `config.yaml`
   - LLM API status
   - Database connection status
   - Current schema and table

3. **Start Querying**:
   - Type natural language questions like:
     - "Show me the top 10 users by age"
     - "What is the average salary by department?"
     - "List all orders from last month"

4. **View Results**:
   - See the generated SQL
   - View interactive charts
   - Browse data tables

### API Endpoints

#### Get Configuration Status
```bash
GET /api/config

Response:
{
  "configured": true,
  "schema": "public",
  "table": "users",
  "llm_configured": true,
  "db_configured": true,
  "message": "Configuration loaded from config.yaml"
}
```

#### Get Table Context
```bash
GET /api/table/context

Response:
{
  "success": true,
  "context": {
    "schema": "public",
    "table": "users",
    "structure": {...}
  }
}
```

#### List Schemas
```bash
GET /api/schemas
```

#### List Tables
```bash
GET /api/tables/<schema>
```

#### Execute Query
```bash
POST /api/query
Content-Type: application/json

{
  "query": "Show me the top 10 users by age"
}
```

#### Get/Clear History
```bash
GET /api/history
DELETE /api/history
```

## LLM API Integration

Your LLM API should accept POST requests with the following format:

**Request:**
```json
{
  "prompt": "SQL generation prompt with table structure...",
  "conversation_history": [],
  "temperature": 0.1,
  "max_tokens": 500
}
```

**Response (flexible):**
```json
{
  "sql": "SELECT * FROM table...",
  "explanation": "Optional explanation"
}
```

The system supports various response formats including OpenAI-like responses.

## Database Support

Currently optimized for **PostgreSQL**. The system is designed to be database-agnostic and can be easily adapted for:
- MySQL
- Snowflake
- SQLite
- Other SQL databases

To adapt for another database:
1. Update `DatabaseTool` in `tools.py`
2. Change the connection library in `requirements.txt`
3. Adjust SQL queries for metadata retrieval

## Security Considerations

- ✅ Only SELECT queries are allowed (enforced at database tool level)
- ✅ SQL injection protection through parameterized queries for metadata
- ✅ Environment variables for sensitive credentials
- ⚠️ In production, add authentication/authorization
- ⚠️ Use HTTPS for production deployment
- ⚠️ Implement rate limiting for API endpoints

## Configuration

### Configuration File (config.yaml)

The application uses `config.yaml` as the primary configuration source:

```yaml
# Flask Server
flask_host: 0.0.0.0
flask_port: 5000
flask_debug: false

# LLM API
llm_api_url: "http://your-api.com/generate"

# Database
database:
  host: localhost
  port: 5432
  name: your_db
  user: your_user
  password: your_password

# Table Context
table_context:
  schema: public
  table: your_table
```

### Environment Variable Overrides

You can override config.yaml values with environment variables:

```bash
export LLM_API_URL="http://different-api.com"
export DEFAULT_SCHEMA="analytics"
export DEFAULT_TABLE="events"
```

### Multiple Environments

Create different config files:

```bash
# Development
config.yaml

# Production
config.prod.yaml

# Run with specific config
CONFIG_FILE=config.prod.yaml python app.py
```

## Customization

### Adding New Tools

Add new tools to `tools.py`:

```python
class CustomTool:
    def __init__(self, config):
        self.config = config
    
    def execute(self, params):
        # Tool logic here
        pass
```

Update the agent in `agent.py` to use the new tool.

### Modifying Prompts

Edit the `_create_sql_generation_prompt` method in `agent.py` to customize how prompts are sent to the LLM.

### Changing Visualization

Modify the chart rendering logic in `static/index.html` to add new chart types or customize appearance.

## Troubleshooting

### Startup Errors

**Error: config.yaml not found**
```bash
# Copy the example config
cp config.yaml.example config.yaml
# Edit with your settings
```

**Error: LLM_API_URL not configured**
- Edit `config.yaml` and add your LLM API endpoint
- Or set environment variable: `export LLM_API_URL="http://your-api.com"`

**Error: Database configuration not found**
- Verify `database` section in `config.yaml` is complete
- Check all required fields: name, user, password

### Connection Issues
- Verify database credentials in `config.yaml`
- Ensure PostgreSQL is running and accessible
- Check LLM API endpoint is reachable
- Test database connection: `psql -h localhost -U user -d database`

### SQL Generation Issues
- Check LLM API response format
- Adjust `_extract_sql` method in `tools.py` if needed
- Review temperature and max_tokens settings in `config.yaml`

### Chart Not Displaying
- Ensure data has numeric columns
- Check browser console for JavaScript errors
- Verify Chart.js is loading

## License

MIT License - feel free to use and modify for your needs.

## Contributing

This is a minimal implementation designed for demonstration purposes. Feel free to extend with:
- Multi-user support
- Session management
- Additional database connectors
- More sophisticated chart types
- Query optimization suggestions
- Export functionality

## Support

For issues or questions, please refer to the documentation in the code comments.

