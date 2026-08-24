from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .memory_db import MobileMemoryDatabase


class MobileChatMemory:
    def __init__(
        self,
        data_dir: Path,
        database: MobileMemoryDatabase,
        max_messages: int = 20,
    ):
        self._path = data_dir / "ember" / "chat_memory.json"
        self._database = database
        self._max_messages = max(2, int(max_messages))
        self._import_legacy_json()

    def _import_legacy_json(self) -> None:
        if self._database.chat_message_count() > 0:
            return
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(value, list):
                for item in value[-self._max_messages :]:
                    if not isinstance(item, dict):
                        continue
                    role = item.get("role")
                    content = item.get("content")
                    timestamp = item.get("timestamp", "")
                    if role in {"user", "assistant", "system"} and isinstance(
                        content, str
                    ):
                        self._database.add_chat_message(role, content, str(timestamp))
        except (OSError, ValueError, TypeError):
            pass

    def add(self, role: str, content: str, timestamp: str) -> None:
        self._database.add_chat_message(role, content, timestamp)
        self._database.trim_chat_messages(self._max_messages)

    def api_messages(self) -> list[dict[str, str]]:
        return [
            {"role": item["role"], "content": item["content"]}
            for item in self._database.get_chat_messages(self._max_messages)
            if item.get("role") in {"user", "assistant"}
        ]

    def snapshot(self) -> list[dict[str, Any]]:
        return self._database.get_chat_messages(self._max_messages)

    def clear(self) -> None:
        self._database.clear_chat_messages()

    def set_max_messages(self, value: int) -> None:
        self._max_messages = max(2, int(value))
        self._database.trim_chat_messages(self._max_messages)
