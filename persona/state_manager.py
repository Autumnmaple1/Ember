import json
import time
import logging
from brain.llm_client import LLMClient
from config.settings import settings
from core.event_bus import EventBus, Event
from config.settings import settings
from memory.memory_process import Hippocampus
from memory.short_term import ShortTermMemory
import random
import threading

logger = logging.getLogger(__name__)


class StateManager:

    def __init__(self, event_bus: EventBus,hippocampus: Hippocampus,short_term_memory: ShortTermMemory):
        self.event_bus = event_bus
        self.hippocampus = hippocampus
        self.short_term_memory = short_term_memory
        self.llm_client = LLMClient()
        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT

        self.last_interaction_logical_time = self.event_bus.logical_now

        self.current_state = settings.STATE
        self.is_thinking = False
        self.is_sleeping = False

        self.event_bus.subscribe("user_interaction", self._on_llm_state_update)
        self.event_bus.subscribe("system.tick", self._on_tick)

    def _get_logical_now(self):
        return self.event_bus.logical_now

    def _format_logical_time(self, logical_time, fmt="%Y-%m-%d %H:%M:%S"):
        return self.event_bus.format_logical_time(logical_time, fmt)

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
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分钟")
        if secs > 0 or not parts:
            parts.append(f"{secs}秒")
        return "".join(parts)

    def _get_idle_info(self, logical_now):
        logical_elapsed = logical_now - self.last_interaction_logical_time
        return {
            "idle_duration": self._format_duration(logical_elapsed),
            "current_time": self._format_logical_time(logical_now, "%Y-%m-%d %H:%M:%S"),
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

    def _async_log(self, filename, content):
        def _log():
            with open(filename, "a", encoding="utf-8", buffering=1) as f:
                f.write(content + "\n")

        threading.Thread(target=_log,daemon=True).start()

    def _update_state(self, new_state, logical_now=None):
        self._async_log(
            "./config/chat_history.log",
            f"{{状态更新: {json.dumps(new_state, ensure_ascii=False)}}}",
        )
        
        self.event_bus.publish(
            Event("state.update", data={"new_state": new_state})
        )
        
        with open("./config/state.json", "w", encoding="utf-8") as f:
            json.dump(new_state, f, ensure_ascii=False, indent=2)
        self.current_state.update(new_state)

    def _on_tick(self, event: Event):
        if self.is_thinking:
            return

        logical_now = self._get_logical_now()
        logical_elapsed = logical_now - self.last_interaction_logical_time
        if logical_elapsed > self._get_floating_timeout(self.state_update_timeout):
            self.state_update_timeout = min(
                self.state_update_timeout * 1.5, settings.STATE_IDLE_MAX_TIMEOUT
            )
            self._update_state_due_to_idle(logical_now)

    def _on_llm_state_update(self, event: Event):
        if self.is_thinking:
            return

        self.state_update_timeout = settings.STATE_IDLE_MIN_TIMEOUT
        self.is_thinking = True
        history = event.data.get("history", [])
        logical_now = self._get_logical_now()
        logical_now_str = self._format_logical_time(logical_now)
        logger.info(f"[{logical_now_str}] 收到用户交互事件，准备更新状态...")
        
        context_for_memory = f"时间: {logical_now_str}\n先前状态: {json.dumps(self.current_state, ensure_ascii=False)}\n对话历史: {json.dumps(history, ensure_ascii=False)}"
        
        memories = self.hippocampus.road_memory(context_for_memory)
        
        prompt = f"当前的准确时间: {logical_now_str}\n\n[先前状态]\n{json.dumps(self.current_state, ensure_ascii=False)}\n\n[可能相关的记忆]:\n{memories if memories else '[]'}\n\n[近期对话记录]:\n{json.dumps(history, ensure_ascii=False)}\n\n{settings.STATE_UPDATE_PROMPT}"

        try:
            response = self._ask_llm(settings.CORE_PERSONA, prompt)
            if response:

                new_state = json.loads(response)
                new_state["对应时间"] = logical_now_str
                self._update_state(new_state, logical_now=logical_now)

                logger.info(f"[{logical_now_str}] 对话引发状态更新: {new_state}")

        except Exception as e:
            logger.error(f"对话更新失败: {e}")
        finally:
            self.is_thinking = False
            self.last_interaction_logical_time = logical_now

    def _update_state_due_to_idle(self, logical_now):
        self.is_thinking = True
        logical_now_str = self._format_logical_time(logical_now)
        logger.info(f"[{logical_now_str}] 收到闲置事件，准备更新状态...")

        info = self._get_idle_info(logical_now)
        
        history = self.short_term_memory.get_memory().get("history", [])
        
        context_for_memory = f"时间: {logical_now_str}\n先前状态: {json.dumps(self.current_state, ensure_ascii=False)}\n对话历史: {json.dumps(history, ensure_ascii=False)}"
        
        memories = self.hippocampus.road_memory(context_for_memory)
        
        prompt = self._apply_idle_template(settings.IDLE_STATE_UPDATE_PROMPT, info)
        
        if memories:
            prompt = f"[可能相关的记忆]:\n{memories}\n\n[近期对话记录]:\n{json.dumps(history, ensure_ascii=False)}\n\n" + prompt

        try:
            response = self._ask_llm(settings.CORE_PERSONA, prompt)
            if response:
                data = json.loads(response)
                impulse = data.get("action_pulse", {})

                if "action_pulse" in data:
                    del data["action_pulse"]

                data["对应时间"] = logical_now_str
                self._update_state(data, logical_now=logical_now)

                if impulse.get("memory_encode", False):
                    self.event_bus.publish(
                        Event(
                            name="memory.preprocess",
                            data={},
                        )
                    )
                    
                if impulse.get("is_sleeping", False) and not self.is_sleeping:
                    self.is_sleeping = True
                    self.event_bus.publish(
                        Event(
                            name="sleep",
                            data={},
                        )
                    )
                
                if not impulse.get("is_sleeping", False):
                    self.is_sleeping = False

                if impulse.get("should_speak", False):
                    self.event_bus.publish(
                        Event(
                            name="idle_speak",
                            data={},
                        )
                    )

                logger.info(f"[{logical_now_str}] 闲置逻辑演化: {data}")

        except Exception as e:
            logger.error(f"闲置更新失败: {e}")
        finally:
            self.is_thinking = False
            self.last_interaction_logical_time = logical_now

    @property
    def prompt_injection(self):
        state_text = json.dumps(self.current_state, ensure_ascii=False, indent=2)
        return f"\n\n###依鸣的前一时刻状态###\n{state_text}\n\n"

    @property
    def speaking_prompt_injection(self):
        logical_now = self._get_logical_now()
        info = self._get_idle_info(logical_now)
        prompt = self._apply_idle_template(settings.IDLE_SPEAKING_UPDATE_PROMPT, info)
        return f"\n\n###你的任务###\n{prompt}\n\n"

    @property
    def state_detail(self):
        return {
            "state": self.current_state,
            "last_update": self._format_logical_time(
                self.last_interaction_logical_time
            ),
        }
