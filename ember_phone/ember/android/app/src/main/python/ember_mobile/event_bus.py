from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import logging
import threading
import time
from typing import Any, Callable


@dataclass(frozen=True)
class Event:
    name: str
    data: Any = None


class EventBus:
    """Thread-safe event bus with Ember's accelerated logical clock."""

    def __init__(
        self,
        start_time: float,
        time_accel_factor: float = 1.0,
        time_flow_enabled: bool = True,
    ):
        self._subscribers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)
        self._lock = threading.RLock()
        self._base_logical_time = float(start_time)
        self._real_start_time = time.time()
        self._time_accel_factor = max(float(time_accel_factor), 0.001)
        self._time_flow_enabled = bool(time_flow_enabled)

    @property
    def logical_now(self) -> float:
        with self._lock:
            if not self._time_flow_enabled:
                return self._base_logical_time
            elapsed = time.time() - self._real_start_time
            return self._base_logical_time + elapsed * self._time_accel_factor

    @property
    def formatted_logical_now(self) -> str:
        return self.format_logical_time(self.logical_now)

    @staticmethod
    def format_logical_time(value: float) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))

    def set_time_accel_factor(self, factor: float) -> None:
        if factor <= 0:
            raise ValueError("time acceleration factor must be greater than zero")
        with self._lock:
            current = self.logical_now
            self._base_logical_time = current
            self._real_start_time = time.time()
            self._time_accel_factor = float(factor)

    def set_time_flow_enabled(self, enabled: bool) -> None:
        with self._lock:
            enabled = bool(enabled)
            if enabled == self._time_flow_enabled:
                return
            current = self.logical_now
            self._base_logical_time = current
            self._real_start_time = time.time()
            self._time_flow_enabled = enabled

    def reset_logical_time(self, logical_time: float) -> None:
        with self._lock:
            self._base_logical_time = float(logical_time)
            self._real_start_time = time.time()

    @property
    def time_accel_factor(self) -> float:
        with self._lock:
            return self._time_accel_factor

    @property
    def time_flow_enabled(self) -> bool:
        with self._lock:
            return self._time_flow_enabled

    def subscribe(self, event_name: str, callback: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers[event_name].append(callback)

    def publish(self, event: Event) -> None:
        with self._lock:
            callbacks = tuple(self._subscribers.get(event.name, ()))
        for callback in callbacks:
            try:
                callback(event)
            except Exception as error:
                logging.getLogger(__name__).error(
                    "处理事件 %s 时出错: %s", event.name, error
                )
