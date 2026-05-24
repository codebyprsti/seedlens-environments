"""
Load agentic config.yaml once at startup. Kept separate from main project DB settings.
Used by the orchestrator; do not reload on every request.
"""
import os
import logging
import yaml
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Module-level single load; do not mutate after startup
_agentic_config: Optional[dict[str, Any]] = None
_config_path_used: Optional[str] = None


def get_config_path() -> str:
    """Resolve config file path (env override or default under this folder)."""
    path = os.environ.get("AGENTIC_CONFIG_PATH", "").strip()
    if path and Path(path).exists():
        return path
    base = Path(__file__).resolve().parent
    default = base / "config.yaml"
    return str(default)


def load_agentic_config(config_path: Optional[str] = None) -> dict[str, Any]:
    """
    Load config.yaml once. Subsequent calls return the same cached dict.
    Use config_path only on first call (e.g. at startup).
    """
    global _agentic_config, _config_path_used
    if _agentic_config is not None:
        logger.debug("Agentic config already loaded, skipping reload.")
        return _agentic_config
    path = config_path or get_config_path()
    if not Path(path).exists():
        logger.warning("Agentic config file not found: %s", path)
        _agentic_config = {}
        _config_path_used = path
        return _agentic_config
    try:
        with open(path, "r", encoding="utf-8") as f:
            _agentic_config = yaml.safe_load(f) or {}
        _config_path_used = path
        logger.info("Agentic config loaded once from %s", path)
        return _agentic_config
    except Exception as e:
        logger.exception("Failed to load agentic config from %s: %s", path, e)
        _agentic_config = {}
        _config_path_used = path
        return _agentic_config


def get_agentic_config() -> dict[str, Any]:
    """Return cached config; load from default path if not yet loaded."""
    if _agentic_config is None:
        load_agentic_config()
    return _agentic_config or {}


def get_db_config() -> Optional[dict[str, str]]:
    """Agentic DB config only (not main project DB)."""
    cfg = get_agentic_config()
    db = cfg.get("database")
    if not db:
        return None
    return {
        "host": str(db.get("host", "localhost")),
        "port": int(db.get("port", 5432)),
        "database": str(db.get("name", "")),
        "user": str(db.get("user", "")),
        "password": str(db.get("password", "")),
    }


def get_llm_config() -> dict[str, Any]:
    """LLM URL, key, model from agentic config."""
    cfg = get_agentic_config()
    return {
        "llm_api_url": (cfg.get("llm_api_url") or "").strip(),
        "llm_api_key": (cfg.get("llm_api_key") or "").strip(),
        "llm_model": (cfg.get("llm_model") or "").strip(),
    }


def get_table_context() -> tuple[str, str]:
    """(schema, table) from agentic config."""
    cfg = get_agentic_config()
    ctx = cfg.get("table_context") or {}
    schema = str(ctx.get("schema") or "public")
    table = str(ctx.get("table") or "")
    logger.debug(f"get_table_context: schema={schema}, table={table} from config")
    logger.debug(f"Config keys: {list(cfg.keys())}, table_context keys: {list(ctx.keys()) if ctx else 'None'}")
    return (schema, table)


def get_prompts() -> dict[str, str]:
    """Few-shot and sql_rules from agentic config (for caching)."""
    cfg = get_agentic_config()
    return {
        "few_shot_prompt": (cfg.get("few_shot_prompt") or "").strip(),
        "sql_rules": (cfg.get("sql_rules") or "").strip(),
    }


def get_config_path_used() -> Optional[str]:
    """Path from which config was loaded (for debug)."""
    return _config_path_used
