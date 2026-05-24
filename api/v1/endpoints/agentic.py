"""
Agentic Conversational AI API. Exposed via main project routing.
Uses orchestrator (config loaded once, separate from main DB).
"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Project root so we can import Agentic_Conversational_AI
_root = Path(__file__).resolve().parents[3]
if str(_root) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_root))

router = APIRouter(tags=["Agentic AI"])


def _orchestrator():
    from Agentic_Conversational_AI.orchestrator import (
        initialize,
        get_agent,
        get_config_status,
        invoke_advanced_analytics,
        get_conversation_history,
        clear_conversation_history,
    )
    initialize()
    return {
        "get_agent": get_agent,
        "get_config_status": get_config_status,
        "invoke_advanced_analytics": invoke_advanced_analytics,
        "get_conversation_history": get_conversation_history,
        "clear_conversation_history": clear_conversation_history,
    }


@router.get("/config")
def agentic_config():
    """Get agentic config status (LLM, DB, schema, table). Loaded once at startup."""
    try:
        o = _orchestrator()
        status = o["get_config_status"]()
        logger.debug("Agentic config status: %s", status)
        # If not initialized, try to get initialization error details
        if not status.get("agentic_initialized"):
            logger.warning("Agentic not initialized. Check server logs for initialization errors.")
            status["initialization_error"] = "Check server logs for details. Common issues: missing dependencies, DB connection failure, or LLM API error."
        return status
    except Exception as e:
        logger.exception("Agentic config error: %s", e)
        return {
            "configured": False,
            "error": str(e),
            "llm_configured": False,
            "db_configured": False,
            "schema": None,
            "table": None,
            "agentic_initialized": False,
            "message": f"Agentic config error: {str(e)}",
        }


@router.get("/schemas")
def list_schemas():
    """List database schemas (agentic DB)."""
    o = _orchestrator()
    agent = o["get_agent"]()
    if not agent:
        raise HTTPException(status_code=400, detail="Agent not configured. Check agentic config.")
    try:
        schemas = agent.db_tool.list_schemas()
        return {"success": True, "schemas": schemas}
    except Exception as e:
        logger.exception("List schemas error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{schema_name}")
def list_tables(schema_name: str):
    """List tables in a schema (agentic DB)."""
    o = _orchestrator()
    agent = o["get_agent"]()
    if not agent:
        raise HTTPException(status_code=400, detail="Agent not configured.")
    try:
        tables = agent.db_tool.list_tables(schema_name)
        return {"success": True, "schema": schema_name, "tables": tables}
    except Exception as e:
        logger.exception("List tables error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/table/context")
def get_table_context():
    """Current table context from agentic config."""
    o = _orchestrator()
    agent = o["get_agent"]()
    if not agent:
        raise HTTPException(status_code=400, detail="Agent not configured.")
    
    # Get expected values from config
    from Agentic_Conversational_AI.config_loader import get_table_context as get_config_table_context
    expected_schema, expected_table = get_config_table_context()
    
    # Check if current context matches config
    current_context = getattr(agent, "current_table_context", None)
    if not current_context:
        # Try to set it from config
        if expected_schema and expected_table:
            try:
                agent.set_table_context(expected_schema, expected_table)
                current_context = agent.current_table_context
                logger.info(f"Refreshed table context from config: {expected_schema}.{expected_table}")
            except Exception as e:
                logger.error(f"Failed to set table context from config: {e}")
                raise HTTPException(
                    status_code=400, 
                    detail=f"No table context set. Failed to load {expected_schema}.{expected_table}: {str(e)}"
                )
        else:
            raise HTTPException(status_code=400, detail="No table context set in config.")
    
    # Log mismatch if any
    if current_context:
        current_schema = current_context.get("schema", "")
        current_table = current_context.get("table", "")
        if current_schema != expected_schema or current_table != expected_table:
            logger.warning(
                f"Table context mismatch: current={current_schema}.{current_table}, "
                f"expected={expected_schema}.{expected_table}"
            )
    
    return {
        "success": True, 
        "context": current_context,
        "config_schema": expected_schema,
        "config_table": expected_table
    }


class QueryBody(BaseModel):
    query: str


@router.post("/query")
def process_query(body: QueryBody):
    """Process natural language query (agentic)."""
    if not body.query or not body.query.strip():
        raise HTTPException(status_code=400, detail="Query is required")
    o = _orchestrator()
    agent = o["get_agent"]()
    if not agent:
        raise HTTPException(status_code=400, detail="Agent not configured.")
    try:
        result = agent.process_query(body.query.strip())
        if result.get("success"):
            return result
        raise HTTPException(status_code=400, detail=result.get("error", "Query failed"))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Query error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
def get_history():
    """Get conversation history (rolling memory)."""
    o = _orchestrator()
    history = o["get_conversation_history"]()
    return {"success": True, "history": history}


@router.delete("/history")
def clear_history():
    """Clear conversation history."""
    o = _orchestrator()
    o["clear_conversation_history"]()
    return {"success": True, "message": "History cleared"}


class AdvancedQueryBody(BaseModel):
    query: str
    schema: str | None = None


@router.post("/query/advanced")
def process_advanced_query(body: AdvancedQueryBody):
    """Process query with formatted data (dimensions/measures)."""
    if not body.query or not body.query.strip():
        raise HTTPException(status_code=400, detail="Query is required")
    o = _orchestrator()
    agent = o["get_agent"]()
    if not agent:
        raise HTTPException(status_code=400, detail="Agent not configured.")
    
    # Update table context if schema provided (refresh table structure)
    if body.schema:
        # Get current table or use default from config
        current_context = getattr(agent, "current_table_context", None)
        table = current_context.get("table") if current_context else None
        
        # If no table in context, get from config
        if not table:
            from Agentic_Conversational_AI.config_loader import get_table_context
            _, table = get_table_context()
        
        if table:
            logger.info(f"Refreshing table context: schema={body.schema}, table={table}")
            try:
                agent.set_table_context(body.schema, table)
                logger.info(f"Table context updated successfully")
            except Exception as e:
                logger.error(f"Failed to refresh table context: {e}")
                raise HTTPException(status_code=400, detail=f"Failed to load table structure for {body.schema}.{table}: {str(e)}")
        else:
            raise HTTPException(status_code=400, detail="Table name not found in context or config")
    
    try:
        result = agent.process_query(body.query.strip())
        if result.get("success"):
            return {
                "success": True,
                "query": result.get("query"),
                "sql": result.get("sql"),
                "data": result.get("data"),
                "formatted_data": result.get("formatted_data", {}),
                "columns": result.get("columns", []),
                "row_count": result.get("row_count", 0),
                "explanation": result.get("explanation", ""),
            }
        raise HTTPException(status_code=400, detail=result.get("error", "Query failed"))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Advanced query error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


class AnalyticsQueryBody(BaseModel):
    user_query: str | None = None
    query: str | None = None


@router.post("/analytics/query")
def analytics_query(body: AnalyticsQueryBody):
    """Advanced analytics (lambda-style) with agentic config."""
    user_query = (body.user_query or body.query or "").strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="user_query is required")
    o = _orchestrator()
    result = o["invoke_advanced_analytics"](user_query)
    status = result.get("statusCode", 500)
    try:
        body_data = json.loads(result.get("body", "{}"))
    except Exception:
        body_data = {"error": result.get("body", "Unknown error")}
    if status >= 400:
        raise HTTPException(status_code=status, detail=body_data.get("error", body_data))
    return body_data


@router.get("/health")
def agentic_health():
    """Agentic service health."""
    o = _orchestrator()
    status = o["get_config_status"]()
    return {
        "status": "healthy",
        "agent_configured": status.get("configured", False),
        "agentic_initialized": status.get("agentic_initialized", False),
    }


@router.get("/app")
def serve_agentic_app():
    """Serve the agentic conversational AI UI (index.html)."""
    index_path = _root / "Agentic_Conversational_AI" / "static" / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Agentic app not found")
    return FileResponse(index_path, media_type="text/html")
