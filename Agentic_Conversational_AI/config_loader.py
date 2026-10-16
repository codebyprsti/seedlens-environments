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


HF_ROUTER_URL = "https://router.huggingface.co/v1"
HF_DEFAULT_MODEL = "Qwen/Qwen2.5-72B-Instruct"


def _is_hf_token(key: str) -> bool:
    return bool(key) and (key.startswith("hf_") or key.startswith("hf-"))


def get_llm_config() -> dict[str, Any]:
    """LLM URL, key, model. Hugging Face is the default; SambaNova only if LLM_PROVIDER=sambanova."""
    cfg = get_agentic_config()
    provider = (
        os.environ.get("LLM_PROVIDER")
        or cfg.get("llm_provider")
        or "huggingface"
    ).strip().lower()
    yaml_url = (os.environ.get("LLM_BASE_URL") or cfg.get("llm_api_url") or "").strip()
    yaml_model = (os.environ.get("LLM_MODEL") or cfg.get("llm_model") or "").strip()
    hf_key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_API_KEY")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or ""
    ).strip()
    yaml_key = (cfg.get("llm_api_key") or "").strip()
    use_hf = provider not in ("sambanova", "samba")
    if use_hf:
        key = hf_key
        if not key and _is_hf_token(yaml_key):
            key = yaml_key
        model = yaml_model
        if not model or "Meta-Llama-3.3" in model or "sambanova" in model.lower():
            model = HF_DEFAULT_MODEL
        return {
            "llm_api_url": yaml_url if "huggingface" in yaml_url.lower() else HF_ROUTER_URL,
            "llm_api_key": key,
            "llm_model": model,
        }
    return {
        "llm_api_url": yaml_url or "https://api.sambanova.ai/v1",
        "llm_api_key": os.environ.get("SAMBANOVA_API_KEY", "").strip() or yaml_key,
        "llm_model": yaml_model or "Meta-Llama-3.3-70B-Instruct",
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
