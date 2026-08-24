from __future__ import annotations

import json
import re
import time
from http.client import IncompleteRead
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class MobileLLMError(RuntimeError):
    pass


class MobileLLMClient:
    @staticmethod
    def _request(config: dict[str, Any], messages: list[dict[str, str]], stream: bool):
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            raise MobileLLMError("请先在设置中填写 API Key")

        base_url = str(config.get("base_url", "")).rstrip("/")
        endpoint = (
            base_url
            if base_url.endswith("/chat/completions")
            else f"{base_url}/chat/completions"
        )
        payload = {
            "model": config["model"],
            "messages": messages,
            "temperature": config.get("temperature", 0.7),
            "stream": stream,
            "thinking": {"type": "disabled"},
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
            },
            method="POST",
        )

    @staticmethod
    def chat(config: dict[str, Any], messages: list[dict[str, str]]) -> str:
        request = MobileLLMClient._request(config, messages, stream=False)

        body = None
        last_error = "LLM 未返回有效内容"
        try:
            for attempt in range(3):
                with urlopen(request, timeout=90) as response:
                    incomplete = False
                    try:
                        raw = response.read()
                    except IncompleteRead as error:
                        raw = error.partial
                        incomplete = True
                    if not raw:
                        last_error = "LLM 连接提前关闭，未返回内容"
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    try:
                        body = json.loads(raw.decode("utf-8"))
                        break
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        if incomplete:
                            last_error = "LLM 响应不完整"
                            time.sleep(0.5 * (attempt + 1))
                            continue
                        raise
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise MobileLLMError(f"LLM HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise MobileLLMError(f"无法连接 LLM：{error.reason}") from error
        except (ValueError, OSError) as error:
            raise MobileLLMError(f"LLM 响应读取失败：{error}") from error

        if body is None:
            raise MobileLLMError(last_error)

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise MobileLLMError(
                f"LLM 返回格式不兼容：{json.dumps(body, ensure_ascii=False)[:500]}"
            ) from error
        if not isinstance(content, str):
            raise MobileLLMError("LLM 返回了空内容")
        return content

    @staticmethod
    def stream_chat(config: dict[str, Any], messages: list[dict[str, str]]):
        request = MobileLLMClient._request(config, messages, stream=True)
        received_content = False
        try:
            with urlopen(request, timeout=90) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        body = json.loads(data)
                        choices = body.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            received_content = True
                            yield content
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise MobileLLMError(f"LLM HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise MobileLLMError(f"无法连接 LLM：{error.reason}") from error
        except IncompleteRead as error:
            # Some OpenAI-compatible endpoints close an SSE response without
            # the final chunk marker. Content already emitted is still valid.
            if not received_content:
                raise MobileLLMError("LLM 流连接提前关闭，未返回内容") from error
        except OSError as error:
            raise MobileLLMError(f"LLM 流读取失败：{error}") from error

    @staticmethod
    def embedding(config: dict[str, Any], text: str) -> list[float]:
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            raise MobileLLMError("请先在设置中填写 Embedding API Key")
        base_url = str(config.get("base_url", "")).rstrip("/")
        endpoint = base_url if base_url.endswith("/embeddings") else f"{base_url}/embeddings"
        request = Request(
            endpoint,
            data=json.dumps(
                {"model": config["model"], "input": text},
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
            vector = body["data"][0]["embedding"]
            if not isinstance(vector, list) or not vector:
                raise ValueError("empty embedding")
            return [float(value) for value in vector]
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise MobileLLMError(
                f"Embedding HTTP {error.code}: {detail}"
            ) from error
        except URLError as error:
            raise MobileLLMError(f"无法连接 Embedding：{error.reason}") from error
        except (KeyError, IndexError, TypeError, ValueError, OSError) as error:
            raise MobileLLMError(f"Embedding 响应读取失败：{error}") from error

    @staticmethod
    def generate_image(
        config: dict[str, Any],
        prompt: str,
        size: str = "2688*1536",
    ) -> str | None:
        """调用 DashScope 千问图像模型生成背景图，失败返回 None。"""
        api_key = str(config.get("api_key", "")).strip()
        model = str(config.get("model", "")).strip()
        if not api_key or not model:
            return None
        if not re.fullmatch(r"\d{2,5}\*\d{2,5}", size or ""):
            size = "2688*1536"
        base_url = str(config.get("base_url", "")).strip().rstrip("/")
        if not base_url.endswith("/generation"):
            base_url = (
                "https://dashscope.aliyuncs.com/api/v1/services/"
                "aigc/multimodal-generation/generation"
            )
        payload = {
            "model": model,
            "input": {
                "messages": [{"role": "user", "content": [{"text": prompt}]}]
            },
            "parameters": {
                "size": size,
                "negative_prompt": (
                    "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，"
                    "人脸无细节，过度光滑，画面具有AI感，构图混乱。"
                ),
                "prompt_extend": True,
                "watermark": False,
            },
        }
        request = Request(
            base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "X-DashScope-Async": "disable",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            url = data["output"]["choices"][0]["message"]["content"][0]["image"]
            return str(url) if url else None
        except Exception:
            return None
