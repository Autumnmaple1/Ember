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
        self._is_processing = False
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
        def _speak():
            with self.lock:
                dynamic_prompt = settings.SYSTEM_PROMPT
                self.memory.update_base_prompt(dynamic_prompt)
            self._llm_speak(self.memory, pack=True)

        thread = threading.Thread(target=_speak)
        thread.start()

    def process_dialogue(self, user_message):
        # 防止并发处理（简单丢弃重复输入）
        if self._is_processing:
            logger.warning("正在处理中，忽略新输入")
            return

        try:
            self._is_processing = True
            self.memory.add_message("user", user_message)
            history = json.dumps(self.memory.get_memory(), ensure_ascii=False)
            state = self.state_manager.prompt_injection
            messages = [
                "history:" + history,
                "state:" + state,
            ]
            road_result = self.hippocampus.road_memory(messages)
            memories = json.dumps(road_result, ensure_ascii=False) if road_result else ""
            dynamic_prompt = settings.SYSTEM_PROMPT
            if memories:
                dynamic_prompt += f"\n\n[脑海闪现的记忆]：{memories}"

            self.memory.update_base_prompt(dynamic_prompt)

            self._llm_speak(self.memory, pack=True)
        finally:
            self._is_processing = False

    def _llm_speak(self, memory, pack: bool = False):
        """LLM 对话生成，带超时和错误处理"""
        import concurrent.futures

        # 在锁外准备数据，减少锁持有时间
        with self.lock:
            data = memory.get_full_messages()
            system_prompt = data[0]["content"]
            history = data[1:]

        # 构建消息（在锁外）
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

        full_content = ""
        chunk_count = 0
        max_chunks = 10000  # 防止无限流

        try:
            # 使用超时包装流式调用
            stream_gen = self.llm_client.stream_chat(
                model_config=settings.LARGE_LLM,
                messages=messages,
            )

            for chunk in stream_gen:
                chunk_count += 1
                if chunk_count > max_chunks:
                    logger.warning("LLM 输出超过最大限制，截断")
                    break

                full_content += chunk
                self.event_bus.publish(Event(name="llm.chunk", data={"text": chunk}))

        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            # 通知用户错误
            error_msg = "[系统: AI 响应出现问题，请稍后再试]"
            self.event_bus.publish(Event(name="llm.chunk", data={"text": error_msg}))
            full_content = error_msg

        if full_content:
            # 修复可能不完整的标签
            full_content = validate_and_fix_llm_output(full_content)
            logger.info(f"LLM回复: {full_content[:100]}...")

            # 安全地添加消息到内存
            try:
                self.memory.add_message("assistant", full_content)
            except Exception as e:
                logger.error(f"保存消息失败: {e}")

            self.event_bus.publish(
                Event(name="llm.finished", data={"text": full_content})
            )
            self.event_bus.publish(
                Event(name="user_interaction", data=self.memory.get_memory())
            )
