# Quick Start Guide

Get started with the Conversational Database Analytics Agent in 5 minutes!

## Prerequisites

- **Python 3.10+** (3.11 or 3.12 recommended for best performance)
- PostgreSQL database
- LLM API endpoint (OpenAI-compatible or custom)

## Installation

### Step 1: Install Dependencies

```bash
# Install Python dependencies
pip install -r requirements.txt
```

### Step 2: Configure the Application

Create your configuration file:

```bash
# Copy the example config
cp config.yaml.example config.yaml

# Edit config.yaml with your settings
nano config.yaml  # or use your favorite editor
```

Update `config.yaml` with your values:

```yaml
# LLM API Configuration
llm_api_url: "http://your-llm-api-endpoint.com/generate"

# Database Configuration
database:
  host: localhost
  port: 5432
  name: your_database_name
  user: your_database_user
  password: your_database_password

# Table Context
table_context:
  schema: public
  table: your_table_name
```

### Step 3: Start the Server

```bash
python app.py
```

You should see:

```
============================================================
Conversational Database Analytics Agent
============================================================
✅ Agent initialized successfully
   - Database: your_database_name
   - LLM API: http://your-llm-api.com/generate
✅ Table context set: public.your_table_name
   - Columns: 10 columns found

============================================================
🚀 Starting server on http://0.0.0.0:5000
============================================================
```

### Step 4: Open Browser

Navigate to `http://localhost:5000`

### Step 5: Start Querying!

Try these example queries:
- "Show me the top 10 records"
- "What is the total count?"
- "Give me the average of [numeric_column]"
- "Show me records from last month"

## Option 2: Programmatic Usage

```python
from agent import ConversationalAgent

# Initialize agent
agent = ConversationalAgent(
    llm_api_url="http://your-llm-api.com/generate",
    db_config={
        "host": "localhost",
        "port": 5432,
        "database": "your_db",
        "user": "your_user",
        "password": "your_password"
    }
)

# Set table context
agent.set_table_context("public", "users")

# Query
result = agent.process_query("Show me the top 10 users by age")

if result["success"]:
    print(f"SQL: {result['sql']}")
    print(f"Data: {result['data']}")

# Close
agent.close()
```

## Option 3: Interactive CLI

```bash
python example_usage.py --interactive
```

Follow the prompts to configure and start querying.

## Environment Variables (Optional)

Create a `.env` file:

```bash
# LLM API
LLM_API_URL=http://your-llm-api.com/generate

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=your_database
DB_USER=your_user
DB_PASSWORD=your_password

# Server
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=False
```

Then simply run:

```bash
python app.py
```

The agent will auto-configure from environment variables.

## LLM API Requirements

Your LLM API should accept:

```json
{
  "prompt": "Your prompt here",
  "temperature": 0.1,
  "max_tokens": 500
}
```

And return (any of these formats work):

```json
{
  "sql": "SELECT * FROM table..."
}
```

or

```json
{
  "response": "SELECT * FROM table..."
}
```

or OpenAI format:

```json
{
  "choices": [
    {
      "text": "SELECT * FROM table..."
    }
  ]
}
```

## Troubleshooting

### "Agent not configured" error
- Make sure you clicked **Connect** after entering credentials
- Check that database is accessible from your machine

### "Failed to generate SQL" error
- Verify LLM API URL is correct and accessible
- Check API response format matches expectations
- Try testing API separately with curl/Postman

### "Database error" in results
- The LLM might have generated invalid SQL
- Check table structure matches expectations
- The agent will attempt to auto-fix and retry

### Charts not showing
- Charts only appear for numeric data
- Ensure your query returns numeric columns
- Check browser console for errors

## Next Steps

- Read the full [README.md](README.md) for detailed documentation
- Check [example_usage.py](example_usage.py) for more code examples
- Customize prompts in `agent.py` for better SQL generation
- Add authentication for production use

## Architecture Overview

```
User Query → Web UI → Flask API → Agent Orchestrator
                                        ↓
                                   LLM Tool (Text-to-SQL)
                                        ↓
                                   Database Tool (Execute)
                                        ↓
                                   Results + Visualization
```

## Key Features

✅ Natural language to SQL conversion  
✅ Automatic error recovery  
✅ Interactive visualizations  
✅ Multi-table support  
✅ Conversation history  
✅ Read-only security  

## Support

For detailed documentation, see [README.md](README.md)

---

**Happy Querying! 🚀**

