import logging
import time
import threading
from core.event_bus import EventBus, Event

logger = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, event_bus: EventBus, interval: int = 10):
        self.event_bus = event_bus
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run)
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()

    def _run(self):
        """心跳循环，带异常保护防止线程崩溃"""
        consecutive_errors = 0
        max_consecutive_errors = 5

        while not self._stop_event.is_set():
            try:
                tick_event = Event(name="system.tick", data={"timestamp": time.time()})
                self.event_bus.publish(tick_event)
                consecutive_errors = 0  # 成功执行后重置错误计数
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"心跳 tick 异常 (连续 {consecutive_errors} 次): {e}")

                if consecutive_errors >= max_consecutive_errors:
                    logger.critical(f"心跳连续失败 {max_consecutive_errors} 次，心跳线程退出")
                    break

            # 使用 wait 支持中断
            if self._stop_event.wait(self.interval):
                break
