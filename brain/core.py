from brain.llm_client import LLMClient
from config.settings import settings
from memory.short_term import ShortTermMemory
from core.event_bus import EventBus, Event
from persona.state_manager import StateManager
import threading
import logging
import concurrent.futures
import json

logger = logging.getLogger(__name__)


class Brain:

    def __init__(
        self, event_bus: EventBus, state_manager: StateManager, memory: ShortTermMemory
    ):
        self.lock = threading.Lock()
        self.llm_client = LLMClient()
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.memory = memory
        self.event_bus.subscribe("user.input", self._on_user_input)
        self.event_bus.subscribe("idle_speak", self._on_idle_speak)

    def _on_user_input(self, event: Event):
        user_msg = event.data["text"]
        thread = threading.Thread(target=self.process_dialogue, args=(user_msg,))
        thread.start()

    def _on_idle_speak(self, event: Event):
        dynamic_prompt = (
            settings.SYSTEM_PROMPT + self.state_manager.speaking_prompt_injection
        )

        self.memory.update_base_prompt(dynamic_prompt)

        self._llm_speak(self.memory, pack=True)

    def process_dialogue(self, user_message):
        self.memory.add_message("user", user_message)
        history = json.dumps(self.memory.get_memory(), ensure_ascii=False)

        resp = self.llm_client.one_chat(
            model_config=settings.SMALL_LLM,
            messages=[
                {
                    "role": "user",
                    "content": f"{settings.MEMORY_JUDGE_PROMPT}\n\n对话历史：{history}",
                }
            ],
        )
        resp = resp.replace("True", "true").replace("False", "false")
        resp = json.loads(resp)

        need_memory = resp.get("need_memory", False)
        keywords = resp.get("keywords", [])
        data = {"user_message": user_message, "key_words": keywords}
        memories = None
        if need_memory or True:
            logger.info("LLM判断需要相关记忆，正在查询...")
            logger.info(f"查询关键词: {keywords}")
            memories = json.dumps(self.get_persistence_memory(data), ensure_ascii=False)
            self.memory.async_log("./config/chat_history.log", f"Memory: {memories}")

        dynamic_prompt = settings.SYSTEM_PROMPT + self.state_manager.prompt_injection
        if memories:
            dynamic_prompt += f"\n\n可能用到的记忆：{memories}"

        self.memory.update_base_prompt(dynamic_prompt)

        self._llm_speak(self.memory, pack=False)

    def get_persistence_memory(self, query_data):
        future = concurrent.futures.Future()

        def on_memory_retrieved(memories):
            if not future.done():
                logger.info(f"Retrieved relevant memories: {memories}")
                future.set_result(memories)

        query_data["callback"] = on_memory_retrieved
        self.event_bus.publish(Event("memory.query", query_data))        
        try:
            return future.result(timeout=5)
        except concurrent.futures.TimeoutError:
            logger.error("查询持久化记忆超时，跳过查询以维持对话。")
            return []
        except Exception as e:
            logger.error(f"查询持久化记忆时发生异常: {e}")
            return []

    def _llm_speak(self, memory, pack: bool = False):
        with self.lock:
            full_content = ""
            data = memory.get_full_messages()

            system_prompt = data[0]["content"]
            history = data[1:]

            if pack:
                formatted_history = ""
                for msg in history:
                    role_label = "用户" if msg["role"] == "user" else "你"
                    formatted_history += f"{role_label}: {msg['content']}\n"

                messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"以下是对话历史：\n{formatted_history}\n请参考并结合意图生成回复",
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
                self.memory.add_message("assistant", full_content)
                self.event_bus.publish(
                    Event(name="llm.finished", data={"full_text": full_content})
                )
                self.event_bus.publish(
                    Event(name="user_interaction", data=self.memory.get_memory())
                )
                logger.info(f"LLM回复: {full_content}")
