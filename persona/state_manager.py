import json
import time
import logging
from brain.llm_client import LLMClient
from config.settings import settings
from core.event_bus import EventBus, Event

logger = logging.getLogger(__name__)


class StateManager:

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.llm_client = LLMClient()
        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT
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

        self.last_interaction_logical_time = self._base_logical_time

        self.current_state = json.loads(settings.STATE)
        self.is_thinking = False

        self.event_bus.subscribe("user_interaction", self._on_llm_state_update)
        self.event_bus.subscribe("system.tick", self._on_tick)

    def _get_logical_now(self):
        real_elapsed = time.time() - self._real_start_time
        return self._base_logical_time + (real_elapsed * self.time_accel_factor)

    def _on_llm_state_update(self, event: Event):
        if self.is_thinking:
            return

        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT
        self.is_thinking = True
        history = event.data.get("history", [])
        prompt = f"{settings.SYSTEM_PROMPT}\n\n{settings.STATE_UPDATE_PROMPT}\n\n[近期对话记录]:\n{json.dumps(history, ensure_ascii=False)}"

        try:
            messages = [
                {"role": "system", "content": settings.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            response = self.llm_client.one_chat(
                model_config=settings.LARGE_LLM,
                messages=messages,
            )
            if response:
                new_state = json.loads(response)
                self.current_state.update(new_state)
                logical_time_str = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(self._get_logical_now())
                )
                logger.info(f"[{logical_time_str}] 对话引发状态更新: {new_state}")

        except Exception as e:
            logger.error(f"对话更新失败: {e}")
        finally:
            self.is_thinking = False
            self.last_interaction_logical_time = self._get_logical_now()

    def _on_tick(self, event: Event):
        if self.is_thinking:
            return

        logical_now = self._get_logical_now()
        logical_elapsed = logical_now - self.last_interaction_logical_time
        if logical_elapsed > self.state_update_timeout:
            self.state_update_timeout = min(
                self.state_update_timeout * 1.2, settings.STATE_IDLE_MAX_TIMEOUT
            )
            self._update_state_due_to_idle(logical_now, logical_elapsed)

    def _update_state_due_to_idle(self, logical_now, logical_elapsed):
        self.is_thinking = True

        idle_minutes = int(logical_elapsed / 60)
        current_time_str = time.strftime("%H:%M", time.localtime(logical_now))

        prompt = (
            settings.IDLE_STATE_UPDATE_PROMPT.replace(
                "{{idle_minutes}}", str(idle_minutes)
            )
            .replace("{{current_time}}", current_time_str)
            .replace(
                "{{old_state}}", json.dumps(self.current_state, ensure_ascii=False)
            )
        )

        try:
            messages = [
                {"role": "system", "content": settings.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            response = self.llm_client.one_chat(
                model_config=settings.LARGE_LLM,
                messages=messages,
            )
            if response:
                new_state = json.loads(response)
                self.current_state.update(new_state)
                logical_time_str = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(logical_now)
                )
                logger.info(f"[{logical_time_str}] 闲置逻辑演化: {new_state}")

        except Exception as e:
            logger.error(f"闲置更新失败: {e}")
        finally:
            self.is_thinking = False
            self.last_interaction_logical_time = logical_now

    @property
    def prompt_injection(self):
        state_text = json.dumps(self.current_state, ensure_ascii=False, indent=2)
        return f"\n\n###依鸣的当前状态###\n{state_text}\n\n"
