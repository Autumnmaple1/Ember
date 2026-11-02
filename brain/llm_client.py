from openai import OpenAI
import logging
import json
import threading
from json_repair import repair_json
from config.settings import settings
from core.turns import TurnCancelled
from brain.reasoning_filter import ReasoningFilter, remove_reasoning

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM 客户端（单例模式）

    所有模块共享同一个实例，复用 HTTP 连接池，减少 TLS 握手开销。
    内部维护三个独立的 OpenAI 客户端：
    - large_client: 大模型（用于对话生成）
    - small_client: 小模型（用于状态更新等轻量任务）
    - embedding_client: 嵌入模型（用于向量化）
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        # 避免重复初始化
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            logger.info("[LLMClient] 初始化连接池（单例模式）")
            self.large_client = OpenAI(
                api_key=settings.LARGE_LLM.api_key,
                base_url=settings.LARGE_LLM.base_url,
            )
            self.small_client = OpenAI(
                api_key=settings.SMALL_LLM.api_key,
                base_url=settings.SMALL_LLM.base_url,
            )
            self.embedding_client = OpenAI(
                api_key=settings.EMBEDDING_MODEL.api_key,
                base_url=settings.EMBEDDING_MODEL.base_url,
            )
            self._initialized = True

    @classmethod
    def _reset_instance(cls):
        """重置单例实例（仅用于测试）"""
        with cls._lock:
            cls._instance = None

    def _extract_json(self, content):
        if not content or not content.strip():
            logger.warning("LLM 返回空内容，无法解析 JSON")
            return None
        try:
            good_json_string = repair_json(content)
            data = json.loads(good_json_string)
            return data
        except (json.JSONDecodeError, Exception) as e:
            logger.error(
                f"JSON 解析失败: {e}, 原始内容: {content[:200] if content else 'None'}..."
            )
            return None

    def _log_usage(self, usage, model_name: str, call_type: str = "dialogue"):
        """记录 Token 消耗日志，兼容 OpenAI 和 Gemini 两种响应格式，包含缓存信息和调用类型

        call_type: dialogue(对话) | state_update(状态更新) | idle_evolve(闲置演化) | memory_query(记忆检索) | memory_encode(记忆编码)
        """
        if usage is None:
            logger.debug("[LLM Usage] usage 为空")
            return

        # 获取基础 token 数
        p = getattr(usage, "prompt_tokens", None)
        c = getattr(usage, "completion_tokens", None)

        # Gemini 风格: prompt_token_count / candidates_token_count
        if p is None:
            p = getattr(usage, "prompt_token_count", None)
        if c is None:
            c = getattr(usage, "candidates_token_count", None)

        try:
            p = int(p) if p is not None else 0
            c = int(c) if c is not None else 0
        except (TypeError, ValueError):
            p, c = 0, 0

        # 获取缓存命中信息
        cached_tokens = 0
        token_details = getattr(usage, "prompt_tokens_details", None)
        if token_details:
            cached = getattr(token_details, "cached_tokens", 0)
            try:
                cached_tokens = int(cached) if cached else 0
            except (TypeError, ValueError):
                cached_tokens = 0

        # 获取缓存创建信息
        cache_creation_tokens = 0
        cache_creation = getattr(usage, "cache_creation", None)
        if cache_creation:
            created = getattr(
                cache_creation, "ephemeral_5m_input_tokens", None
            ) or getattr(cache_creation, "cache_creation_input_tokens", None)
            try:
                cache_creation_tokens = int(created) if created else 0
            except (TypeError, ValueError):
                cache_creation_tokens = 0

        # 构建包含缓存信息的日志
        cache_info = ""
        if cached_tokens > 0:
            cache_info += f" | CachedHit: {cached_tokens}"
        if cache_creation_tokens > 0:
            cache_info += f" | CacheCreated: {cache_creation_tokens}"

        logger.info(
            f"[LLM Usage] Type: {call_type} | Model: {model_name} | Prompt: {p} | Completion: {c} | Total: {p + c}{cache_info}"
        )

    def one_chat(
        self, model_config, messages, timeout=60, call_type: str = "state_update"
    ):
        """单次对话，带超时和重试

        call_type: 用于区分调用来源，默认 state_update（非对话类调用）
        """
        client = (
            self.large_client
            if model_config == settings.LARGE_LLM
            else self.small_client
        )

        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model_config.name,
                    messages=messages,
                    extra_body={"enable_thinking": False},
                    stream=False,
                    temperature=settings.LLM_TEMPERATURE,
                    timeout=timeout,
                )
                full_response = response.choices[0].message.content
                usage = getattr(response, "usage", None) or getattr(
                    response, "usage_metadata", None
                )
                self._log_usage(usage, model_config.name, call_type)
                return remove_reasoning(full_response)
            except Exception as e:
                logger.error(
                    f"OneChat Error (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    return None
        return None

    def stream_chat(self, model_config, messages, call_type: str = "dialogue", cancel_token=None):
        """流式对话

        call_type: 用于区分调用来源，默认 dialogue（对话类调用）
        """
        client = (
            self.large_client
            if model_config == settings.LARGE_LLM
            else self.small_client
        )

        response = None
        try:
            if cancel_token:
                cancel_token.check()
            response = client.chat.completions.create(
                model=model_config.name,
                messages=messages,
                extra_body={"enable_thinking": False},
                stream=True,
                stream_options={"include_usage": True},
                temperature=settings.LLM_TEMPERATURE,
                timeout=60,
            )
            if cancel_token and hasattr(response, 'close'):
                cancel_token.attach_stream(response.close)

            last_usage = None
            public_content = ReasoningFilter()
            for chunk in response:
                if cancel_token:
                    cancel_token.check()
                if getattr(chunk, "usage", None):
                    last_usage = chunk.usage

                # include_usage=True 时最后一个 chunk choices 为空列表，跳过
                if not chunk.choices:
                    continue

                # reasoning_content/reasoning are private provider channels.
                # Some gateways ignore enable_thinking=False or put <think>
                # blocks in content; filter before any downstream consumer.
                content = getattr(chunk.choices[0].delta, 'content', None)
                if isinstance(content, str):
                    visible = public_content.feed(content)
                    if visible:
                        yield visible

            if cancel_token:
                cancel_token.check()
            usage = (
                last_usage
                or getattr(response, "usage", None)
                or getattr(response, "usage_metadata", None)
            )
            self._log_usage(usage, model_config.name, call_type)

        except TurnCancelled:
            raise
        except Exception as e:
            if cancel_token:
                cancel_token.check()
                raise
            yield f"[Error]: {str(e)}"
            logger.error(f"Streaming Chat Error: {e}")
        finally:
            if cancel_token:
                cancel_token.detach_stream()
            if response is not None and hasattr(response, 'close'):
                response.close()

    def get_embedding(self, model_config, text: str):
        client = self.embedding_client
        try:
            response = client.embeddings.create(
                model=model_config.name,
                input=text,
                dimensions=1536,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Get Embedding Error: {e}")
            return None
