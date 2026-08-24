from __future__ import annotations

import threading
import time

from .event_bus import Event, EventBus


class Heartbeat:
    def __init__(self, event_bus: EventBus, interval: float = 10.0):
        self.event_bus = event_bus
        self.interval = max(float(interval), 1.0)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="ember-mobile-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        worker = self._thread
        if worker and worker.is_alive():
            worker.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.event_bus.publish(
                Event(
                    "system.tick",
                    {
                        "wall_time": time.time(),
                        "logical_time": self.event_bus.logical_now,
                    },
                )
            )
            if self._stop_event.wait(self.interval):
                break
