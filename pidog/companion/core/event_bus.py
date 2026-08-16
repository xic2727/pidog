import threading
import logging
from collections import defaultdict
from typing import Callable, Any, Dict, List, Set, Optional

logger = logging.getLogger(__name__)


class EventBus:
    """Thread-safe publish-subscribe event bus."""

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: Dict[str, List[Callable[[Any], None]]] = defaultdict(list)
        self._all_subscribers: List[Callable[[str, Any], None]] = []

    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> Callable[[], None]:
        """
        Subscribe a handler callable to a specific topic.
        Returns an unsubscribe callable.
        """
        with self._lock:
            self._subscribers[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if topic in self._subscribers and handler in self._subscribers[topic]:
                    self._subscribers[topic].remove(handler)
                    if not self._subscribers[topic]:
                        del self._subscribers[topic]

        return unsubscribe

    def subscribe_all(self, handler: Callable[[str, Any], None]) -> Callable[[], None]:
        """
        Subscribe a handler to all events. Handler signature: handler(topic, data).
        Returns an unsubscribe callable.
        """
        with self._lock:
            self._all_subscribers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._all_subscribers:
                    self._all_subscribers.remove(handler)

        return unsubscribe

    def publish(self, topic: str, data: Any = None) -> None:
        """
        Publish an event to a topic with optional data.
        Handlers are executed synchronously under a copy of current subscribers.
        """
        with self._lock:
            topic_handlers = list(self._subscribers.get(topic, []))
            all_handlers = list(self._all_subscribers)

        for handler in topic_handlers:
            try:
                handler(data)
            except Exception as e:
                logger.exception(f"Error in EventBus handler {handler} for topic '{topic}': {e}")

        for handler in all_handlers:
            try:
                handler(topic, data)
            except Exception as e:
                logger.exception(f"Error in EventBus subscribe_all handler {handler} for topic '{topic}': {e}")

    def clear(self) -> None:
        """Clear all subscribers."""
        with self._lock:
            self._subscribers.clear()
            self._all_subscribers.clear()
