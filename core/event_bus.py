from collections import defaultdict
from typing import Callable, Any, Dict, List
import logging

logger = logging.getLogger(__name__)


class Event:
    def __init__(self, name: str, data: Any = None):
        self.name = name
        self.data = data

    def __repr__(self):
        return f"Event{self.name}, data={self.data})"


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_name: str, callback: Callable):
        self._subscribers[event_name].append(callback)

    def publish(self, event: Event):
        if event.name in self._subscribers:
            for callback in self._subscribers[event.name]:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"处理事件 {event.name} 时出错: {e}")
