from brain.llm_client import LLMClient
from config.settings import settings
from memory.short_term import ShortTermMemory
from core.event_bus import EventBus, Event
from persona.state_manager import StateManager
import threading
import logging
from memory.memory_process import Hippocampus
import json
from brain.tag_utils import validate_and_fix_llm_output
from tools.processor import ToolCallProcessor

logger = logging.getLogger(__name__)


class Brain:

    # 最大单次对话工具调用次数
    MAX_TOOL_CALLS_PER_TURN = 5

    def __init__(
        self,
        event_bus: EventBus,
        state_manager: StateManager,
        memory: ShortTermMemory,
        hippocampus: Hippocampus,
        tool_processor: ToolCallProcessor = None,
    ):
        # 工具调用处理器（外部传入或自动创建）
        self.tool_processor = (
            tool_processor
            or ToolCallProcessor.create_with_memory_tool(
                hippocampus, max_calls=self.MAX_TOOL_CALLS_PER_TURN
            )
        )

        # Brain 自身的属性
        self.lock = threading.Lock()
        self._is_processing = False
        self.llm_client = LLMClient()
        self.state_manager = state_manager
        self.memory = memory
        self.event_bus = event_bus
        self.hippocampus = hippocampus

        # 订阅事件
        self.event_bus.subscribe("user.input", self._on_user_input)
        self.event_bus.subscribe("idle_speak", self._on_idle_speak)

    def _on_user_input(self, event: Event):
        user_msg = event.data["text"]
        thread = threading.Thread(target=self.process_dialogue, args=(user_msg,))
        thread.start()

    def _on_idle_speak(self, event: Event):
        def _speak():
            with self.lock:
                self.memory.update_base_prompt(settings.SYSTEM_PROMPT)
            self._llm_speak(self.memory, pack=True, memories="")

        thread = threading.Thread(target=_speak)
        thread.start()

    def process_dialogue(self, user_message):
        # 防止并发处理
        if self._is_processing:
            logger.warning("正在处理中，忽略新输入")
            return

        try:
            self._is_processing = True

            self.memory.add_message("user", user_message)

            # 异步更新 base_prompt（不阻塞 LLM 调用）
            # 注：实际注入到消息中是在 _llm_speak 中完成的
            self.memory.update_base_prompt(
                self.tool_processor.build_system_prompt_with_tools(settings.SYSTEM_PROMPT)
            )

            self._llm_speak(self.memory, pack=True)
        finally:
            self._is_processing = False

    def _llm_speak(self, memory, pack: bool = False, memories: str = ""):
        # 立即发布 llm.started 事件，让前端尽早显示"正在思考"
        self.event_bus.publish(Event(name="llm.started", data=""))

        # 在锁外准备数据，减少锁持有时间
        with self.lock:
            data = memory.get_full_messages()
            system_prompt = data[0]["content"]
            history = data[1:]

        # 构建消息（在锁外）
        if pack:
            # 使用列表构建历史，比字符串拼接更高效
            history_parts = []
            for msg in history:
                role_label = (
                    "对方" if msg["role"] == "user" else f"{settings.CHARACTER_NAME}"
                )
                history_parts.append(f"{role_label}: {msg['content']}\n")
            formatted_history = "".join(history_parts)

            dynamic_context = ""
            if memories:
                dynamic_context = f"\n\n[脑海闪现的记忆]：\n{memories}\n"

            state_injection = self.state_manager.prompt_injection

            user_content = f"""以下是对话历史：
{formatted_history}{dynamic_context}{state_injection}
现在的时间是{self.event_bus.formatted_logical_now}，请参考并结合状态生成你将要说的下一句话"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
        else:
            messages = data

        full_content = ""
        chunk_count = 0
        max_chunks = 10000

        try:
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

        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            error_msg = "[系统: AI 响应出现问题，请稍后再试]"
            full_content = error_msg

        if full_content:
            # 多轮工具调用处理
            max_tool_iterations = 1
            iteration = 0
            current_messages = messages.copy()

            while iteration < max_tool_iterations:
                iteration += 1

                # 检测并执行工具调用
                tool_calls = self.tool_processor.extract_tool_calls(full_content)

                if not tool_calls:
                    if iteration == 1:
                        # 首轮输出先在服务端缓冲；确认没有工具调用后再整体回放给前端。
                        self.event_bus.publish(
                            Event(name="llm.chunk", data={"text": full_content})
                        )
                    break  # 没有工具调用，结束循环

                logger.info(
                    f"[Tool] 第 {iteration} 轮检测到 {len(tool_calls)} 个工具调用"
                )

                # 执行工具调用（带调用来源标识）
                tool_results = self.tool_processor.execute_tool_calls(
                    tool_calls, caller=f"Brain._llm_speak[iter{iteration}]"
                )

                # 格式化工具结果
                tool_results_text = self.tool_processor.format_tool_results_for_prompt(
                    tool_results
                )

                # 从原始内容中移除工具调用标签
                clean_content = self.tool_processor.remove_tool_calls(full_content)

                # 如果有工具结果，需要让 LLM 继续生成回复
                if tool_results_text:
                    # 发布工具执行结果事件
                    self.event_bus.publish(
                        Event(
                            name="tool.executed",
                            data={
                                "calls": tool_calls,
                                "results": tool_results,
                                "iteration": iteration,
                            },
                        )
                    )

                    # 构建包含工具结果的新消息
                    current_messages = current_messages + [
                        {"role": "assistant", "content": full_content},
                        {
                            "role": "user",
                            "content": f"{tool_results_text}\n\n【重要】你现在已经获取了所需的信息，请立刻生成你的回复。\n【重要】不要再调用任何工具！直接根据工具结果和提示词要求生成回复！",
                        },
                    ]

                    # 再次调用 LLM 生成回复
                    try:
                        follow_up_content = ""
                        stream_gen = self.llm_client.stream_chat(
                            model_config=settings.LARGE_LLM,
                            messages=current_messages,
                        )

                        for chunk in stream_gen:
                            chunk_count += 1
                            follow_up_content += chunk
                            self.event_bus.publish(
                                Event(name="llm.chunk", data={"text": chunk})
                            )

                        full_content = follow_up_content

                    except Exception as e:
                        logger.error(f"LLM 工具后续调用失败: {e}")
                        full_content = clean_content
                        break
                else:
                    full_content = clean_content
                    break

            # 确保移除所有工具调用标签（防止 LLM 在工具执行后又输出工具调用）
            retry_count = 0
            max_retry = 2
            while (
                self.tool_processor.has_tool_calls(full_content)
                and retry_count < max_retry
            ):
                retry_count += 1
                logger.warning(
                    f"[Tool] 最终输出仍包含工具调用标签，请求 LLM 重新生成 (第 {retry_count} 次)"
                )

                # 请求 LLM 重新生成（不带工具调用）
                retry_messages = current_messages + [
                    {"role": "assistant", "content": full_content},
                    {
                        "role": "user",
                        "content": "你的回复中包含了工具调用标签，这是不允许的。请直接给出你的回复，不要使用任何工具调用。",
                    },
                ]

                try:
                    retry_content = ""
                    stream_gen = self.llm_client.stream_chat(
                        model_config=settings.LARGE_LLM,
                        messages=retry_messages,
                    )
                    for chunk in stream_gen:
                        retry_content += chunk
                        self.event_bus.publish(
                            Event(name="llm.chunk", data={"text": chunk})
                        )
                    full_content = retry_content
                except Exception as e:
                    logger.error(f"LLM 重试生成失败: {e}")
                    break

            # 最终仍有工具标签，强制清理
            if self.tool_processor.has_tool_calls(full_content):
                logger.warning("[Tool] 重试后仍有工具标签，强制清理")
                full_content = self.tool_processor.remove_tool_calls(full_content)

            # 修复可能不完整的标签
            full_content = validate_and_fix_llm_output(full_content)

            logger.info(f"LLM回复: {full_content[:100]}...")

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
