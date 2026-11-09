import time
import threading
import logging
from core.event_bus import EventBus, Event

logger = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, event_bus: EventBus, interval: int = 10):
        self.event_bus = event_bus
        self.interval = interval
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._thread = None
        self._is_paused = False

    def start(self):
        """启动心跳线程"""
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._pause_event.clear()
            self._is_paused = False
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            logger.debug("心跳线程已启动")

    def stop(self):
        """停止心跳线程"""
        self._stop_event.set()
        self._pause_event.set()  # 确保暂停状态不会阻塞
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logger.debug("心跳线程已停止")

    def pause(self):
        """暂停心跳（不停止线程）"""
        self._pause_event.set()
        self._is_paused = True
        logger.debug("心跳已暂停")

    def resume(self):
        """恢复心跳"""
        self._pause_event.clear()
        self._is_paused = False
        logger.debug("心跳已恢复")

    @property
    def is_paused(self) -> bool:
        """是否处于暂停状态"""
        return self._is_paused

    def _run(self):
        """心跳循环，带异常保护防止线程崩溃"""
        consecutive_errors = 0
        max_consecutive_errors = 5

        while not self._stop_event.is_set():
            # 检查暂停状态
            while self._pause_event.is_set() and not self._stop_event.is_set():
                time.sleep(0.1)  # 暂停期间轮询检查
                continue

            if self._stop_event.is_set():
                break

            try:
                tick_event = Event(name="system.tick", data={"timestamp": time.time()})
                self.event_bus.publish(tick_event)
                consecutive_errors = 0  # 成功执行后重置错误计数
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"心跳 tick 异常 (连续 {consecutive_errors} 次): {e}")

                if consecutive_errors >= max_consecutive_errors:
                    logger.critical(
                        f"心跳连续失败 {max_consecutive_errors} 次，心跳线程退出"
                    )
                    break

            # 使用 wait 支持中断
            if self._stop_event.wait(self.interval):
                break
