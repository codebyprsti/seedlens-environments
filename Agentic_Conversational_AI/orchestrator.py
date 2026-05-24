"""
Main orchestrator: loads agentic config once, holds agent and memory, exposes services.
Config is separate from main project DB. Lambda logic is wrapped and invoked with agentic config.
"""
import logging
import os
import sys
from pathlib import Path

# Add Agentic_Conversational_AI to path when run from project root
_agentic_root = Path(__file__).resolve().parent
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))

from config_loader import (
    load_agentic_config,
    get_agentic_config,
    get_db_config,
    get_llm_config,
    get_table_context,
    get_prompts,
    get_config_path_used,
)
from memory import RollingMemory

logger = logging.getLogger(__name__)

# Singleton agent and state
_agent = None
_memory: RollingMemory | None = None
_initialized = False
_CACHED_SYSTEM_PROMPT = ""


def _ensure_agentic_path():
    """Ensure advanced_convo_analytics is importable."""
    adv = _agentic_root / "advanced_convo_analytics"
    if adv.exists() and str(adv) not in sys.path:
        sys.path.insert(0, str(adv))


def get_cached_system_prompt() -> str:
    """Cache static system prompt at startup to avoid token explosion."""
    global _CACHED_SYSTEM_PROMPT
    if _CACHED_SYSTEM_PROMPT:
        return _CACHED_SYSTEM_PROMPT
    prompts = get_prompts()
    few = (prompts.get("few_shot_prompt") or "").strip()
    rules = (prompts.get("sql_rules") or "").strip()
    _CACHED_SYSTEM_PROMPT = (few + "\n\nRules: " + rules) if (few or rules) else ""
    logger.debug("Cached system prompt length: %d chars", len(_CACHED_SYSTEM_PROMPT))
    return _CACHED_SYSTEM_PROMPT


def initialize(config_path: str = None) -> bool:
    """
    Load config once and create agent. Uses agentic config only (not main project DB).
    """
    global _agent, _memory, _initialized
    if _initialized:
        logger.debug("Orchestrator already initialized.")
        return True
    _ensure_agentic_path()
    load_agentic_config(config_path)
    cfg_path = get_config_path_used()
    logger.info("Agentic config loaded from %s", cfg_path)
    db_config = get_db_config()
    llm = get_llm_config()
    schema, table = get_table_context()
    logger.info("Agentic config loaded: schema=%s, table=%s", schema, table)
    if not db_config:
        logger.warning("Agentic DB config missing; agentic endpoints may fail.")
    if not llm.get("llm_api_url"):
        logger.warning("Agentic LLM API URL missing; agentic endpoints may fail.")
    logger.info("Agentic DB mapping: host=%s db=%s", (db_config or {}).get("host"), (db_config or {}).get("database"))
    try:
        from agent import ConversationalAgent
        _agent = ConversationalAgent(
            llm.get("llm_api_url", ""),
            db_config or {},
            api_key=llm.get("llm_api_key") or None,
            model=llm.get("llm_model") or None,
            few_shot_prompt=get_prompts().get("few_shot_prompt") or None,
            rules=get_prompts().get("sql_rules") or None,
        )
        if schema and table:
            try:
                _agent.set_table_context(schema, table)
                logger.info("Agentic table context set: %s.%s", schema, table)
            except Exception as e:
                logger.error("Failed to set table context %s.%s: %s", schema, table, e)
                # Don't fail initialization, but log the error
        _memory = RollingMemory(max_messages=10, summarize_after=20)
        _initialized = True
        get_cached_system_prompt()
        logger.info("Orchestrator initialized; agentic routes ready.")
        return True
    except Exception as e:
        logger.exception("Orchestrator init failed: %s", e)
        return False


def get_agent():
    """Return the ConversationalAgent (initializes if needed)."""
    global _agent
    if _agent is None and not _initialized:
        initialize()
    return _agent


def get_memory() -> RollingMemory | None:
    if _memory is None and not _initialized:
        initialize()
    return _memory


def get_config_status() -> dict:
    """Config status for /api/config: LLM, DB, schema, table, agentic status."""
    if not _initialized:
        initialize()
    db = get_db_config()
    llm = get_llm_config()
    schema, table = get_table_context()
    agent = get_agent()
    return {
        "configured": agent is not None and bool(llm.get("llm_api_url")) and bool(db),
        "schema": schema,
        "table": table,
        "llm_configured": bool(llm.get("llm_api_url")),
        "db_configured": bool(db),
        "agentic_initialized": _initialized,
        "config_path": get_config_path_used(),
        "message": "Configuration loaded from agentic config (config.yaml).",
    }


def invoke_advanced_analytics(user_query: str) -> dict:
    """Run lambda-style advanced analytics with agentic config. Uses dynamic schema/table from config."""
    if not _initialized:
        initialize()
    db = get_db_config()
    llm = get_llm_config()
    schema, table = get_table_context()
    prompts = get_prompts()
    if not db:
        return {"statusCode": 500, "body": '{"error": "Agentic database not configured."}'}
    if not schema or not table:
        logger.warning("Schema or table not set in agentic config. Using defaults.")
        schema = schema or "operations"
        table = table or ""
    logger.info("Advanced analytics: schema=%s, table=%s", schema, table)
    _ensure_agentic_path()
    try:
        from advanced_convo_analytics.service import run_advanced_analytics
    except ImportError:
        try:
            from service import run_advanced_analytics
        except ImportError:
            logger.exception("Advanced analytics service not found.")
            return {"statusCode": 500, "body": '{"error": "Advanced analytics service unavailable."}'}
    result = run_advanced_analytics(
        user_query=user_query,
        db_config=db,
        schema_name=schema,
        table_name=table,  # Pass table_name for dynamic prompt building
        few_shot_prompt=prompts.get("few_shot_prompt", ""),
        rules=prompts.get("sql_rules", ""),
        schema_text="",
        llm_base_url=llm.get("llm_api_url", ""),
        llm_api_key=llm.get("llm_api_key", ""),
        llm_model=llm.get("llm_model", ""),
    )
    logger.debug("Advanced analytics result statusCode=%s for %s.%s", result.get("statusCode"), schema, table)
    return result


def get_conversation_history():
    """Last N messages (rolling memory). Returns agent's conversation history."""
    agent = get_agent()
    if agent and hasattr(agent, "conversation_history"):
        return agent.get_conversation_history()
    # Fallback to memory if agent doesn't have history
    mem = get_memory()
    return mem.get_full_history() if mem else []


def clear_conversation_history():
    mem = get_memory()
    if mem:
        mem.clear()
    agent = get_agent()
    if agent and hasattr(agent, "conversation_history"):
        agent.clear_conversation_history()
