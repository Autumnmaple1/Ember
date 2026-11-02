from collections import defaultdict
import logging
import time
import threading
import math
from datetime import datetime
from core.time_utils import get_timezone, to_epoch
from typing import Callable, Any, Dict, List
from config.settings import settings
from core.turns import TurnCoordinator

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
        self.operation_lock = threading.RLock()
        self.paused = False
        self.world_paused = False
        self.quiet = False
        self.pending_condition = threading.Condition()
        self.pending_dialogues = 0
        self.turns = TurnCoordinator()
        self.timezone = get_timezone()
        self._init_time()

    def _init_time(self):
        self.time_accel_factor = settings.TIME_ACCEL_FACTOR
        try:
            self._base_logical_time = to_epoch(settings.START_TIME) if settings.START_TIME else time.time()
        except (ValueError, TypeError):
            warning = "START_TIME 格式无效，已使用当前现实时间"
            settings.CONFIG_WARNINGS.append(warning)
            logger.warning(warning)
            self._base_logical_time = time.time()
        self._real_start_time = time.monotonic()

    @property
    def logical_now(self):
        if self.world_paused:
            return self._base_logical_time
        real_elapsed = time.monotonic() - self._real_start_time
        logical_time = self._base_logical_time + (real_elapsed * self.time_accel_factor)
        return logical_time

    def pause_world(self, paused):
        with self.operation_lock:
            now = self.logical_now
            self._base_logical_time = now
            self._real_start_time = time.monotonic()
            self.world_paused = bool(paused)

    @property
    def formatted_logical_now(self):
        return self.format_logical_time(self.logical_now)

    def format_logical_time(self, logical_time, fmt="%Y-%m-%d %H:%M:%S"):
        return datetime.fromtimestamp(logical_time, self.timezone).strftime(fmt)

    def restore_logical_time(self, value):
        with self.operation_lock:
            self._base_logical_time = to_epoch(value)
            self._real_start_time = time.monotonic()

    def set_time_accel_factor(self, factor: float):
        """动态设置时间加速因子，同时修正基准时间以保持逻辑时间连续"""
        if not math.isfinite(factor) or factor <= 0:
            logger.warning(f"无效的时间加速因子: {factor}，必须大于0")
            return False

        # 先计算当前逻辑时间
        current_logical = self.logical_now

        # 更新加速因子
        self.time_accel_factor = factor

        # 重置基准，保持逻辑时间连续
        self._base_logical_time = current_logical
        self._real_start_time = time.monotonic()

        logger.info(f"时间加速因子已更新为: {factor}")
        return True

    def subscribe(self, event_name: str, callback: Callable):
        self._subscribers[event_name].append(callback)

    def publish(self, event: Event):
        results = []
        if event.name in self._subscribers:
            for callback in self._subscribers[event.name]:
                try:
                    results.append(callback(event))
                except Exception as e:
                    logger.error(f"处理事件 {event.name} 时出错: {e}")
        return results
