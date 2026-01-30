# Configuration Changes - File-Based Configuration

## Overview

The application has been refactored to use **file-based configuration** (`config.yaml`) instead of UI-based configuration. All settings including LLM API, database credentials, and table context are now loaded from a configuration file at startup.

## What Changed

### ✅ Before (UI-Based Configuration)
- Users had to manually enter configuration in the web UI
- LLM API URL, database credentials, schema/table selection via forms
- Configuration lost on server restart
- Not suitable for production deployments

### ✅ After (File-Based Configuration)
- All configuration in `config.yaml` file
- Auto-loaded at startup
- Persistent across restarts
- Production-ready
- Can be version-controlled (excluding passwords)
- Environment variable overrides supported

## Files Modified

### 1. **config.py** - Complete Rewrite
**Changes:**
- Added YAML file loading support
- Implemented property-based configuration accessors
- Added `DEFAULT_SCHEMA` and `DEFAULT_TABLE` settings
- Environment variable override support
- Graceful fallback handling

**New Features:**
```python
Config.DEFAULT_SCHEMA  # Table schema
Config.DEFAULT_TABLE   # Table name
Config.load_config()   # Load from YAML file
```

### 2. **app.py** - Initialization Changes
**Changes:**
- Removed `POST /api/config` endpoint (was for UI configuration)
- Changed to `GET /api/config` (read-only status)
- Removed `POST /api/table/context` endpoint
- Changed to `GET /api/table/context` (read-only)
- Added `initialize_agent()` function with comprehensive error handling
- Auto-initialization at startup
- Beautiful startup output with status indicators

**New Behavior:**
```bash
$ python app.py
============================================================
Conversational Database Analytics Agent
============================================================
✅ Agent initialized successfully
   - Database: your_database
   - LLM API: http://your-api.com/generate
✅ Table context set: public.users
   - Columns: 10 columns found

============================================================
🚀 Starting server on http://0.0.0.0:5000
============================================================
```

### 3. **static/index.html** - UI Simplification
**Changes:**
- **Removed:** All configuration input forms
  - LLM API URL input
  - Database credential inputs
  - Schema/table selection dropdowns
  - "Connect" and "Set Table" buttons
  
- **Added:** Read-only configuration status display
  - LLM API status indicator
  - Database connection status
  - Current schema and table
  - Available schemas list (informational)
  - Note indicating config is from `config.yaml`

**New UI Features:**
```
📋 Configuration Status
  LLM API: ✅ Configured
  Database: ✅ Connected
  Schema: public
  Table: users
  
  ℹ️ Configuration is loaded from config.yaml
  
🗂️ Available Schemas
  📁 public
  📁 analytics
  📁 reporting
```

### 4. **requirements.txt** - New Dependency
**Added:**
```
PyYAML==6.0.1  # For YAML configuration file parsing
```

### 5. **Documentation Updates**
**Updated Files:**
- `README.md` - Complete usage section rewrite
- `QUICKSTART.md` - New configuration steps
- `.env.example` - Added notes about YAML being primary

**New Documentation:**
- `config.yaml.example` - Template configuration file
- `config.yaml` - Default configuration (needs user values)
- `CONFIGURATION_CHANGES.md` - This file

## Configuration File Format

### config.yaml
```yaml
# Flask Server Configuration
flask_host: 0.0.0.0
flask_port: 5000
flask_debug: false

# LLM API Configuration
llm_api_url: "http://your-llm-api-endpoint.com/generate"

# Database Configuration
database:
  host: localhost
  port: 5432
  name: your_database_name
  user: your_database_user
  password: your_database_password

# Table Context - The default schema and table to query
table_context:
  schema: public
  table: your_table_name

# Agent Configuration (Optional)
sql_temperature: 0.1
max_tokens: 500
max_result_rows: 1000
```

## Migration Guide

### For New Deployments

1. Copy the example config:
```bash
cp config.yaml.example config.yaml
```

2. Edit `config.yaml` with your values

3. Start the server:
```bash
python app.py
```

### For Existing Deployments

If you were using environment variables (`.env`), you have two options:

**Option 1: Create config.yaml (Recommended)**
```bash
# Create config.yaml with your settings
cat > config.yaml << EOF
llm_api_url: "http://your-api.com/generate"
database:
  host: localhost
  port: 5432
  name: your_db
  user: your_user
  password: your_password
table_context:
  schema: public
  table: your_table
EOF
```

**Option 2: Keep using environment variables**
Environment variables still work as overrides:
```bash
export LLM_API_URL="http://your-api.com"
export DB_NAME="your_database"
export DB_USER="your_user"
export DB_PASSWORD="your_password"
export DEFAULT_SCHEMA="public"
export DEFAULT_TABLE="your_table"
```

## Benefits of File-Based Configuration

### ✅ Production Ready
- Configuration separate from code
- Easy deployment with different configs
- No manual UI setup needed

### ✅ Version Control
- Track configuration changes
- Use `.gitignore` for sensitive data
- Different configs for dev/staging/prod

### ✅ Automation Friendly
- Works with Docker, Kubernetes
- CI/CD pipeline compatible
- No UI interaction required

### ✅ Security
- Passwords in file, not entered in browser
- Can use file permissions to protect config
- Environment variable overrides for secrets

### ✅ Reliability
- Configuration validated at startup
- Fails fast with clear error messages
- No runtime configuration errors

## API Changes

### Removed Endpoints
❌ `POST /api/config` - Was used for UI configuration  
❌ `POST /api/table/context` - Was used to set table

### Modified Endpoints
✅ `GET /api/config` - Now returns read-only configuration status  
✅ `GET /api/table/context` - Now returns current context from config

### Unchanged Endpoints
- `GET /api/schemas` - Still lists available schemas
- `GET /api/tables/<schema>` - Still lists tables
- `POST /api/query` - Still processes queries
- `GET /api/history` - Still returns history
- `DELETE /api/history` - Still clears history
- `GET /health` - Still provides health status

## Environment Variables Priority

Configuration is loaded in this order (later overrides earlier):

1. **Default values** (hardcoded in config.py)
2. **config.yaml file** (primary configuration source)
3. **Environment variables** (highest priority overrides)

Example:
```yaml
# config.yaml
flask_port: 5000
```

```bash
# Override with environment variable
export FLASK_PORT=8080  # This wins!
```

## Error Handling

The application now provides comprehensive startup validation:

### ❌ Missing config.yaml
```
❌ ERROR: config.yaml not found!
   Please create config.yaml from config.yaml.example
```

### ❌ Missing LLM API URL
```
❌ ERROR: LLM_API_URL not configured in config.yaml
```

### ❌ Missing Database Config
```
❌ ERROR: Database configuration not found in config.yaml
```

### ⚠️ Missing Table Config
```
⚠️  WARNING: DEFAULT_TABLE not configured. You'll need to specify it later.
```

## Testing

Test your configuration:

```bash
# 1. Start the server
python app.py

# 2. Check the output for ✅ marks
# 3. Open http://localhost:5000
# 4. Verify configuration status in sidebar
# 5. Try a test query
```

## Rollback

If you need to revert to UI-based configuration (not recommended):

```bash
git checkout <previous-commit>
```

Or keep both versions in different branches:
```bash
git branch config-ui-based
git branch config-file-based
```

## Support

### Configuration Issues
- Check `config.yaml` syntax (YAML is whitespace-sensitive)
- Validate with: `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`
- Check file permissions: `ls -l config.yaml`

### Database Connection Issues
- Test connection: `psql -h localhost -U user -d database`
- Check PostgreSQL is running: `pg_isready`
- Verify credentials in `config.yaml`

### LLM API Issues
- Test API endpoint: `curl -X POST <llm_api_url>`
- Check network connectivity
- Verify API response format

## Future Enhancements

Potential improvements for the configuration system:

- [ ] Support for multiple config file formats (JSON, TOML)
- [ ] Configuration validation schema
- [ ] Hot-reload configuration without restart
- [ ] Web UI for read-only config viewing
- [ ] Encrypted password storage
- [ ] Configuration templates per database type

## Summary

This refactoring transforms the application from a manually-configured development tool into a production-ready service with:

✅ File-based configuration  
✅ Auto-initialization  
✅ Clear error messages  
✅ Environment variable overrides  
✅ No UI configuration required  
✅ Version control friendly  
✅ CI/CD compatible  

The application is now enterprise-ready and follows infrastructure-as-code best practices.

