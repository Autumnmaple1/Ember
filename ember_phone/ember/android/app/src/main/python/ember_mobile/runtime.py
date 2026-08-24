from __future__ import annotations

import json
from pathlib import Path
import random
import threading
import time
from typing import Any

from .chat_memory import MobileChatMemory
from .archive_manager import MobileArchiveManager
from .config_store import MobileConfigStore
from .conversation import MobileConversation
from .event_bus import Event, EventBus
from .heartbeat import Heartbeat
from .memory_db import MobileMemoryDatabase
from .memory_manager import MobileMemoryManager
from .state_store import MobileStateStore
from .state_evolution import MobileStateEvolution
from .tool_system import create_mobile_tool_processor


class MobileEmberRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._running = False
        self._started_at: float | None = None
        self._tick_count = 0
        self._data_dir: Path | None = None
        self._cache_dir: Path | None = None
        self._event_bus: EventBus | None = None
        self._heartbeat: Heartbeat | None = None
        self._state_store: MobileStateStore | None = None
        self._config_store: MobileConfigStore | None = None
        self._chat_memory: MobileChatMemory | None = None
        self._conversation: MobileConversation | None = None
        self._memory_db: MobileMemoryDatabase | None = None
        self._memory_manager: MobileMemoryManager | None = None
        self._archive_manager: MobileArchiveManager | None = None
        self._state_evolution: MobileStateEvolution | None = None
        self._tool_processor = None
        self._event_callback = None
        self._idle_lock = threading.Lock()
        self._idle_cancel = threading.Event()
        self._dialogue_active = threading.Event()
        self._idle_timeout = 40.0
        self._is_sleeping = False

    def start(self, data_dir: str, cache_dir: str, event_callback=None) -> str:
        with self._lock:
            if self._running:
                return self.status_json()

            self._data_dir = Path(data_dir)
            self._cache_dir = Path(cache_dir)
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._cache_dir.mkdir(parents=True, exist_ok=True)

            self._config_store = MobileConfigStore(self._data_dir)
            runtime_config = self._config_store.private_value()
            checkpoint = MobileStateStore.load_checkpoint(self._data_dir) or {}
            factor = float(runtime_config.get("time_accel_factor", 5.0))
            time_flow_enabled = bool(
                runtime_config.get("time_flow_enabled", True)
            )
            saved_factor = float(checkpoint.get("time_accel_factor", factor))
            saved_time_flow_enabled = bool(
                checkpoint.get("time_flow_enabled", time_flow_enabled)
            )
            saved_wall = float(checkpoint.get("wall_time", time.time()))
            saved_logical = float(checkpoint.get("logical_time", time.time()))
            logical_start = (
                saved_logical
                + (
                    max(0.0, time.time() - saved_wall) * saved_factor
                    if saved_time_flow_enabled
                    else 0.0
                )
            )

            self._event_bus = EventBus(
                logical_start,
                factor,
                time_flow_enabled=time_flow_enabled,
            )
            self._event_bus.subscribe("system.tick", self._on_tick)
            self._event_bus.subscribe("user.input", self._on_user_input)
            self._state_store = MobileStateStore(
                self._event_bus,
                self._data_dir,
                checkpoint.get("last_interaction_logical_time"),
            )
            self._memory_db = MobileMemoryDatabase(self._data_dir)
            self._chat_memory = MobileChatMemory(
                self._data_dir,
                self._memory_db,
                max_messages=int(runtime_config.get("context_window_size", 20)),
            )
            self._memory_manager = MobileMemoryManager(
                self._memory_db,
                self._config_store,
            )
            self._archive_manager = MobileArchiveManager(
                self._data_dir,
                self._cache_dir,
                self._state_store,
                self._memory_db,
            )
            self._tool_processor = create_mobile_tool_processor(
                self._memory_manager,
            )
            self._state_evolution = MobileStateEvolution(
                self._state_store,
                self._config_store,
                self._chat_memory,
                self._tool_processor,
            )
            self._conversation = MobileConversation(
                self._event_bus,
                self._state_store,
                self._config_store,
                self._chat_memory,
                self._state_evolution,
                self._memory_manager,
                self._tool_processor,
            )
            self._heartbeat = Heartbeat(
                self._event_bus,
                interval=float(runtime_config.get("heartbeat_interval", 10.0)),
            )
            self._tick_count = 0
            self._event_callback = event_callback
            self._idle_timeout = float(
                runtime_config.get("state_idle_min_timeout", 40.0)
            )
            self._idle_cancel.clear()
            self._dialogue_active.clear()
            self._is_sleeping = False
            self._started_at = time.time()
            self._running = True
            self._heartbeat.start()
            print(
                "[EmberRuntime] started data_dir=%s heartbeat=%ss accel=%sx "
                "logical=%s"
                % (
                    self._data_dir,
                    runtime_config.get("heartbeat_interval", 10.0),
                    factor,
                    self._event_bus.formatted_logical_now,
                )
            )
            return self.status_json()

    def stop(self) -> str:
        with self._lock:
            heartbeat = self._heartbeat
            state_store = self._state_store
            memory_db = self._memory_db
            conversation = self._conversation
            tool_processor = self._tool_processor
            self._running = False
        if heartbeat:
            heartbeat.stop()
        if conversation:
            conversation.close()
        if tool_processor:
            tool_processor.shutdown()
        if state_store:
            state_store.persist()
        if memory_db:
            memory_db.close()
        print("[EmberRuntime] stopped")
        with self._lock:
            self._heartbeat = None
            self._conversation = None
            self._chat_memory = None
            self._memory_db = None
            self._memory_manager = None
            self._archive_manager = None
            self._state_evolution = None
            self._tool_processor = None
            self._config_store = None
            self._event_callback = None
            return self.status_json()

    def _on_tick(self, event: Event) -> None:
        with self._lock:
            self._tick_count += 1
            tick_count = self._tick_count
            state_store = self._state_store
            config_store = self._config_store
        if not state_store or not config_store:
            return
        if tick_count % 60 == 0:
            print(
                "[EmberRuntime] heartbeat alive tick=%s logical=%s idle=%ss"
                % (
                    tick_count,
                    state_store.event_bus.formatted_logical_now,
                    round(state_store.idle_seconds, 1),
                )
            )
        if not state_store.event_bus.time_flow_enabled:
            return
        if self._dialogue_active.is_set():
            return
        # The configured idle timeout is expressed in real-world seconds,
        # while StateStore measures idleness on the accelerated logical clock.
        # Convert the real timeout into logical seconds before comparing so
        # TIME_ACCEL_FACTOR changes character time without shortening waits.
        logical_idle_timeout = (
            self._idle_timeout * state_store.event_bus.time_accel_factor
        )
        # 与电脑端 StateManager._get_floating_timeout 一致：每次随机 ±10% 抖动
        logical_idle_timeout *= random.uniform(0.9, 1.1)
        if state_store.idle_seconds < logical_idle_timeout:
            return
        if not self._idle_lock.acquire(blocking=False):
            return
        self._idle_cancel.clear()
        threading.Thread(
            target=self._run_idle_evolution,
            name="ember-mobile-idle-evolution",
            daemon=True,
        ).start()

    def _on_user_input(self, event: Event) -> None:
        self._dialogue_active.set()
        self._idle_cancel.set()
        with self._lock:
            if self._config_store:
                self._idle_timeout = float(
                    self._config_store.private_value().get(
                        "state_idle_min_timeout", 40.0
                    )
                )

    def _emit(self, event_type: str, **data) -> None:
        callback = self._event_callback
        if callback is None:
            return
        try:
            callback.onEvent(
                json.dumps({"type": event_type, **data}, ensure_ascii=False)
            )
        except Exception:
            pass

    def _run_idle_evolution(self) -> None:
        try:
            print("[EmberRuntime] idle evolution started")
            with self._lock:
                evolution = self._state_evolution
                conversation = self._conversation
                state_store = self._state_store
                config_store = self._config_store
                memory_manager = self._memory_manager
            if (
                not evolution
                or not conversation
                or not state_store
                or not config_store
                or not memory_manager
            ):
                return

            result = evolution.update_due_to_idle(self._idle_cancel.is_set)
            if self._idle_cancel.is_set():
                return
            if result is None:
                state_store.record_idle_evolution()
                return
            self._emit("idle.state.updated", snapshot=result["snapshot"])
            evolution.reset_dialogue_count()

            pulse = result.get("action_pulse", {})
            if pulse.get("memory_encode"):
                memory_manager.schedule_encode(force=True)
            sleeping = bool(pulse.get("is_sleeping"))
            if sleeping and not self._is_sleeping:
                memory_manager.schedule_sleep_consolidation()
            self._is_sleeping = sleeping
            idle_seconds = state_store.idle_seconds
            if pulse.get("should_speak") and not sleeping:
                def emit_idle_tool(event_type: str, **data) -> None:
                    self._emit(event_type, background=True, **data)

                message = conversation.speak_due_to_idle(
                    idle_seconds,
                    self._idle_cancel.is_set,
                    emit_idle_tool,
                )
                if message and not self._idle_cancel.is_set():
                    self._emit("idle.message", **message)
                    print("[EmberRuntime] idle message emitted")

            state_store.record_idle_evolution()
            print("[EmberRuntime] idle evolution finished")
            config = config_store.private_value()
            maximum = float(config.get("state_idle_max_timeout", 120.0))
            self._idle_timeout = min(self._idle_timeout * 1.5, maximum)
        except Exception as error:
            self._emit("idle.error", message=str(error))
            # 失败也算完成一次空闲窗口：重置空闲计时并拉长超时，
            # 避免每个心跳都立刻重试，造成频繁“空闲演化失败”。
            state_store = self._state_store
            config_store = self._config_store
            if state_store:
                state_store.record_idle_evolution()
            if config_store:
                maximum = float(
                    config_store.private_value().get(
                        "state_idle_max_timeout", 120.0
                    )
                )
                self._idle_timeout = min(self._idle_timeout * 1.5, maximum)
        finally:
            self._idle_cancel.clear()
            self._idle_lock.release()

    def status(self) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = {
                "running": self._running,
                "started_at": self._started_at,
                "tick_count": self._tick_count,
                "data_dir": str(self._data_dir) if self._data_dir else None,
                "cache_dir": str(self._cache_dir) if self._cache_dir else None,
                "memory_db": str(self._memory_db.path) if self._memory_db else None,
            }
            if self._state_store:
                result.update(self._state_store.snapshot())
            if self._memory_db:
                result["memory"] = self._memory_db.memory_stats()
            return result

    def status_json(self) -> str:
        return json.dumps(self.status(), ensure_ascii=False)

    def memory_overview_json(self) -> str:
        with self._lock:
            if not self._memory_db:
                raise RuntimeError("Ember runtime is not running")
            value = self._memory_db.graph_overview()
        return json.dumps(value, ensure_ascii=False)

    def record_interaction_json(self) -> str:
        with self._lock:
            if not self._state_store:
                raise RuntimeError("Ember runtime is not running")
            value = self._state_store.record_interaction()
        return json.dumps(value, ensure_ascii=False)

    def set_pad_json(self, pleasure: float, arousal: float, dominance: float) -> str:
        with self._lock:
            if not self._state_store:
                raise RuntimeError("Ember runtime is not running")
            value = self._state_store.set_pad(pleasure, arousal, dominance)
        return json.dumps(value, ensure_ascii=False)

    def config_json(self) -> str:
        with self._lock:
            if not self._config_store:
                raise RuntimeError("Ember runtime is not running")
            value = self._config_store.public_value()
        return json.dumps(value, ensure_ascii=False)

    def update_app_config_json(self, config_json: str) -> str:
        values = json.loads(config_json)
        if not isinstance(values, dict):
            raise ValueError("设置数据必须是 JSON 对象")
        with self._lock:
            if not self._config_store:
                raise RuntimeError("Ember runtime is not running")
            updated = self._config_store.update_app_config(values)
            if self._event_bus:
                self._event_bus.set_time_accel_factor(
                    float(updated["time_accel_factor"])
                )
                self._event_bus.set_time_flow_enabled(
                    bool(updated["time_flow_enabled"])
                )
                if not bool(updated["time_flow_enabled"]):
                    self._idle_cancel.set()
            if self._heartbeat:
                self._heartbeat.interval = float(updated["heartbeat_interval"])
            if self._chat_memory:
                self._chat_memory.set_max_messages(
                    int(updated["context_window_size"])
                )
            self._idle_timeout = float(updated["state_idle_min_timeout"])
            if self._state_store:
                self._state_store.persist()
        return json.dumps(updated, ensure_ascii=False)

    def update_config_json(
        self,
        base_url: str,
        model: str,
        small_model: str,
        api_key: str | None,
        temperature: float,
        state_updates_enabled: bool,
    ) -> str:
        with self._lock:
            if not self._config_store:
                raise RuntimeError("Ember runtime is not running")
            value = self._config_store.update(
                base_url,
                model,
                small_model,
                api_key,
                temperature,
                state_updates_enabled,
            )
        return json.dumps(value, ensure_ascii=False)

    def send_message_json(self, text: str) -> str:
        with self._lock:
            conversation = self._conversation
        if not conversation:
            raise RuntimeError("Ember runtime is not running")
        try:
            return json.dumps(conversation.send(text), ensure_ascii=False)
        finally:
            self._dialogue_active.clear()

    def send_message_stream_json(
        self,
        text: str,
        image_size: str | None,
        event_callback,
    ) -> str:
        with self._lock:
            conversation = self._conversation
        if not conversation:
            raise RuntimeError("Ember runtime is not running")
        try:
            return json.dumps(
                conversation.send_stream(
                    text, event_callback, image_size=image_size
                ),
                ensure_ascii=False,
            )
        finally:
            self._dialogue_active.clear()

    def history_json(self) -> str:
        with self._lock:
            if not self._conversation:
                raise RuntimeError("Ember runtime is not running")
            value = self._conversation.history()
        return json.dumps(value, ensure_ascii=False)

    def clear_history_json(self) -> str:
        with self._lock:
            if not self._conversation:
                raise RuntimeError("Ember runtime is not running")
            self._conversation.clear_history()
        return json.dumps({"ok": True}, ensure_ascii=False)

    def list_archives_json(self) -> str:
        with self._lock:
            if not self._archive_manager:
                raise RuntimeError("Ember runtime is not running")
            value = self._archive_manager.list()
        return json.dumps(value, ensure_ascii=False)

    def create_archive_json(self, name: str) -> str:
        with self._lock:
            if not self._archive_manager:
                raise RuntimeError("Ember runtime is not running")
            value = self._archive_manager.create(name)
        return json.dumps(value, ensure_ascii=False)

    def load_archive_json(self, archive_id: str) -> str:
        if self._dialogue_active.is_set():
            raise RuntimeError("对话进行中，暂时不能加载存档")
        if not self._idle_lock.acquire(blocking=False):
            raise RuntimeError("后台状态演化进行中，请稍后再试")
        memory_manager = self._memory_manager
        if not memory_manager or not memory_manager.try_acquire_archive_lock():
            self._idle_lock.release()
            raise RuntimeError("后台记忆整理进行中，请稍后再试")
        self._dialogue_active.set()
        self._idle_cancel.set()
        try:
            with self._lock:
                if not self._archive_manager:
                    raise RuntimeError("Ember runtime is not running")
                value = self._archive_manager.load(archive_id)
                if self._state_evolution:
                    self._state_evolution.reset_dialogue_count()
                self._idle_timeout = float(
                    self._config_store.private_value().get(
                        "state_idle_min_timeout", 40.0
                    )
                )
            return json.dumps(value, ensure_ascii=False)
        finally:
            self._dialogue_active.clear()
            self._idle_cancel.clear()
            memory_manager.release_archive_lock()
            self._idle_lock.release()

    def delete_archive_json(self, archive_id: str) -> str:
        with self._lock:
            if not self._archive_manager:
                raise RuntimeError("Ember runtime is not running")
            value = self._archive_manager.delete(archive_id)
        return json.dumps(value, ensure_ascii=False)
