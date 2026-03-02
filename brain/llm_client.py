from openai import OpenAI
import logging
from config.settings import settings

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self):
        # 复用 OpenAI 客户端实例，减少重复实例化开销
        self.large_client = OpenAI(
            api_key=settings.LARGE_LLM.api_key,
            base_url=settings.LARGE_LLM.base_url,
        )
        self.small_client = OpenAI(
            api_key=settings.SMALL_LLM.api_key,
            base_url=settings.SMALL_LLM.base_url,
        )

    def one_chat(self, model_config, messages):
        client = (
            self.large_client
            if model_config == settings.LARGE_LLM
            else self.small_client
        )

        try:
            response = client.chat.completions.create(
                model=model_config.name,
                messages=messages,
                extra_body={"enable_thinking": False},
                stream=False,
                temperature=0.3,
            )
            full_response = response.choices[0].message.content
            return full_response
        except Exception as e:
            logger.error(f"OneChat Error: {e}")
            return None

    def stream_chat(self, model_config, messages):
        client = (
            self.large_client
            if model_config == settings.LARGE_LLM
            else self.small_client
        )

        try:
            response = client.chat.completions.create(
                model=model_config.name,
                messages=messages,
                extra_body={"enable_thinking": False},
                stream=True,
                temperature=0.7,
            )

            for chunk in response:
                # 捕获推理/思考过程 (Reasoning Content)
                reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
                if reasoning:
                    yield f"{reasoning}"

                content = chunk.choices[0].delta.content
                if content is not None:
                    yield content

        except Exception as e:
            yield f"[发生内部错误: {e}]"
