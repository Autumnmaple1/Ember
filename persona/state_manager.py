import json
import time
import logging
from brain.llm_client import LLMClient
from config.settings import settings
from core.event_bus import EventBus, Event
import random

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

    def _format_logical_time(self, logical_time, fmt="%Y-%m-%d %H:%M:%S"):
        return time.strftime(fmt, time.localtime(logical_time))

    def _get_floating_timeout(self, time: float):
        lower = time * 0.9
        upper = time * 1.1
        return random.uniform(lower, upper)

    def _format_duration(self, seconds: float):
        days = int(seconds // (24 * 3600))
        rem = seconds % (24 * 3600)
        hours = int(rem // 3600)
        rem %= 3600
        minutes = int(rem // 60)
        secs = int(rem % 60)
        
        parts = []
        if days > 0: parts.append(f"{days}天")
        if hours > 0: parts.append(f"{hours}小时")
        if minutes > 0: parts.append(f"{minutes}分钟")
        if secs > 0 or not parts: parts.append(f"{secs}秒")
        return "".join(parts)

    def _get_idle_info(self, logical_now):
        logical_elapsed = logical_now - self.last_interaction_logical_time
        return {
            "idle_duration": self._format_duration(logical_elapsed),
            "current_time": self._format_logical_time(logical_now, "%H:%M"),
            "old_state": json.dumps(self.current_state, ensure_ascii=False),
        }

    def _apply_idle_template(self, template, info):
        return (
            template.replace("{{idle_minutes}}", info["idle_duration"])
            .replace("{{current_time}}", info["current_time"])
            .replace("{{old_state}}", info["old_state"])
        )

    def _ask_llm(self, system_prompt, user_prompt):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.llm_client.one_chat(
            model_config=settings.LARGE_LLM,
            messages=messages,
        )

    def _on_llm_state_update(self, event: Event):
        if self.is_thinking:
            return

        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT
        self.is_thinking = True
        history = event.data.get("history", [])
        prompt = f"{settings.SYSTEM_PROMPT}\n\n{settings.STATE_UPDATE_PROMPT}\n\n[近期对话记录]:\n{json.dumps(history, ensure_ascii=False)}"

        try:
            response = self._ask_llm(settings.SYSTEM_PROMPT, prompt)
            if response:
                new_state = json.loads(response)
                self.current_state.update(new_state)
                logical_time_str = self._format_logical_time(self._get_logical_now())
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
        if logical_elapsed > self._get_floating_timeout(self.state_update_timeout):
            self.state_update_timeout = min(
                self.state_update_timeout * 1.5, settings.STATE_IDLE_MAX_TIMEOUT
            )
            self._update_state_due_to_idle(logical_now, logical_elapsed)

    def _update_state_due_to_idle(self, logical_now, logical_elapsed):
        self.is_thinking = True

        info = self._get_idle_info(logical_now)
        prompt = self._apply_idle_template(settings.IDLE_STATE_UPDATE_PROMPT, info)

        try:
            response = self._ask_llm(settings.SYSTEM_PROMPT, prompt)
            if response:
                data = json.loads(response)
                impulse = data.get("action_pulse", {})

                if "action_pulse" in data:
                    del data["action_pulse"]

                self.current_state.update(data)

                if impulse.get("should_speak", False):
                    self.event_bus.publish(
                        Event(
                            name="idle_speak",
                            data={},
                        )
                    )

                logical_time_str = self._format_logical_time(logical_now)
                logger.info(f"[{logical_time_str}] 闲置逻辑演化: {data}")

        except Exception as e:
            logger.error(f"闲置更新失败: {e}")
        finally:
            self.is_thinking = False
            self.last_interaction_logical_time = self._get_logical_now()

    @property
    def prompt_injection(self):
        state_text = json.dumps(self.current_state, ensure_ascii=False, indent=2)
        return f"\n\n###依鸣的当前状态###\n{state_text}\n\n"

    @property
    def speaking_prompt_injection(self):
        logical_now = self._get_logical_now()
        info = self._get_idle_info(logical_now)
        prompt = self._apply_idle_template(settings.IDLE_SPEAKING_UPDATE_PROMPT, info)
        return f"\n\n###你的任务###\n{prompt}\n\n"
