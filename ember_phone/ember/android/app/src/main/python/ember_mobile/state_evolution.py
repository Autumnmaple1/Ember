from __future__ import annotations

import json
import re
from typing import Any

from .chat_memory import MobileChatMemory
from .config_store import MobileConfigStore
from .llm_client import MobileLLMClient
from .prompt_store import CORE_PERSONA, STATE_UPDATE_PROMPT, IDLE_STATE_UPDATE_PROMPT
from .state_store import MobileStateStore


class MobileStateEvolution:
    def __init__(
        self,
        state_store: MobileStateStore,
        config_store: MobileConfigStore,
        memory: MobileChatMemory,
        tool_processor=None,
    ):
        self._state_store = state_store
        self._config_store = config_store
        self._memory = memory
        self._tool_processor = tool_processor
        self._dialogue_count = 0

    @staticmethod
    def _format_duration(seconds: float) -> str:
        days = int(seconds // (24 * 3600))
        remainder = seconds % (24 * 3600)
        hours = int(remainder // 3600)
        remainder %= 3600
        minutes = int(remainder // 60)
        secs = int(remainder % 60)
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

    def _ask_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if self._tool_processor is None:
            return MobileLLMClient.chat(
                self._config_store.state_model_value(),
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        messages = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\n"
                    f"{self._tool_processor.registry.prompt()}\n"
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        response = MobileLLMClient.chat(
            self._config_store.state_model_value(), messages
        )
        # 与电脑端 StateManager 的工具处理器 max_calls=3 一致
        calls = self._tool_processor.extract(response)[:3]
        if not calls:
            return response
        results = self._tool_processor.execute(calls)
        messages.extend(
            [
                {"role": "assistant", "content": response},
                {
                    "role": "user",
                    "content": (
                        f"{self._tool_processor.results_prompt(results)}\n"
                        "请根据工具执行结果继续处理。"
                    ),
                },
            ]
        )
        return self._tool_processor.remove_calls(response)

    def update_after_dialogue(self) -> dict[str, Any] | None:
        public_config = self._config_store.public_value()
        if not public_config.get("state_updates_enabled", True):
            return None
        self._dialogue_count += 1
        interval = max(1, int(public_config.get("state_update_interval", 1)))
        if self._dialogue_count % interval != 0:
            return None

        snapshot = self._state_store.snapshot()
        history = []
        character_name = str(
            self._config_store.private_value().get("character_name", "依鸣")
        ).strip() or "依鸣"
        user_name = str(
            self._config_store.private_value().get("user_name", "用户")
        ).strip() or "用户"
        for message in self._memory.snapshot():
            role_name = user_name if message.get("role") == "user" else character_name
            history.append(
                {
                    "role": message.get("role"),
                    "content": f"{role_name}: {message.get('content', '')}",
                }
            )
        messages = [
            {"role": "system", "content": CORE_PERSONA},
            {
                "role": "user",
                "content": (
                    f"当前的准确时间: {snapshot['logical_time']}\n\n"
                    f"[先前状态]\n{json.dumps(snapshot['state'], ensure_ascii=False)}"
                    f"\n\n[近期对话记录]:\n"
                    f"{json.dumps(history, ensure_ascii=False)}\n\n"
                    f"{STATE_UPDATE_PROMPT}"
                ),
            },
        ]
        raw = self._ask_with_tools(
            messages[0]["content"], messages[1]["content"]
        )
        parsed = self._extract_object(raw)
        return self._state_store.update_fields(parsed)

    def reset_dialogue_count(self) -> None:
        self._dialogue_count = 0

    def update_due_to_idle(self, should_cancel=None) -> dict[str, Any] | None:
        public_config = self._config_store.public_value()
        if not public_config.get("state_updates_enabled", True):
            return None

        snapshot = self._state_store.snapshot()
        idle_duration = self._format_duration(self._state_store.idle_seconds)
        character_name = str(
            self._config_store.private_value().get("character_name", "依鸣")
        ).strip() or "依鸣"
        user_name = str(
            self._config_store.private_value().get("user_name", "用户")
        ).strip() or "用户"
        # 把真实对话打包成带说话人标注的转录文本，防止状态模型混淆说话人身份。
        history_lines = []
        for message in self._memory.snapshot():
            role = message.get("role")
            speaker = user_name if role == "user" else character_name
            content = str(message.get("content", "")).strip()
            if content:
                history_lines.append(f"{speaker}: {content}")
        history_text = "\n".join(history_lines) if history_lines else "（无）"
        user_content = (
            "【环境变更推断任务】\n"
            f"距离上次互动已经过去 {idle_duration} 分钟，"
            f"当前时间为 {snapshot['logical_time']}。\n"
            f"历史状态：{json.dumps(snapshot['state'], ensure_ascii=False)}\n"
            "[近期对话记录]:\n"
            f"{history_text}\n"
        )
        messages = [
            {"role": "system", "content": CORE_PERSONA},
            {
                "role": "user",
                "content": user_content + "\n\n" + IDLE_STATE_UPDATE_PROMPT,
            },
        ]
        print(
            "[EmberRuntime] idle prompt chars=%s history_lines=%s"
            % (len(user_content), len(history_lines))
        )
        raw = self._ask_with_tools(
            messages[0]["content"], messages[1]["content"]
        )
        if should_cancel is not None and should_cancel():
            return None
        parsed = self._extract_object(raw)
        pulse = parsed.pop("action_pulse", {})
        snapshot = self._state_store.update_fields(parsed)
        return {
            "snapshot": snapshot,
            "action_pulse": pulse if isinstance(pulse, dict) else {},
        }

    @staticmethod
    def _extract_object(raw: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.IGNORECASE)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise ValueError("状态模型没有返回 JSON 对象")
            value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise ValueError("状态模型返回值不是 JSON 对象")
        return value
