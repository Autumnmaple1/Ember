from brain.llm_client import LLMClient
from config.settings import settings
from memory.short_term import ShortTermMemory
from core.event_bus import EventBus, Event
from persona.state_manager import StateManager
import threading
import queue
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
        self._is_speaking = False
        self._message_queue = queue.Queue(maxsize=100)
        self.llm_client = LLMClient()
        self.state_manager = state_manager
        self.memory = memory
        self.event_bus = event_bus
        self.hippocampus = hippocampus

        # 订阅事件
        self.event_bus.subscribe("user.input", self._on_user_input)
        self.event_bus.subscribe("idle_speak", self._on_idle_speak)

    def _on_user_input(self, event: Event):
        user_message = event.data["text"]
        try:
            self._message_queue.put_nowait(user_message)
        except queue.Full:
            logger.error("消息队列已满，消息被丢弃")
            return
        # 中断任何进行中的空闲演化，用户输入优先
        self.state_manager._interrupt_idle.set()
        threading.Thread(target=self._process_queue, daemon=True).start()

    def _on_idle_speak(self, event: Event):
        def _speak():
            with self.lock:
                self.memory.update_base_prompt(settings.SYSTEM_PROMPT)
            self._llm_speak(
                self.memory,
                pack=True,
                memories="",
                idle_duration_seconds=event.data.get("idle_duration_seconds"),
            )

        thread = threading.Thread(target=_speak)
        thread.start()

    def _execute_dialogue(self, user_message):
        """核心对话处理逻辑（假定调用者已设置 _is_processing）"""

        self.memory.add_message("user", user_message, timestamp=self.event_bus.formatted_logical_now)

        # === Pre-Routing 意图识别与预检索 ===
        dynamic_context = ""
        try:
            pre_routing_msg = [
                {"role": "system", "content": settings.PRE_ROUTING_PROMPT},
                {"role": "user", "content": f"用户最新输入：{user_message}"},
            ]

            model_to_use = (
                settings.SMALL_LLM
                if settings.SMALL_LLM.api_key
                else settings.LARGE_LLM
            )

            intent_json_str = self.llm_client.one_chat(
                model_config=model_to_use,
                messages=pre_routing_msg,
                timeout=10,
                call_type="pre_routing",
            )

            intent_data = (
                self.llm_client._extract_json(intent_json_str)
                if intent_json_str
                else {}
            )

            need_memory = intent_data.get("need_memory", False)
            memory_query = intent_data.get("memory_query", "")
            memory_keywords = intent_data.get("memory_keywords", [])
            need_search = intent_data.get("need_search", False)
            search_query = intent_data.get("search_query", "")

            new_location = intent_data.get("location", "")
            new_action = intent_data.get("action", "")
            logger.info(f"[Pre-Routing] Intent extracted - location: '{new_location}', action: '{new_action}', full: {intent_data}")

            if new_location and new_action:
                current_location = settings.STATE.get("当前位置", "")
                if new_location != current_location:
                    logger.info(f"[Location State] Location changed: {current_location} -> {new_location}. Action: {new_action}")

                    self.state_manager._update_state({"当前位置": new_location, "当前行为": new_action}, logical_now=self.event_bus.logical_now)

                    def _update_location_bg():
                        try:
                            state = self.state_manager.current_state
                            pad_mood = ""
                            p = state.get("P", 5)
                            a = state.get("A", 5)
                            if p >= 7:
                                pad_mood = "开心地微笑着"
                            elif p >= 4:
                                pad_mood = "表情平静"
                            else:
                                pad_mood = "表情略显低落"
                            if a >= 7:
                                pad_mood += "，充满活力"
                            elif a <= 3:
                                pad_mood += "，略显慈惰"

                            inner = state.get("内心活动", "")
                            situation = state.get("客观情境", "")

                            prompt = (
                                f"High-quality anime background for a visual novel, style of Makoto Shinkai, "
                                f"scenery porn, vibrant colors, cinematic lighting, masterpiece, 2D art. "
                                f"Location: {new_location}. "
                                f"Environment details: {situation[:80] if situation else new_location}. "
                                f"Atmosphere: soft focus, atmospheric glow, high contrast between light and shadow. "
                                f"NO characters, background only, wide angle."
                            )
                            bg_url = self.llm_client.generate_image(prompt)
                            if bg_url:
                                logger.info(f"[Location State] Generated BG URL: {bg_url}")
                                self.state_manager._update_state({"背景图Url": bg_url}, logical_now=self.event_bus.logical_now)
                        except Exception as e:
                            logger.error(f"[Location State] Error generating background: {e}")

                    threading.Thread(target=_update_location_bg).start()
                else:
                    if settings.STATE.get("当前行为") != new_action:
                        self.state_manager._update_state({"当前行为": new_action}, logical_now=self.event_bus.logical_now)

            tool_calls = []
            if need_memory and memory_query:
                tool_calls.append(
                    {
                        "name": "memory_query",
                        "parameters": {
                            "query": memory_query,
                            "keywords": memory_keywords if memory_keywords else [],
                        },
                    }
                )
            if need_search and search_query:
                tool_calls.append(
                    {"name": "search_web", "parameters": {"query": search_query}}
                )

            if tool_calls:
                logger.info(f"[Pre-Routing] 判定执行前置工具: {tool_calls}")
                tool_results = self.tool_processor.execute_tool_calls(
                    tool_calls, caller="Pre-Routing"
                )
                dynamic_context = (
                    self.tool_processor.format_tool_results_for_prompt(tool_results)
                )

        except Exception as e:
            logger.error(f"[Pre-Routing] 意图识别或工具调用失败: {e}")
        # ==================================

        self.memory.update_base_prompt(
            self.tool_processor.build_system_prompt_with_tools(
                settings.SYSTEM_PROMPT
            )
        )

        self._llm_speak(self.memory, pack=True, memories=dynamic_context)

    def _process_queue(self):
        """FIFO 排空循环。同时检查 _is_processing 和 _is_speaking。"""
        while True:
            with self.lock:
                if self._is_processing or self._is_speaking:
                    return
                try:
                    msg = self._message_queue.get_nowait()
                except queue.Empty:
                    return
                self._is_processing = True

            try:
                self._execute_dialogue(msg)
            finally:
                with self.lock:
                    self._is_processing = False

    def process_dialogue(self, user_message):
        """直接处理对话（供外部调用和测试使用），内部转发到 _execute_dialogue"""
        with self.lock:
            if self._is_processing:
                logger.warning("正在处理中，忽略新输入")
                return
            self._is_processing = True

        try:
            self._execute_dialogue(user_message)
        finally:
            with self.lock:
                self._is_processing = False
            self._process_queue()

    def _stream_with_tag_gate(self, stream_gen, chunk_count: int, max_chunks: int):
        """流式读取 LLM 输出，屏蔽 <tool> 标签及其后的本轮内容。"""
        full_content = ""
        visible_buffer = []
        tag_buffer = ""
        inside_tag = False
        suppress_after_tool = False

        def flush_visible():
            nonlocal visible_buffer
            if visible_buffer and not suppress_after_tool:
                text = "".join(visible_buffer)
                self.event_bus.publish(Event(name="llm.chunk", data={"text": text}))
                visible_buffer = []

        for chunk in stream_gen:
            chunk_count += 1
            if chunk_count > max_chunks:
                logger.warning("LLM 输出超过最大限制，截断")
                break

            full_content += chunk

            for char in chunk:
                if suppress_after_tool:
                    continue

                if inside_tag:
                    tag_buffer += char
                    if char == ">":
                        normalized_tag = tag_buffer.strip().lower()
                        if normalized_tag in ("<thought>", "</thought>"):
                            visible_buffer.append(tag_buffer)
                        elif normalized_tag in ("<tool>", "</tool>"):
                            suppress_after_tool = True
                        elif normalized_tag.startswith("<speech") or normalized_tag.replace(" ", "").replace("\n", "") == "</speech>":
                            # <speech> 标签仅供 TTS 情感解析，不显示在前端
                            pass
                        else:
                            visible_buffer.append(tag_buffer)
                        tag_buffer = ""
                        inside_tag = False
                    continue

                if char == "<":
                    flush_visible()
                    inside_tag = True
                    tag_buffer = "<"
                    continue

                visible_buffer.append(char)

            if visible_buffer and not inside_tag and not suppress_after_tool:
                flush_visible()

        return full_content, chunk_count

    @staticmethod
    def _format_idle_duration(seconds: float) -> str:
        """用最大的时间单位表示空闲时长，并保留一位小数。"""
        seconds = max(0.0, float(seconds))
        if seconds >= 24 * 60 * 60:
            return f"{seconds / (24 * 60 * 60):.1f}天"
        if seconds >= 60 * 60:
            return f"{seconds / (60 * 60):.1f}小时"
        return f"{seconds / 60:.1f}分钟"

    def _llm_speak(
        self,
        memory,
        pack: bool = False,
        memories: str = "",
        idle_duration_seconds: float = None,
    ):
        # 立即发布 llm.started 事件，让前端尽早显示"正在思考"
        self.event_bus.publish(Event(name="llm.started", data=""))
        self._is_speaking = True

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
            if idle_duration_seconds is not None:
                idle_duration = self._format_idle_duration(idle_duration_seconds)
                history_parts.append(f"user: （{idle_duration}没有回复）\n")
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
            full_content, chunk_count = self._stream_with_tag_gate(
                stream_gen, chunk_count, max_chunks
            )
        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            error_msg = "[系统: AI 响应出现问题，请稍后再试]"
            self.event_bus.publish(Event(name="llm.chunk", data={"text": error_msg}))
            full_content = error_msg

        if full_content:
            # 允许多轮工具调用（最多 3 轮）
            max_tool_iterations = 3
            iteration = 0
            current_messages = messages.copy()

            while iteration < max_tool_iterations:
                iteration += 1

                # 检测并执行工具调用
                tool_calls = self.tool_processor.extract_tool_calls(full_content)

                if not tool_calls:
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
                        follow_up_content, chunk_count = self._stream_with_tag_gate(
                            stream_gen, chunk_count, max_chunks
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
                    retry_content, chunk_count = self._stream_with_tag_gate(
                        stream_gen, chunk_count, max_chunks
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
                self.memory.add_message("assistant", full_content, timestamp=self.event_bus.formatted_logical_now)
            except Exception as e:
                logger.error(f"保存消息失败: {e}")

            self.event_bus.publish(
                Event(name="llm.finished", data={"text": full_content})
            )
            self.event_bus.publish(
                Event(name="user_interaction", data=self.memory.get_memory())
            )

        # 说话结束后的清理，排空期间可能排队的新消息
        self._is_speaking = False
        self._process_queue()
