import openai
import re
import logging
import time
import random

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Retry config for 429/503
MAX_RETRIES = 5
INITIAL_BACKOFF_SEC = 1.0
MAX_BACKOFF_SEC = 60.0
SYSTEM_MSG = "You are a PostgreSQL SQL assistant. Return only valid SQL."


def _get_llm_config():
    try:
        from config import LLM_CONFIG
        return LLM_CONFIG
    except ImportError:
        return {}


class LLMClient:
  def __init__(self, max_tokens: int = 1024, base_url: str = None, api_key: str = None, model: str = None):
    cfg = _get_llm_config()
    base_url = base_url or cfg.get("base_url", "")
    api_key = api_key or cfg.get("api_key", "")
    self.model = model or cfg.get("model", "Meta-Llama-3.3-70B-Instruct")
    try:
      self.client = openai.OpenAI(base_url=base_url, api_key=api_key)
      self.max_tokens = max_tokens
      logger.info("LLMClient initialized successfully.")
    except Exception as e:
      logger.exception("Failed to initialize LLMClient: %s", str(e))
      raise

  @classmethod
  def from_config(cls, base_url: str, api_key: str = "", model: str = "", max_tokens: int = 1024):
    return cls(max_tokens=max_tokens, base_url=base_url, api_key=api_key, model=model)

  def prompt_to_sql(self, prompt: str):
    """Call LLM with exponential backoff on 429/503."""
    last_error = None
    for attempt in range(MAX_RETRIES):
      try:
        if attempt > 0:
          delay = min(
            INITIAL_BACKOFF_SEC * (2 ** attempt) + random.uniform(0, 1),
            MAX_BACKOFF_SEC
          )
          logger.info("Retrying request to /chat/completions in %s seconds", round(delay, 2))
          time.sleep(delay)
        logger.info("Sending prompt to LLM...")
        logger.debug("Prompt length: %d chars", len(prompt))
        response = self.client.chat.completions.create(
          model=self.model,
          messages=[
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": prompt}
          ],
          temperature=0.1,
          top_p=0.1,
          max_tokens=self.max_tokens,
        )
        logger.info("Received response from LLM.")
        return response
      except (openai.RateLimitError, openai.APIConnectionError) as e:
        last_error = e
        if attempt == MAX_RETRIES - 1:
          logger.exception("Failed after %d retries: %s", MAX_RETRIES, str(e))
          raise
        continue
      except Exception as e:
        logger.exception("Failed to get SQL from LLM: %s", str(e))
        raise
    raise last_error

  def get_ai_query(self, message_content: str):
    """
    Extracts SQL queries from LLM response message content.
    Returns a list of SQL queries found.
    """
    try:
      logger.info("Extracting SQL queries from LLM response...")
      queries = re.findall(r"```sql\n(.*?)\n```", message_content, re.IGNORECASE | re.DOTALL)

      if not queries:
        logger.warning("No formatted SQL block found, trying fallback search...")
        queries = re.findall(r"(select\s+.*?;)", message_content, re.IGNORECASE | re.DOTALL)

      if queries:
        logger.info(f"Found {len(queries)} SQL query(ies).")
        for idx, query in enumerate(queries, start=1):
          logger.debug(f"\nQuery {idx}:\n{query.strip()}")
      else:
        logger.warning("No SQL query could be extracted from the message content.")

      return [q.strip() for q in queries] if isinstance(queries, list) else [queries]

    except Exception as e:
      logger.exception("Error while extracting SQL from response: %s", str(e))
      return []
