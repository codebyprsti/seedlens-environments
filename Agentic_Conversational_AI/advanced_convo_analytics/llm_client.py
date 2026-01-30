import openai
import re
import logging
from config import LLM_CONFIG

# Set up logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Set to INFO or WARNING in production


class LLMClient:
  def __init__(self):
    try:
      self.client = openai.OpenAI(
        base_url=LLM_CONFIG['base_url'],
        api_key=LLM_CONFIG['api_key']
      )
      logger.info("LLMClient initialized successfully.")
    except Exception as e:
      logger.exception("Failed to initialize LLMClient: %s", str(e))
      raise

  def prompt_to_sql(self, prompt: str):
    """
    Sends a prompt to the LLM and returns the full response object.
    """
    try:
      logger.info("Sending prompt to LLM...")
      logger.debug(f"Prompt: {prompt}")
      response = self.client.chat.completions.create(
        model="Meta-Llama-3.3-70B-Instruct",
        messages=[
          {"role": "system", "content": "You are a helpful PostgreSQL SQL generation assistant"},
          {"role": "user", "content": prompt}
        ],
        temperature=0.1,
        top_p=0.1
      )
      logger.info("Received response from LLM.")
      return response
    except Exception as e:
      logger.exception("Failed to get SQL from LLM: %s", str(e))
      raise

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
