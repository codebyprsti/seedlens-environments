"""
Rolling conversation memory: keep last N messages, optional summarization.
Avoids sending full prompt history on every request (reduces tokens and rate-limit risk).
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default: send only last N turns to LLM
DEFAULT_ROLLING_WINDOW = 10


class RollingMemory:
    """
    Keeps last N conversation turns. Optionally holds a short summary of older history.
    """

    def __init__(self, max_messages: int = DEFAULT_ROLLING_WINDOW, summarize_after: int = 20):
        self.max_messages = max(max_messages, 1)
        self.summarize_after = summarize_after
        self._history: list[dict[str, Any]] = []
        self._summary: str = ""

    def add(self, role: str, content: Any, meta: dict[str, Any] | None = None) -> None:
        """Append one message (e.g. user query + sql + row_count)."""
        entry = {"role": role, "content": content, "meta": meta or {}}
        self._history.append(entry)
        if len(self._history) > self.summarize_after and not self._summary:
            self._set_summary_from_recent()

    def _set_summary_from_recent(self) -> None:
        """Simple summarization: keep a one-line summary of older turns."""
        older = self._history[:-self.max_messages]
        if not older:
            return
        parts = []
        for e in older[-5:]:  # last 5 of the "old" part
            role = e.get("role", "")
            c = e.get("content", "")
            if isinstance(c, str) and len(c) > 60:
                c = c[:60] + "..."
            parts.append(f"{role}: {c}")
        self._summary = "Earlier conversation: " + "; ".join(parts)
        logger.debug("RollingMemory: summarized %d older messages", len(older))

    def get_context_for_llm(self) -> list[dict[str, str]]:
        """
        Last N messages in chat format for LLM (user/assistant).
        Prepends summary of older history if present.
        """
        window = self._history[-self.max_messages:] if len(self._history) > self.max_messages else self._history
        messages = []
        if self._summary:
            messages.append({"role": "system", "content": self._summary})
        for e in window:
            role = e.get("role", "user")
            content = e.get("content", "")
            if role == "user" and isinstance(content, str):
                messages.append({"role": "user", "content": content})
            elif role == "assistant" or "sql" in (e.get("meta") or {}):
                sql = (e.get("meta") or {}).get("sql", content)
                if isinstance(sql, str):
                    messages.append({"role": "assistant", "content": f"SQL: {sql}"})
        return messages

    def get_full_history(self) -> list[dict[str, Any]]:
        """Return full history for API (e.g. /api/history)."""
        return list(self._history)

    def clear(self) -> None:
        """Clear history and summary."""
        self._history.clear()
        self._summary = ""
        logger.debug("RollingMemory: cleared")

    def __len__(self) -> int:
        return len(self._history)
