"""
Pytest 配置文件和共享 fixtures
"""
import pytest
import sys
import os

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环用于异步测试"""
    import asyncio
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_state_file(tmp_path):
    """创建临时状态文件"""
    state_file = tmp_path / "test_state.json"
    default_state = {
        "P": 5,
        "A": 5,
        "D": 5,
        "客观情境": "测试场景",
        "近期综合轨迹": "测试轨迹",
        "内心活动": "测试中",
        "近期目标": "通过测试",
        "对应时间": "2026-03-13 12:00:00"
    }
    import json
    state_file.write_text(json.dumps(default_state, ensure_ascii=False), encoding='utf-8')
    return str(state_file)


@pytest.fixture
def mock_env(monkeypatch):
    """设置测试环境变量"""
    monkeypatch.setenv("LARGE_LLM_API_KEY", "test-key")
    monkeypatch.setenv("LARGE_LLM_BASE_URL", "http://test.local/v1")
    monkeypatch.setenv("LARGE_LLM_MODEL", "test-model")
    monkeypatch.setenv("SMALL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SMALL_LLM_BASE_URL", "http://test.local/v1")
    monkeypatch.setenv("SMALL_LLM_MODEL", "test-model")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://test.local/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setenv("PG_HOST", "localhost")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_USER", "test")
    monkeypatch.setenv("PG_PASSWORD", "test")
    monkeypatch.setenv("PG_DB", "test_db")
    monkeypatch.setenv("ENABLE_GRAPH_MEMORY", "False")
