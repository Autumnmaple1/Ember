from collections import defaultdict
import logging
import time
from typing import Callable, Any, Dict, List
from config.settings import settings

logger = logging.getLogger(__name__)


class Event:
    def __init__(self, name: str, data: Any = None):
        self.name = name
        self.data = data

    def __repr__(self):
        return f"Event({self.name}, data={self.data})"


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = defaultdict(list)
        self._init_time()

    def _init_time(self):
        self.time_accel_factor = settings.TIME_ACCEL_FACTOR
        if isinstance(settings.START_TIME, str):
            try:
                self._base_logical_time = time.mktime(
                    time.strptime(settings.START_TIME, "%Y-%m-%d %H:%M:%S")
                )
            except Exception:
                self._base_logical_time = time.time()
        else:
            self._base_logical_time = float(settings.START_TIME)
        self._real_start_time = time.time()

    @property
    def logical_now(self):
        real_elapsed = time.time() - self._real_start_time
        logical_time = self._base_logical_time + (real_elapsed * self.time_accel_factor)
        formatted_time = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(logical_time)
        )
        # logger.debug(f"[Logical Time Access] {formatted_time}")
        return logical_time

    @property
    def formatted_logical_now(self):
        return self.format_logical_time(self.logical_now)

    def format_logical_time(self, logical_time, fmt="%Y-%m-%d %H:%M:%S"):
        return time.strftime(fmt, time.localtime(logical_time))

    def subscribe(self, event_name: str, callback: Callable):
        self._subscribers[event_name].append(callback)

    def publish(self, event: Event):
        if event.name in self._subscribers:
            for callback in self._subscribers[event.name]:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"处理事件 {event.name} 时出错: {e}")
