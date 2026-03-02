import time
import threading
from core.event_bus import EventBus, Event


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
        while not self._stop_event.is_set():
            tick_event = Event(name="system.tick", data={"timestamp": time.time()})
            self.event_bus.publish(tick_event)
            time.sleep(self.interval)
