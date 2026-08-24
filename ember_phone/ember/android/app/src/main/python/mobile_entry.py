"""Stable Chaquopy facade for the Android-adapted Ember runtime."""

from __future__ import annotations

import json
import platform
import sys
from ember_mobile import MobileEmberRuntime


_runtime = MobileEmberRuntime()


def python_info() -> str:
    return json.dumps(
        {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "platform": sys.platform,
        },
        ensure_ascii=False,
    )


def echo(value: str) -> str:
    return f"Python 已收到：{value}"


def start_runtime(data_dir: str, cache_dir: str, event_callback=None) -> str:
    return _runtime.start(data_dir, cache_dir, event_callback)


def stop_runtime() -> str:
    return _runtime.stop()


def get_status_json() -> str:
    return _runtime.status_json()


def get_memory_overview() -> str:
    return _runtime.memory_overview_json()


def record_interaction() -> str:
    return _runtime.record_interaction_json()


def set_pad(pleasure: float, arousal: float, dominance: float) -> str:
    return _runtime.set_pad_json(pleasure, arousal, dominance)


def get_llm_config() -> str:
    return _runtime.config_json()


def update_app_config(config_json: str) -> str:
    return _runtime.update_app_config_json(config_json)


def update_llm_config(
    base_url: str,
    model: str,
    small_model: str,
    api_key: str | None,
    temperature: float,
    state_updates_enabled: bool,
) -> str:
    return _runtime.update_config_json(
        base_url,
        model,
        small_model,
        api_key,
        temperature,
        state_updates_enabled,
    )


def send_message(text: str) -> str:
    return _runtime.send_message_json(text)


def send_message_stream(text: str, image_size: str | None, event_callback) -> str:
    return _runtime.send_message_stream_json(text, image_size, event_callback)


def get_chat_history() -> str:
    return _runtime.history_json()


def clear_chat_history() -> str:
    return _runtime.clear_history_json()


def list_archives() -> str:
    return _runtime.list_archives_json()


def create_archive(name: str) -> str:
    return _runtime.create_archive_json(name)


def load_archive(archive_id: str) -> str:
    return _runtime.load_archive_json(archive_id)


def delete_archive(archive_id: str) -> str:
    return _runtime.delete_archive_json(archive_id)
