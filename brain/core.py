"""
Brain 核心模块 - 负责对话处理和 LLM 交互

提供对话流程控制、消息处理和 LLM 流式响应管理
"""
import json
import logging
import threading
from typing import TYPE_CHECKING

from brain.llm_client import LLMClient
from brain.tag_utils import validate_and_fix_llm_output
from config.settings import settings
from core.event_bus import Event, EventBus

if TYPE_CHECKING:
    from memory.memory_process import Hippocampus
    from memory.short_term import ShortTermMemory
    from persona.state_manager import StateManager

logger = logging.getLogger(__name__)


class Brain:
    """大脑核心类，协调对话处理和 LLM 交互"""

    def __init__(
        self,
        event_bus: EventBus,
        state_manager: "StateManager",
        memory: "ShortTermMemory",
        hippocampus: "Hippocampus",
    ):
        self._lock = threading.Lock()
        self._is_processing = False
        self.llm_client = LLMClient()
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.memory = memory
        self.hippocampus = hippocampus

        self._subscribe_events()

    def _subscribe_events(self) -> None:
        """订阅相关事件"""
        self.event_bus.subscribe("user.input", self._on_user_input)
        self.event_bus.subscribe("idle_speak", self._on_idle_speak)

    def _on_user_input(self, event: Event) -> None:
        """处理用户输入事件"""
        user_msg = event.data["text"]
        threading.Thread(
            target=self.process_dialogue,
            args=(user_msg,),
            daemon=True
        ).start()

    def _on_idle_speak(self, event: Event) -> None:
        """处理空闲说话事件"""
        def _speak():
            with self._lock:
                self.memory.update_base_prompt(settings.SYSTEM_PROMPT)
            self._llm_speak(pack=True)

        threading.Thread(target=_speak, daemon=True).start()

    def process_dialogue(self, user_message: str) -> None:
        """
        处理对话流程

        流程：
        1. 检查并发限制
        2. 保存用户消息
        3. 检索相关记忆
        4. 构建动态提示
        5. 生成 LLM 回复

        Args:
            user_message: 用户输入的消息
        """
        if self._is_processing:
            logger.warning("正在处理中，忽略新输入")
            return

        try:
            self._is_processing = True
            self._handle_user_message(user_message)
        finally:
            self._is_processing = False

    def _handle_user_message(self, user_message: str) -> None:
        """处理用户消息的核心逻辑"""
        self.memory.add_message("user", user_message)

        # 检索相关记忆
        context = self._retrieve_context()
        memories = json.dumps(context, ensure_ascii=False) if context else ""

        # 构建动态提示
        dynamic_prompt = self._build_dynamic_prompt(memories)
        self.memory.update_base_prompt(dynamic_prompt)

        # 生成回复
        self._llm_speak(pack=True)

    def _retrieve_context(self) -> list | None:
        """检索对话相关的记忆上下文"""
        history = json.dumps(self.memory.get_memory(), ensure_ascii=False)
        state = self.state_manager.prompt_injection

        messages = [
            f"history:{history}",
            f"state:{state}",
        ]
        return self.hippocampus.road_memory(messages)

    def _build_dynamic_prompt(self, memories: str) -> str:
        """构建包含记忆上下文的动态提示"""
        prompt = settings.SYSTEM_PROMPT
        if memories:
            prompt += f"\n\n[脑海闪现的记忆]：{memories}"
        return prompt

    def _format_history_for_llm(self, messages: list[dict]) -> str:
        """
        将消息历史格式化为 LLM 可读格式

        Args:
            messages: 消息列表，每项包含 role 和 content

        Returns:
            格式化后的历史字符串
        """
        lines = []
        for msg in messages:
            role_label = "对方" if msg["role"] == "user" else settings.CHARACTER_NAME
            lines.append(f"{role_label}: {msg['content']}")
        return "\n".join(lines)

    def _build_llm_messages(self, pack: bool) -> list[dict]:
        """
        构建发送给 LLM 的消息列表

        Args:
            pack: 是否打包历史为单一消息

        Returns:
            LLM 消息列表
        """
        data = self.memory.get_full_messages()
        system_prompt = data[0]["content"]
        history = data[1:]

        if not pack:
            return data

        formatted_history = self._format_history_for_llm(history)

        # Prompt 分解日志
        state_injection = self.state_manager.prompt_injection
        base_len = len(settings.SYSTEM_PROMPT)
        mem_len = max(0, len(system_prompt) - base_len)
        logger.info(
            f"[Prompt Breakdown] base={base_len} mem={mem_len}"
            f" history={len(formatted_history)} state={len(state_injection)}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"以下是对话历史：\n{formatted_history}\n"
                    f"{state_injection}"
                    "请参考并结合状态生成回复"
                ),
            },
        ]

    def _llm_speak(self, pack: bool = False) -> None:
        """
        LLM 对话生成，带流式输出和错误处理

        Args:
            pack: 是否将历史打包为单一消息
        """
        messages = self._build_llm_messages(pack)
        self.event_bus.publish(Event(name="llm.started", data=""))

        full_content = self._stream_llm_response(messages)

        if not full_content:
            return

        # 修复可能不完整的标签
        full_content = validate_and_fix_llm_output(full_content)
        logger.info(f"LLM回复: {full_content[:100]}...")

        # 保存回复到内存
        self._save_assistant_message(full_content)

        # 发布完成事件
        self._publish_completion_events(full_content)

    def _stream_llm_response(self, messages: list[dict]) -> str:
        """
        流式获取 LLM 响应

        Args:
            messages: 发送给 LLM 的消息列表

        Returns:
            完整的响应内容
        """
        full_content = ""
        chunk_count = 0

        try:
            stream_gen = self.llm_client.stream_chat(
                model_config=settings.LARGE_LLM,
                messages=messages,
            )

            for chunk in stream_gen:
                chunk_count += 1
                if chunk_count > settings.LLM_MAX_CHUNKS:
                    logger.warning("LLM 输出超过最大限制，截断")
                    break

                full_content += chunk
                self.event_bus.publish(
                    Event(name="llm.chunk", data={"text": chunk})
                )

        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            error_msg = "[系统: AI 响应出现问题，请稍后再试]"
            self.event_bus.publish(
                Event(name="llm.chunk", data={"text": error_msg})
            )
            return error_msg

        return full_content

    def _save_assistant_message(self, content: str) -> None:
        """安全地保存助手消息到内存"""
        try:
            self.memory.add_message("assistant", content)
        except Exception as e:
            logger.error(f"保存消息失败: {e}")

    def _publish_completion_events(self, full_content: str) -> None:
        """发布 LLM 完成相关事件"""
        self.event_bus.publish(
            Event(name="llm.finished", data={"text": full_content})
        )
        self.event_bus.publish(
            Event(name="user_interaction", data=self.memory.get_memory())
        )
