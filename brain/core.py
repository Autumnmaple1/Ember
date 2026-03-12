from brain.llm_client import LLMClient
from config.settings import settings
from memory.short_term import ShortTermMemory
from core.event_bus import EventBus, Event
from persona.state_manager import StateManager
import threading
import logging
import concurrent.futures
from memory.memory_process import Hippocampus
import json
from brain.tag_utils import validate_and_fix_llm_output

logger = logging.getLogger(__name__)


class Brain:

    def __init__(
        self,
        event_bus: EventBus,
        state_manager: StateManager,
        memory: ShortTermMemory,
        hippocampus: Hippocampus,
    ):
        self.lock = threading.Lock()
        self.llm_client = LLMClient()
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.memory = memory
        self.hippocampus = hippocampus
        self.event_bus.subscribe("user.input", self._on_user_input)
        self.event_bus.subscribe("idle_speak", self._on_idle_speak)

    def _on_user_input(self, event: Event):
        user_msg = event.data["text"]
        thread = threading.Thread(target=self.process_dialogue, args=(user_msg,))
        thread.start()

    def _on_idle_speak(self, event: Event):
        dynamic_prompt = settings.SYSTEM_PROMPT

        self.memory.update_base_prompt(dynamic_prompt)

        self._llm_speak(self.memory, pack=True)

    def process_dialogue(self, user_message):
        self.memory.add_message("user", user_message)
        history = json.dumps(self.memory.get_memory(), ensure_ascii=False)
        state = self.state_manager.prompt_injection
        messages = [
            "history:" + history,
            "state:" + state,
        ]
        memories = json.dumps(
            self.hippocampus.road_memory(messages), ensure_ascii=False
        )
        dynamic_prompt = settings.SYSTEM_PROMPT
        if memories:
            dynamic_prompt += f"\n\n[脑海闪现的记忆]：{memories}"

        self.memory.update_base_prompt(dynamic_prompt)

        self._llm_speak(self.memory, pack=False)

    def _llm_speak(self, memory, pack: bool = False):
        with self.lock:
            full_content = ""
            data = memory.get_full_messages()

            system_prompt = data[0]["content"]
            history = data[1:]

            if pack:
                formatted_history = ""
                for msg in history:
                    role_label = (
                        "对方"
                        if msg["role"] == "user"
                        else f"{settings.CHARACTER_NAME}"
                    )
                    formatted_history += f"{role_label}: {msg['content']}\n"

                messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"以下是对话历史：\n{formatted_history}\n{self.state_manager.prompt_injection}请参考并结合状态生成回复",
                    },
                ]
            else:
                messages = data

            self.event_bus.publish(Event(name="llm.started", data=""))
            for chunk in self.llm_client.stream_chat(
                model_config=settings.LARGE_LLM,
                messages=messages,
            ):
                full_content += chunk
                self.event_bus.publish(Event(name="llm.chunk", data={"text": chunk}))

            if full_content:
                # 修复可能不完整的标签
                full_content = validate_and_fix_llm_output(full_content)
                logger.info(f"LLM回复: {full_content}")
                self.memory.add_message("assistant", full_content)
                self.event_bus.publish(
                    Event(name="llm.finished", data={"text": full_content})
                )
                self.event_bus.publish(
                    Event(name="user_interaction", data=self.memory.get_memory())
                )
