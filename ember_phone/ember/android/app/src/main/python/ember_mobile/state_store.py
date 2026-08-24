from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from typing import Any

from .event_bus import Event, EventBus


DEFAULT_STATE: dict[str, Any] = {
    "P": 7,
    "A": 8,
    "D": 4,
    "客观情境": (
        "南京大学鼓楼校区北园，午后的图书馆自习室。依鸣正坐在靠窗的位置，"
        "阳光透过落地窗洒在她专注的侧脸上，桌上摊开着一本算法导论和几页"
        "写满笔记的草稿纸。"
    ),
    "近期综合轨迹": (
        "在图书馆自习了一上午 -> 中午在附近的小食堂吃了碗热汤面 -> "
        "下午继续整理算法课的笔记 -> 偶然抬头时与对面的人视线相遇。"
    ),
    "内心活动": (
        "那个坐在对面的人...好像一直在看我？不不不，应该只是恰好对视了吧。"
        "不过他的眼睛真的很好看啊，就是那种...让人不敢再直视的感觉。"
        "为什么心跳突然变快了？明明只是在自习室偶遇而已。要不要继续低头"
        "看书...可是好像又有点想再偷偷看一眼？"
    ),
    "近期目标": (
        "假装若无其事地继续看书，整理完这页算法笔记，偷偷观察对面那个人"
        "是不是还在这里。"
    ),
    "对应时间": "2026-03-06 14:15:00",
}


class MobileStateStore:
    """Owns mobile PAD state and persists it in Android's private files dir."""

    def __init__(
        self,
        event_bus: EventBus,
        data_dir: Path,
        last_interaction_logical_time: float | None = None,
    ):
        self.event_bus = event_bus
        self.root_dir = data_dir / "ember"
        self.state_path = self.root_dir / "state.json"
        self.checkpoint_path = self.root_dir / "runtime_checkpoint.json"
        self._lock = threading.RLock()
        self._ticks_since_save = 0
        self._last_interaction_logical_time = (
            float(last_interaction_logical_time)
            if last_interaction_logical_time is not None
            else event_bus.logical_now
        )
        self.current_state = self._load_state()
        self.event_bus.subscribe("system.tick", self._on_tick)

    def _load_state(self) -> dict[str, Any]:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = deepcopy(DEFAULT_STATE)
                state.update(loaded)
                return state
        except (OSError, ValueError, TypeError):
            pass
        return deepcopy(DEFAULT_STATE)

    def _atomic_write(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def persist(self) -> None:
        with self._lock:
            self.current_state["对应时间"] = self.event_bus.formatted_logical_now
            self._atomic_write(self.state_path, self.current_state)
            self._atomic_write(
                self.checkpoint_path,
                {
                    "wall_time": time.time(),
                    "logical_time": self.event_bus.logical_now,
                    "time_accel_factor": self.event_bus.time_accel_factor,
                    "time_flow_enabled": self.event_bus.time_flow_enabled,
                    "last_interaction_logical_time": self._last_interaction_logical_time,
                },
            )
            self._ticks_since_save = 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": deepcopy(self.current_state),
                "logical_time": self.event_bus.formatted_logical_now,
                "time_accel_factor": self.event_bus.time_accel_factor,
                "time_flow_enabled": self.event_bus.time_flow_enabled,
                "last_interaction": self.event_bus.format_logical_time(
                    self._last_interaction_logical_time
                ),
            }

    def record_interaction(self) -> dict[str, Any]:
        with self._lock:
            self._last_interaction_logical_time = self.event_bus.logical_now
            self.current_state["对应时间"] = self.event_bus.formatted_logical_now
            self.persist()
            snapshot = self.snapshot()
        self.event_bus.publish(Event("user_interaction", snapshot))
        return snapshot

    @property
    def idle_seconds(self) -> float:
        with self._lock:
            return max(
                0.0,
                self.event_bus.logical_now - self._last_interaction_logical_time,
            )

    def record_idle_evolution(self) -> dict[str, Any]:
        """Start a fresh idle window without pretending a user interacted."""
        with self._lock:
            self._last_interaction_logical_time = self.event_bus.logical_now
            self.persist()
            return self.snapshot()

    def set_pad(self, pleasure: float, arousal: float, dominance: float) -> dict[str, Any]:
        def clamp(value: float) -> float:
            return round(max(0.0, min(10.0, float(value))), 2)

        with self._lock:
            self.current_state.update(
                {"P": clamp(pleasure), "A": clamp(arousal), "D": clamp(dominance)}
            )
            self.persist()
            snapshot = self.snapshot()
        self.event_bus.publish(Event("state.update", snapshot))
        return snapshot

    def update_fields(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            values = {}
        with self._lock:
            # 与电脑端 StateManager._update_state 一致：合并 LLM 返回的全部字段，
            # 不做白名单过滤或 PAD 钳制，保证两端状态演化行为一致。
            self.current_state.update(deepcopy(values))
            self.persist()
            snapshot = self.snapshot()
        self.event_bus.publish(Event("state.update", snapshot))
        return snapshot

    def restore(
        self,
        state: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(state, dict) or not all(key in state for key in ("P", "A", "D")):
            raise ValueError("存档状态格式不正确")
        logical_time = float(checkpoint.get("logical_time", time.time()))
        last_interaction = float(
            checkpoint.get("last_interaction_logical_time", logical_time)
        )
        with self._lock:
            restored = deepcopy(DEFAULT_STATE)
            restored.update(state)
            self.current_state = restored
            self._last_interaction_logical_time = last_interaction
            self.event_bus.reset_logical_time(logical_time)
            self.persist()
            snapshot = self.snapshot()
        self.event_bus.publish(Event("state.update", snapshot))
        return snapshot

    def _on_tick(self, event: Event) -> None:
        with self._lock:
            self.current_state["对应时间"] = self.event_bus.formatted_logical_now
            self._ticks_since_save += 1
            if self._ticks_since_save >= 6:
                self.persist()

    @classmethod
    def load_checkpoint(cls, data_dir: Path) -> dict[str, Any] | None:
        path = data_dir / "ember" / "runtime_checkpoint.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None
