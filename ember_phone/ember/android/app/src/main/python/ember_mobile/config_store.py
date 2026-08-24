from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
from typing import Any

DEFAULT_LLM_CONFIG: dict[str, Any] = {
    "config_version": 2,
    "character_name": "依鸣",
    "user_name": "用户",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "small_model": "deepseek-v4-flash",
    "small_base_url": "https://api.deepseek.com",
    "small_api_key": "",
    "api_key": "",
    "temperature": 0.7,
    "state_updates_enabled": True,
    "embedding_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "embedding_model": "text-embedding-v4",
    "embedding_api_key": "",
    "image_generation_enabled": False,
    "image_generation_base_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    "image_generation_model": "qwen-image-2.0",
    "image_generation_api_key": "",
    "heartbeat_interval": 10.0,
    "time_accel_factor": 5.0,
    "time_flow_enabled": True,
    "state_idle_min_timeout": 40.0,
    "state_idle_max_timeout": 120.0,
    "context_window_size": 20,
    "state_update_interval": 1,
    "memory_encode_threshold": 5000,
    "memory_keep_last_lines": 5,
    "memory_decay_factor": 0.2,
    "recall_top_k": 5,
    "graph_memory_enabled": True,
}


class MobileConfigStore:
    def __init__(self, data_dir: Path):
        self._path = data_dir / "ember" / "llm_config.json"
        self._lock = threading.RLock()
        self._value = self._load()
        self._persist()

    def _load(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                if int(loaded.get("config_version", 1)) < 2:
                    if (
                        loaded.get("base_url")
                        == "https://dashscope.aliyuncs.com/compatible-mode/v1"
                        and loaded.get("model") == "qwen3.5-plus"
                    ):
                        loaded["base_url"] = "https://api.deepseek.com"
                        loaded["model"] = "deepseek-v4-flash"
                        loaded["small_model"] = "deepseek-v4-flash"
                    loaded["config_version"] = 2
                value = deepcopy(DEFAULT_LLM_CONFIG)
                value.update(loaded)
                return value
        except (OSError, ValueError, TypeError):
            pass
        return deepcopy(DEFAULT_LLM_CONFIG)

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self._path)

    def private_value(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._value)

    def public_value(self) -> dict[str, Any]:
        with self._lock:
            hidden_keys = {
                "api_key",
                "small_api_key",
                "embedding_api_key",
                "image_generation_api_key",
            }
            result = {
                key: deepcopy(value)
                for key, value in self._value.items()
                if key not in hidden_keys
            }
            result.update(
                {
                    "api_key_configured": bool(self._value.get("api_key")),
                    "small_api_key_configured": bool(
                        self._value.get("small_api_key")
                        or self._value.get("api_key")
                    ),
                    "embedding_api_key_configured": bool(
                        self._value.get("embedding_api_key")
                    ),
                    "image_generation_api_key_configured": bool(
                        self._value.get("image_generation_api_key")
                    ),
                }
            )
            return result

    def state_model_value(self) -> dict[str, Any]:
        with self._lock:
            value = deepcopy(self._value)
            # 与电脑端一致：只有配置了小模型 API Key 时才整体切换到小模型，
            # 否则整组使用主模型（模型名、BaseURL、API Key）。
            if value.get("small_api_key"):
                value["model"] = value.get("small_model") or value["model"]
                value["base_url"] = value.get("small_base_url") or value["base_url"]
                value["api_key"] = value.get("small_api_key")
            return value

    def embedding_model_value(self) -> dict[str, Any]:
        with self._lock:
            return {
                "base_url": self._value.get("embedding_base_url", ""),
                "model": self._value.get("embedding_model", ""),
                "api_key": self._value.get("embedding_api_key", ""),
            }

    def update_app_config(self, values: dict[str, Any]) -> dict[str, Any]:
        string_fields = {
            "base_url",
            "model",
            "small_base_url",
            "small_model",
            "embedding_base_url",
            "embedding_model",
            "image_generation_base_url",
            "image_generation_model",
            "character_name",
            "user_name",
        }
        secret_fields = {
            "api_key",
            "small_api_key",
            "embedding_api_key",
            "image_generation_api_key",
        }
        bool_fields = {
            "state_updates_enabled",
            "image_generation_enabled",
            "graph_memory_enabled",
            "time_flow_enabled",
        }
        numeric_ranges = {
            "temperature": (0.0, 2.0, float),
            "heartbeat_interval": (1.0, 3600.0, float),
            "time_accel_factor": (0.01, 1000.0, float),
            "state_idle_min_timeout": (5.0, 86400.0, float),
            "state_idle_max_timeout": (5.0, 604800.0, float),
            "context_window_size": (2, 200, int),
            "state_update_interval": (1, 100, int),
            "memory_encode_threshold": (100, 1000000, int),
            "memory_keep_last_lines": (0, 1000, int),
            "memory_decay_factor": (0.0, 1.0, float),
            "recall_top_k": (1, 100, int),
        }

        with self._lock:
            for field in string_fields:
                if field not in values:
                    continue
                text = str(values[field]).strip()
                if field.endswith("base_url"):
                    text = text.rstrip("/")
                if not text:
                    raise ValueError(f"{field} 不能为空")
                if field.endswith("base_url") and not text.startswith(
                    ("https://", "http://")
                ):
                    raise ValueError(f"{field} 必须以 http:// 或 https:// 开头")
                self._value[field] = text

            for field in secret_fields:
                text = str(values.get(field, "")).strip()
                if text:
                    self._value[field] = text

            for field in bool_fields:
                if field in values:
                    self._value[field] = bool(values[field])

            for field, (minimum, maximum, converter) in numeric_ranges.items():
                if field not in values:
                    continue
                number = converter(values[field])
                self._value[field] = converter(max(minimum, min(maximum, number)))

            if self._value["state_idle_max_timeout"] < self._value[
                "state_idle_min_timeout"
            ]:
                raise ValueError("空闲最大超时不能小于最小超时")

            self._persist()
            return self.public_value()

    def update(
        self,
        base_url: str,
        model: str,
        small_model: str,
        api_key: str | None = None,
        temperature: float = 0.7,
        state_updates_enabled: bool = True,
    ) -> dict[str, Any]:
        clean_url = base_url.strip().rstrip("/")
        clean_model = model.strip()
        if not clean_url.startswith(("https://", "http://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        if not clean_model:
            raise ValueError("model 不能为空")
        clean_small_model = small_model.strip() or clean_model

        with self._lock:
            self._value.update(
                {
                    "base_url": clean_url,
                    "model": clean_model,
                    "small_model": clean_small_model,
                    "temperature": max(0.0, min(2.0, float(temperature))),
                    "state_updates_enabled": bool(state_updates_enabled),
                }
            )
            if api_key is not None and api_key.strip():
                self._value["api_key"] = api_key.strip()
            self._persist()
            return self.public_value()
