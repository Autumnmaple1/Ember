from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from .chat_memory import MobileChatMemory
from .config_store import MobileConfigStore
from .event_bus import Event, EventBus
from .llm_client import MobileLLMClient
from .memory_manager import MobileMemoryManager
from .persona_prompt import SYSTEM_PROMPT
from .prompt_store import (
    PERSONA_SYSTEM_RULES,
    PRE_ROUTING_PROMPT,
    SYSTEM_RULES_PROMPT,
)
from .state_store import MobileStateStore
from .state_evolution import MobileStateEvolution
from .tool_system import create_mobile_tool_processor


SPEECH_PATTERN = re.compile(
    r'<speech\s+p=["\'](?P<p>[\d.]+)["\']\s+'
    r'a=["\'](?P<a>[\d.]+)["\']\s+'
    r'd=["\'](?P<d>[\d.]+)["\']\s*>(?P<text>.*?)</speech>',
    re.IGNORECASE | re.DOTALL,
)
THOUGHT_PATTERN = re.compile(
    r"<thought>(?P<text>.*?)</thought>",
    re.IGNORECASE | re.DOTALL,
)

logger = logging.getLogger(__name__)

_MOVE_VERB_RE = re.compile(
    r"去|到|回|来|换|搬|前往|去往|走进|来到|到达|出发|进入|转移|"
    r"传送|瞬移|穿越|离开|去趟|去逛|去玩|去海边|换到|转到|切到|"
    r"飞往|跑去|走去|回到|去到"
)


def _is_explicit_environment_change(user_message: str, location: str) -> bool:
    """只有用户明确表达“现在要切换场景”时才视为环境变化：
    消息里必须同时出现移动/转换动词，并且提到了该地点。"""
    message = (user_message or "").strip()
    location = (location or "").strip()
    if not message or not location:
        return False
    if _MOVE_VERB_RE.search(message) is None:
        return False
    return location in message


def _clean_background_scene(situation: str, location: str) -> str:
    """从客观情境中提取干净的环境描述，裁掉人物叙事，避免背景图出现角色。"""
    text = (situation or "").strip()
    for marker in ("。依鸣", "，依鸣", "。她", "，她", "。他", "，他", "依鸣正"):
        index = text.find(marker)
        if 0 < index <= 80:
            text = text[:index]
            break
    text = (
        text.replace("依鸣", "")
        .replace("她", "")
        .replace("他", "")
        .strip("，。 ")
    )
    return text[:80] or location


class MobileConversation:
    def __init__(
        self,
        event_bus: EventBus,
        state_store: MobileStateStore,
        config_store: MobileConfigStore,
        memory: MobileChatMemory,
        state_evolution: MobileStateEvolution,
        memory_manager: MobileMemoryManager,
        tool_processor=None,
    ):
        self._event_bus = event_bus
        self._state_store = state_store
        self._config_store = config_store
        self._memory = memory
        self._state_evolution = state_evolution
        self._memory_manager = memory_manager
        self._owns_tool_processor = tool_processor is None
        self._tool_processor = tool_processor or create_mobile_tool_processor(
            memory_manager
        )
        self._lock = threading.Lock()

    def _system_message(self) -> str:
        return self._effective_system_prompt()

    def _system_message_with_tools(self) -> str:
        return (
            f"{self._effective_system_prompt()}\n\n"
            f"{self._tool_processor.registry.prompt()}\n"
        )

    def _effective_system_prompt(self) -> str:
        try:
            persona = str(
                self._config_store.private_value().get("persona", "") or ""
            ).strip()
        except Exception:
            persona = ""
        if not persona:
            return SYSTEM_PROMPT
        # 与电脑端一致：SYSTEM_PROMPT = CORE_PERSONA + system_prompt，
        # 自定义人设时替换 CORE_PERSONA 的身份部分，但保留
        # PAD 情感模型等不可改动的系统机制（persona_system_rules）。
        return (
            f"{persona}\n\n{PERSONA_SYSTEM_RULES}\n\n{SYSTEM_RULES_PROMPT}"
        )

    def _character_name(self) -> str:
        try:
            name = str(
                self._config_store.private_value().get("character_name", "依鸣")
            ).strip()
            return name or "依鸣"
        except Exception:
            return "依鸣"

    def _user_name(self) -> str:
        try:
            name = str(
                self._config_store.private_value().get("user_name", "用户")
            ).strip()
            return name or "用户"
        except Exception:
            return "用户"

    def _state_prompt_injection(self) -> str:
        snapshot = self._state_store.snapshot()
        state = snapshot["state"]
        pad = (
            f"P:{state.get('P', 5)} A:{state.get('A', 5)} "
            f"D:{state.get('D', 5)}"
        )
        state_zip = (
            f"\n[状态 {state.get('对应时间', '')} | {pad}]\n"
            f"位置:{state.get('当前位置', '常规位置')} "
            f"(正在:{state.get('当前行为', '无特定行为')})\n"
            f"情境:{state.get('客观情境', '')}\n"
            f"内心:{state.get('内心活动', '')}\n"
            f"目标:{state.get('近期目标', '')}\n"
        )
        state_zip_full = (
            f"{state_zip}\n"
            f"近期综合轨迹:{state.get('近期综合轨迹', '')}\n"
        )
        return f"\n\n【角色的先前状态】\n{state_zip_full}\n\n"

    @staticmethod
    def _format_idle_duration(seconds: float) -> str:
        if seconds >= 24 * 60 * 60:
            return f"{seconds / (24 * 60 * 60):.1f}天"
        if seconds >= 60 * 60:
            return f"{seconds / (60 * 60):.1f}小时"
        return f"{seconds / 60:.1f}分钟"

    def _packed_messages(
        self,
        dynamic_context: str = "",
        idle_seconds: float | None = None,
        include_tools: bool = True,
    ) -> list[dict[str, str]]:
        history_parts = []
        character_name = self._character_name()
        user_name = self._user_name()
        for message in self._memory.snapshot():
            role_label = user_name if message.get("role") == "user" else character_name
            content = message.get("content", "")
            timestamp = message.get("timestamp", "")
            if timestamp:
                try:
                    content = f"[{timestamp[5:16]}] {content}"
                except Exception:
                    pass
            history_parts.append(f"{role_label}: {content}\n")
        if idle_seconds is not None:
            idle_duration = self._format_idle_duration(idle_seconds)
            history_parts.append(f"{user_name}: （{idle_duration}没有回复）\n")
        memories = (
            f"\n\n[脑海闪现的记忆]：\n{dynamic_context}\n"
            if dynamic_context
            else ""
        )
        user_content = (
            "以下是对话历史：\n"
            f"{''.join(history_parts)}{memories}{self._state_prompt_injection()}"
            f"现在的时间是{self._event_bus.formatted_logical_now}，"
            "请参考并结合状态生成你将要说的下一句话"
        )
        return [
            {
                "role": "system",
                "content": (
                    self._system_message_with_tools()
                    if include_tools
                    else self._system_message()
                ),
            },
            {"role": "user", "content": user_content},
        ]

    def _publish_tool_event(
        self,
        event_type: str,
        iteration: int,
        **data,
    ) -> None:
        self._event_bus.publish(
            Event(event_type, {"iteration": iteration, **data})
        )

    def _execute_tool_calls(
        self,
        calls: list[dict[str, Any]],
        iteration: int,
        emit=None,
    ) -> list[dict[str, Any]]:
        public_calls = [
            {"name": item["name"], "parameters": item.get("parameters", {})}
            for item in calls
        ]
        self._publish_tool_event(
            "tool.started", iteration, calls=public_calls
        )
        if emit is not None:
            emit("tool.started", iteration=iteration, calls=public_calls)
        results = self._tool_processor.execute(calls)
        public_results = self._tool_processor.public_results(results)
        self._publish_tool_event(
            "tool.finished", iteration, results=public_results
        )
        if emit is not None:
            emit("tool.finished", iteration=iteration, results=public_results)
        return results

    @staticmethod
    def _extract_json_object(raw: str) -> dict[str, Any]:
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I
        )
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.S)
            if match is None:
                return {}
            value = json.loads(match.group(0))
        return value if isinstance(value, dict) else {}

    def _pre_route(
        self,
        user_message: str,
        emit=None,
        image_size: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        try:
            raw = MobileLLMClient.chat(
                self._config_store.state_model_value(),
                [
                    {"role": "system", "content": PRE_ROUTING_PROMPT},
                    {
                        "role": "user",
                        "content": f"用户最新输入：{user_message}",
                    },
                ],
            )
            intent = self._extract_json_object(raw)
            location = str(intent.get("location", "")).strip()
            action = str(intent.get("action", "")).strip()
            if location and action:
                current = self._state_store.snapshot()["state"]
                updates = {}
                if location != current.get("当前位置", ""):
                    updates["当前位置"] = location
                    updates["当前行为"] = action
                    if _is_explicit_environment_change(user_message, location):
                        self._generate_background(
                            location, action, emit, image_size
                        )
                    else:
                        logger.info(
                            "[Location State] 未检测到明确环境变化，跳过背景生成: %s",
                            location,
                        )
                elif action != current.get("当前行为", ""):
                    updates["当前行为"] = action
                if updates:
                    self._state_store.update_fields(updates)

            calls = []
            memory_query = str(intent.get("memory_query", "")).strip()
            memory_keywords = intent.get("memory_keywords", [])
            if not isinstance(memory_keywords, list):
                memory_keywords = []
            if intent.get("need_memory", False) and memory_query:
                calls.append(
                    {
                        "name": "memory_query",
                        "parameters": {
                            "query": memory_query,
                            "keywords": memory_keywords,
                        },
                    }
                )
            search_query = str(intent.get("search_query", "")).strip()
            if intent.get("need_search", False) and search_query:
                calls.append(
                    {"name": "search_web", "parameters": {"query": search_query}}
                )
            if not calls:
                return "", []
            results = self._execute_tool_calls(calls, 0, emit)
            return (
                self._tool_processor.results_prompt(results),
                self._tool_processor.public_results(results),
            )
        except Exception:
            return "", []

    def _generate_background(
        self,
        location: str,
        action: str,
        emit=None,
        image_size: str | None = None,
    ) -> None:
        """位置变化时异步生成一张干净的二次元背景，写入状态并通知前端。"""

        def _run() -> None:
            try:
                config = self._config_store.private_value()
                if not config.get("image_generation_enabled", False):
                    return
                image_config = {
                    "api_key": config.get("image_generation_api_key", ""),
                    "base_url": config.get("image_generation_base_url", ""),
                    "model": config.get("image_generation_model", ""),
                }
                if not image_config["api_key"] or not image_config["model"]:
                    return
                situation = str(
                    self._state_store.snapshot()["state"].get("客观情境", "")
                )
                scene = _clean_background_scene(situation, location)
                prompt = (
                    "二次元清新治愈系动漫背景原画，纯背景、画面干净通透，"
                    "高质量 2D 插画。"
                    f"场景：{location}，{scene}。"
                    "构图：这是放在 Live2D 角色立绘背后的背景图，"
                    "角色站立区域（画面中心偏下）保持干净留白，"
                    "主要景物分布在画面上部与左右两侧，不要遮挡人物主体区域；"
                    "横版宽画幅，柔光，浅色清新色调，轻微景深，干净利落的线条。"
                    "禁止出现人物、剪影、文字、水印、UI 元素或 logo，"
                    "背景中不得出现任何角色。"
                )
                requested_size = str(image_size or "").strip() or "2688*1536"
                url = MobileLLMClient.generate_image(
                    image_config,
                    prompt,
                    size=requested_size,
                )
                if not url and requested_size != "2688*1536":
                    # 个别接口可能不接收任意比例，回退到 16:9 再试一次。
                    url = MobileLLMClient.generate_image(
                        image_config,
                        prompt,
                        size="2688*1536",
                    )
                if not url:
                    return
                snapshot = self._state_store.update_fields({"背景图Url": url})
                if emit is not None:
                    emit("state.updated", snapshot=snapshot)
            except Exception:
                return

        threading.Thread(target=_run, daemon=True).start()

    def _chat_with_tools(
        self,
        messages: list[dict[str, str]],
        emit=None,
    ) -> tuple[str, list[dict[str, Any]]]:
        working_messages = list(messages)
        trace: list[dict[str, Any]] = []
        max_iterations = 3
        for iteration in range(1, max_iterations + 1):
            raw = MobileLLMClient.chat(
                self._config_store.private_value(), working_messages
            )
            calls = self._tool_processor.extract(raw)
            if not calls:
                return raw, trace
            results = self._execute_tool_calls(calls, iteration, emit)
            trace.extend(self._tool_processor.public_results(results))
            working_messages.extend(
                [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"{self._tool_processor.results_prompt(results)}\n\n"
                            "【重要】你现在已经获取了所需的信息，请立刻生成你的回复。\n"
                            "【重要】不要再调用任何工具！直接根据工具结果和提示词要求生成回复！"
                        ),
                    },
                ]
            )

        retry_count = 0
        while self._tool_processor.extract(raw) and retry_count < 2:
            retry_count += 1
            retry_messages = working_messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "你的回复中包含了工具调用标签，这是不允许的。"
                        "请直接给出你的回复，不要使用任何工具调用。"
                    ),
                },
            ]
            raw = MobileLLMClient.chat(
                self._config_store.private_value(), retry_messages
            )
        return self._tool_processor.remove_calls(raw), trace

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        thought = "".join(
            match.group("text").strip()
            for match in THOUGHT_PATTERN.finditer(raw)
        ).strip()
        matches = list(SPEECH_PATTERN.finditer(raw))
        if matches:
            speech = "".join(match.group("text").strip() for match in matches).strip()
            last = matches[-1]
            pad = {
                "P": float(last.group("p")),
                "A": float(last.group("a")),
                "D": float(last.group("d")),
            }
        else:
            speech = THOUGHT_PATTERN.sub("", raw).strip()
            speech = re.sub(r"</?speech[^>]*>", "", speech).strip()
            pad = None
        return {
            "speech": speech,
            "thought": thought,
            "speech_pad": pad,
            "raw": raw,
        }

    def send(self, text: str) -> dict[str, Any]:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("消息不能为空")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("依鸣正在回复上一条消息")

        try:
            timestamp = self._event_bus.formatted_logical_now
            self._memory.add("user", clean_text, timestamp)
            self._event_bus.publish(Event("user.input", {"text": clean_text}))
            self._event_bus.publish(Event("llm.started", {"timestamp": timestamp}))

            dynamic_context, routing_trace = self._pre_route(clean_text)
            messages = self._packed_messages(dynamic_context)
            raw, tool_trace = self._chat_with_tools(messages)
            tool_trace = routing_trace + tool_trace
            parsed = self._parse_response(raw)
            self._memory.add(
                "assistant",
                parsed["speech"],
                self._event_bus.formatted_logical_now,
            )
            self._state_store.record_interaction()
            state_update_error = None
            try:
                self._state_evolution.update_after_dialogue()
            except Exception as error:
                state_update_error = str(error)
            result = {
                **parsed,
                "timestamp": self._event_bus.formatted_logical_now,
                "state_update_error": state_update_error,
                "tools": tool_trace,
            }
            self._event_bus.publish(Event("llm.finished", result))
            return result
        finally:
            self._lock.release()

    def send_stream(
        self,
        text: str,
        event_callback,
        image_size: str | None = None,
    ) -> dict[str, Any]:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("消息不能为空")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("依鸣正在回复上一条消息")

        def emit(event_type: str, **data) -> None:
            event_callback.onEvent(
                json.dumps({"type": event_type, **data}, ensure_ascii=False)
            )

        try:
            timestamp = self._event_bus.formatted_logical_now
            self._memory.add("user", clean_text, timestamp)
            self._event_bus.publish(Event("user.input", {"text": clean_text}))
            self._event_bus.publish(Event("llm.started", {"timestamp": timestamp}))
            emit("started", timestamp=timestamp)

            dynamic_context, routing_trace = self._pre_route(
                clean_text, emit, image_size
            )
            messages = self._packed_messages(dynamic_context)
            working_messages = list(messages)
            tool_trace: list[dict[str, Any]] = list(routing_trace)
            parser = None
            for iteration in range(1, 4):
                parser = self._stream_round(working_messages, emit)
                calls = self._tool_processor.extract(parser.raw)
                if not calls:
                    break
                results = self._execute_tool_calls(calls, iteration, emit)
                tool_trace.extend(self._tool_processor.public_results(results))
                working_messages.extend(
                    [
                        {"role": "assistant", "content": parser.raw},
                        {
                            "role": "user",
                            "content": (
                                f"{self._tool_processor.results_prompt(results)}\n\n"
                                "【重要】你现在已经获取了所需的信息，请立刻生成你的回复。\n"
                                "【重要】不要再调用任何工具！直接根据工具结果和提示词要求生成回复！"
                            ),
                        },
                    ]
                )
            else:
                retry_count = 0
                while (
                    self._tool_processor.extract(parser.raw)
                    and retry_count < 2
                ):
                    retry_count += 1
                    retry_messages = working_messages + [
                        {"role": "assistant", "content": parser.raw},
                        {
                            "role": "user",
                            "content": (
                                "你的回复中包含了工具调用标签，这是不允许的。"
                                "请直接给出你的回复，不要使用任何工具调用。"
                            ),
                        },
                    ]
                    parser = self._stream_round(retry_messages, emit)
                parser.raw = self._tool_processor.remove_calls(parser.raw)

            assert parser is not None
            parsed = self._parse_response(parser.raw)
            if not parser.emitted_text and parsed["speech"]:
                emit("chunk", text=parsed["speech"])
            self._memory.add(
                "assistant",
                parsed["speech"],
                self._event_bus.formatted_logical_now,
            )
            self._state_store.record_interaction()
            state_update_error = None
            try:
                emit("state.updating")
                state_snapshot = self._state_evolution.update_after_dialogue()
                if state_snapshot is not None:
                    emit("state.updated", snapshot=state_snapshot)
            except Exception as error:
                state_update_error = str(error)
                emit("state.error", message=state_update_error)
            result = {
                **parsed,
                "timestamp": self._event_bus.formatted_logical_now,
                "state_update_error": state_update_error,
                "tools": tool_trace,
            }
            self._event_bus.publish(Event("llm.finished", result))
            emit("finished", **result)
            return result
        except Exception as error:
            emit("error", message=str(error))
            raise
        finally:
            self._lock.release()

    def speak_due_to_idle(
        self,
        idle_seconds: float,
        should_cancel=None,
        tool_event_callback=None,
    ) -> dict[str, Any] | None:
        if not self._lock.acquire(blocking=False):
            return None
        try:
            messages = self._packed_messages(
                idle_seconds=idle_seconds,
                include_tools=False,
            )
            raw, tool_trace = self._chat_with_tools(
                messages,
                tool_event_callback,
            )
            if should_cancel is not None and should_cancel():
                return None
            parsed = self._parse_response(raw)
            if not parsed["speech"]:
                return None
            timestamp = self._event_bus.formatted_logical_now
            self._memory.add("assistant", parsed["speech"], timestamp)
            return {
                **parsed,
                "timestamp": timestamp,
                "idle": True,
                "tools": tool_trace,
            }
        finally:
            self._lock.release()

    def history(self) -> list[dict[str, Any]]:
        result = []
        for item in self._memory.snapshot():
            view = dict(item)
            if item.get("role") == "assistant":
                view.update(self._parse_response(str(item.get("content", ""))))
                view.pop("raw", None)
                view.pop("content", None)
            result.append(view)
        return result

    def clear_history(self) -> None:
        self._memory.clear()

    def close(self) -> None:
        if self._owns_tool_processor:
            self._tool_processor.shutdown()

    def _stream_round(self, messages, emit) -> "_SpeechStreamParser":
        parser = _SpeechStreamParser()
        emitted_thought = ""
        for raw_chunk in MobileLLMClient.stream_chat(
            self._config_store.private_value(), messages
        ):
            visible_chunks = parser.feed(raw_chunk)
            if parser.thought != emitted_thought:
                emitted_thought = parser.thought
                emit("thought", text=emitted_thought)
            for visible_chunk in visible_chunks:
                emit("chunk", text=visible_chunk)
                self._event_bus.publish(
                    Event("llm.chunk", {"text": visible_chunk})
                )
        return parser


class _SpeechStreamParser:
    _closing_tag = "</speech>"

    def __init__(self):
        self.raw = ""
        self._pending = ""
        self._inside_speech = False
        self.emitted_text = ""
        self.thought = ""

    def feed(self, chunk: str) -> list[str]:
        self.raw += chunk
        self._update_thought()
        self._pending += chunk
        output: list[str] = []

        while self._pending:
            if not self._inside_speech:
                opening = self._pending.lower().find("<speech")
                if opening < 0:
                    self._pending = self._pending[-7:]
                    break
                self._pending = self._pending[opening:]
                end = self._pending.find(">")
                if end < 0:
                    break
                self._pending = self._pending[end + 1 :]
                self._inside_speech = True
                continue

            closing = self._pending.lower().find(self._closing_tag)
            if closing >= 0:
                self._append_output(self._pending[:closing], output)
                self._pending = self._pending[closing + len(self._closing_tag) :]
                self._inside_speech = False
                continue

            held = self._closing_prefix_length(self._pending.lower())
            safe_end = len(self._pending) - held
            if safe_end > 0:
                self._append_output(self._pending[:safe_end], output)
                self._pending = self._pending[safe_end:]
            break
        return output

    def _update_thought(self) -> None:
        lowered = self.raw.lower()
        opening = lowered.find("<thought")
        if opening < 0:
            return
        content_start = self.raw.find(">", opening)
        if content_start < 0:
            return
        content_start += 1
        closing = lowered.find("</thought>", content_start)
        content_end = closing if closing >= 0 else len(self.raw)
        self.thought = self.raw[content_start:content_end].strip()

    def _append_output(self, value: str, output: list[str]) -> None:
        if not value:
            return
        self.emitted_text += value
        output.append(value)

    def _closing_prefix_length(self, value: str) -> int:
        maximum = min(len(value), len(self._closing_tag) - 1)
        for size in range(maximum, 0, -1):
            if value.endswith(self._closing_tag[:size]):
                return size
        return 0
