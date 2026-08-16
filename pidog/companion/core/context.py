"""Conversation context management with history window trimming."""
import threading
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class ConversationContext:
    """
    Thread-safe conversation history and session context container.
    Maintains a FIFO sliding window of conversation turns for LLM/VLM context.
    """

    def __init__(self, max_history: int = 10, system_prompt: Optional[str] = None):
        """
        :param max_history: Maximum number of message dicts to keep in history.
        :param system_prompt: Optional default system prompt.
        """
        self.max_history = max(1, max_history)
        self.system_prompt = system_prompt
        self._history: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._metadata: Dict[str, Any] = {}

    def add_message(self, role: str, content: Any) -> None:
        """
        Add a message to the conversation history and trim to max_history.

        :param role: 'user', 'assistant', 'system'
        :param content: string or structured multimodal content
        """
        with self._lock:
            self._history.append({"role": role, "content": content})
            self._trim_history()

    def add_user_message(self, content: Any) -> None:
        """Convenience method to add a user message."""
        self.add_message("user", content)

    def add_assistant_message(self, content: Any) -> None:
        """Convenience method to add an assistant message."""
        self.add_message("assistant", content)

    def add_system_message(self, content: str) -> None:
        """Convenience method to add a system message."""
        self.add_message("system", content)

    def get_history(self) -> List[Dict[str, Any]]:
        """
        Get a copy of the conversation history list.
        """
        with self._lock:
            return [dict(msg) for msg in self._history]

    def _trim_history(self) -> None:
        """
        Trim history to keep within max_history items.
        Must be called while holding _lock.
        """
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history:]

    def clear(self) -> None:
        """Clear all conversation history and metadata."""
        with self._lock:
            self._history.clear()
            self._metadata.clear()

    def set_metadata(self, key: str, value: Any) -> None:
        """Store arbitrary session metadata (thread-safe)."""
        with self._lock:
            self._metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Retrieve session metadata (thread-safe)."""
        with self._lock:
            return self._metadata.get(key, default)

    def __len__(self) -> int:
        with self._lock:
            return len(self._history)
