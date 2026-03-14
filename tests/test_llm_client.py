"""
LLM 客户端测试
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import json


class TestLLMClient:
    """测试 LLM 客户端"""

    @patch('openai.OpenAI')
    def test_init_creates_clients(self, mock_openai):
        """测试初始化创建三个客户端"""
        from brain.llm_client import LLMClient

        with patch('brain.llm_client.settings') as mock_settings:
            mock_settings.LARGE_LLM.api_key = "large_key"
            mock_settings.LARGE_LLM.base_url = "http://large.local"
            mock_settings.SMALL_LLM.api_key = "small_key"
            mock_settings.SMALL_LLM.base_url = "http://small.local"
            mock_settings.EMBEDDING_MODEL.api_key = "emb_key"
            mock_settings.EMBEDDING_MODEL.base_url = "http://emb.local"

            client = LLMClient()

            # 验证 OpenAI 被调用了 3 次
            assert mock_openai.call_count == 3

    @patch('openai.OpenAI')
    def test_one_chat_success(self, mock_openai):
        """测试单次对话成功"""
        from brain.llm_client import LLMClient

        # 设置 mock 响应
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "测试回复"

        # 设置 usage 对象以支持缓存检查
        mock_token_details = MagicMock()
        mock_token_details.cached_tokens = 0
        mock_usage = MagicMock()
        mock_usage.prompt_tokens_details = mock_token_details
        mock_response.usage = mock_usage

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        with patch('brain.llm_client.settings') as mock_settings:
            mock_settings.LARGE_LLM.api_key = "test"
            mock_settings.LARGE_LLM.base_url = "http://test"
            mock_settings.LARGE_LLM.name = "test-model"
            mock_settings.SMALL_LLM.api_key = "test"
            mock_settings.SMALL_LLM.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.api_key = "test"
            mock_settings.EMBEDDING_MODEL.base_url = "http://test"

            client = LLMClient()
            client.large_client = mock_client

            result = client.one_chat(
                mock_settings.LARGE_LLM,
                [{"role": "user", "content": "你好"}]
            )

            assert result == "测试回复"
            mock_client.chat.completions.create.assert_called_once()

    @patch('openai.OpenAI')
    def test_one_chat_error(self, mock_openai):
        """测试单次对话错误处理"""
        from brain.llm_client import LLMClient

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_openai.return_value = mock_client

        with patch('brain.llm_client.settings') as mock_settings:
            mock_settings.LARGE_LLM.api_key = "test"
            mock_settings.LARGE_LLM.base_url = "http://test"
            mock_settings.LARGE_LLM.name = "test-model"
            mock_settings.SMALL_LLM.api_key = "test"
            mock_settings.SMALL_LLM.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.api_key = "test"
            mock_settings.EMBEDDING_MODEL.base_url = "http://test"

            client = LLMClient()
            client.large_client = mock_client

            result = client.one_chat(
                mock_settings.LARGE_LLM,
                [{"role": "user", "content": "你好"}]
            )

            # 错误时返回 None
            assert result is None

    @patch('openai.OpenAI')
    def test_stream_chat(self, mock_openai):
        """测试流式对话"""
        from brain.llm_client import LLMClient

        # 模拟流式响应
        def mock_stream():
            chunks = [
                MagicMock(choices=[MagicMock(delta=MagicMock(content="你好", reasoning_content=None))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content="世界", reasoning_content=None))]),
            ]
            for chunk in chunks:
                yield chunk

            # 最后一个 chunk 包含 usage 信息
            final_chunk = MagicMock(choices=[MagicMock(delta=MagicMock(content=None, reasoning_content=None))])
            mock_token_details = MagicMock()
            mock_token_details.cached_tokens = 0
            mock_usage = MagicMock()
            mock_usage.prompt_tokens_details = mock_token_details
            final_chunk.usage = mock_usage
            yield final_chunk

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_stream()

        with patch('brain.llm_client.settings') as mock_settings:
            mock_settings.LARGE_LLM.api_key = "test"
            mock_settings.LARGE_LLM.base_url = "http://test"
            mock_settings.LARGE_LLM.name = "test-model"
            mock_settings.SMALL_LLM.api_key = "test"
            mock_settings.SMALL_LLM.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.api_key = "test"
            mock_settings.EMBEDDING_MODEL.base_url = "http://test"

            client = LLMClient()
            client.large_client = mock_client

            chunks = list(client.stream_chat(
                mock_settings.LARGE_LLM,
                [{"role": "user", "content": "你好"}]
            ))

            # 应该收到两个 content 块
            assert "你好" in chunks
            assert "世界" in chunks

    @patch('openai.OpenAI')
    def test_get_embedding(self, mock_openai):
        """测试获取 embedding"""
        from brain.llm_client import LLMClient

        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1, 0.2, 0.3]

        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = mock_response

        with patch('brain.llm_client.settings') as mock_settings:
            mock_settings.LARGE_LLM.api_key = "test"
            mock_settings.LARGE_LLM.base_url = "http://test"
            mock_settings.SMALL_LLM.api_key = "test"
            mock_settings.SMALL_LLM.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.api_key = "test"
            mock_settings.EMBEDDING_MODEL.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.name = "test-emb"

            client = LLMClient()
            client.embedding_client = mock_client

            result = client.get_embedding(
                mock_settings.EMBEDDING_MODEL,
                "测试文本"
            )

            assert result == [0.1, 0.2, 0.3]
            mock_client.embeddings.create.assert_called_once()

    @patch('openai.OpenAI')
    def test_get_embedding_error(self, mock_openai):
        """测试 embedding 错误处理"""
        from brain.llm_client import LLMClient

        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = Exception("Embedding Error")

        with patch('brain.llm_client.settings') as mock_settings:
            mock_settings.LARGE_LLM.api_key = "test"
            mock_settings.LARGE_LLM.base_url = "http://test"
            mock_settings.SMALL_LLM.api_key = "test"
            mock_settings.SMALL_LLM.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.api_key = "test"
            mock_settings.EMBEDDING_MODEL.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.name = "test-emb"

            client = LLMClient()
            client.embedding_client = mock_client

            result = client.get_embedding(
                mock_settings.EMBEDDING_MODEL,
                "测试文本"
            )

            # 错误时返回 None
            assert result is None

    def test_extract_json_valid(self):
        """测试提取有效 JSON"""
        from brain.llm_client import LLMClient
        from unittest.mock import patch

        with patch('brain.llm_client.settings') as mock_settings:
            mock_settings.LARGE_LLM.api_key = "test"
            mock_settings.LARGE_LLM.base_url = "http://test"
            mock_settings.SMALL_LLM.api_key = "test"
            mock_settings.SMALL_LLM.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.api_key = "test"
            mock_settings.EMBEDDING_MODEL.base_url = "http://test"

            client = LLMClient.__new__(LLMClient)
            result = client._extract_json('{"P": 5, "A": 3}')

            assert result == {"P": 5, "A": 3}

    def test_extract_json_with_markdown(self):
        """测试提取带 Markdown 的 JSON"""
        from brain.llm_client import LLMClient
        from unittest.mock import patch

        with patch('brain.llm_client.settings') as mock_settings:
            mock_settings.LARGE_LLM.api_key = "test"
            mock_settings.LARGE_LLM.base_url = "http://test"
            mock_settings.SMALL_LLM.api_key = "test"
            mock_settings.SMALL_LLM.base_url = "http://test"
            mock_settings.EMBEDDING_MODEL.api_key = "test"
            mock_settings.EMBEDDING_MODEL.base_url = "http://test"

            client = LLMClient.__new__(LLMClient)
            result = client._extract_json('```json\n{"P": 5}\n```')

            assert result == {"P": 5}
