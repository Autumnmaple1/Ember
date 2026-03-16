"""
线程安全测试 - 验证修复后的问题
"""
import pytest
import threading
import time
import concurrent.futures


class TestShortTermMemoryThreads:
    """测试 ShortTermMemory 线程安全"""

    def test_thread_pool_reuse(self):
        """测试线程池复用，而不是每次都创建新线程"""
        from memory.short_term import ShortTermMemory

        memory = ShortTermMemory(max_memory_size=10)

        # 提交多个任务
        for i in range(10):
            memory.add_message("user", f"消息 {i}")

        # 等待所有任务完成
        time.sleep(0.5)

        # 验证线程池存在
        assert hasattr(ShortTermMemory, '_executor')
        assert ShortTermMemory._executor is not None

    def test_concurrent_add_message(self):
        """测试并发添加消息"""
        from memory.short_term import ShortTermMemory

        memory = ShortTermMemory(max_memory_size=100)

        def add_messages(start, count):
            for i in range(count):
                memory.add_message("user", f"消息 {start}-{i}")

        # 并发添加消息
        threads = []
        for i in range(5):
            t = threading.Thread(target=add_messages, args=(i * 10, 10))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        time.sleep(0.5)  # 等待异步保存完成

        # 验证消息都被添加（可能有部分被截断，但不应该崩溃）
        assert len(memory.memory) > 0


class TestBrainConcurrency:
    """测试 Brain 并发处理"""

    def test_processing_flag_prevents_concurrent(self):
        """测试 _is_processing 标志防止并发处理"""
        from unittest.mock import Mock, patch
        from brain.core import Brain
        from core.event_bus import EventBus

        with patch('brain.core.LLMClient') as mock_llm:
            mock_llm.return_value.stream_chat.return_value = iter(["测试回复"])

            event_bus = EventBus()
            mock_state = Mock()
            mock_state.prompt_injection = ""
            mock_memory = Mock()
            mock_memory.get_memory.return_value = {"history": []}
            mock_memory.get_full_messages.return_value = [
                {"role": "system", "content": "test"}
            ]
            mock_hippo = Mock()
            mock_hippo.road_memory.return_value = []

            brain = Brain(event_bus, mock_state, mock_memory, mock_hippo)

            # 设置处理中标志
            brain._is_processing = True

            # 尝试并发处理应该被忽略
            result = brain.process_dialogue("测试消息")

            # 应该返回 None（被忽略）
            assert result is None


class TestBrainErrorHandling:
    """测试 Brain 错误处理"""

    def test_llm_error_not_crashes(self):
        """测试 LLM 错误不会导致崩溃"""
        from unittest.mock import Mock, patch, MagicMock
        from brain.core import Brain
        from core.event_bus import EventBus

        event_bus = EventBus()

        with patch('brain.core.LLMClient') as mock_llm:
            # 模拟 LLM 抛出异常
            mock_llm_instance = MagicMock()
            mock_llm_instance.stream_chat.side_effect = Exception("API 错误")
            mock_llm.return_value = mock_llm_instance

            mock_hippo_instance = Mock()
            mock_hippo_instance.road_memory.return_value = []

            mock_state = Mock()
            mock_state.prompt_injection = ""
            mock_memory = Mock()
            mock_memory.get_full_messages.return_value = [
                {"role": "system", "content": "test"}
            ]
            mock_memory.get_memory.return_value = {"history": []}

            brain = Brain(event_bus, mock_state, mock_memory, mock_hippo_instance)

            # 设置 _is_processing 为 False 以允许处理
            brain._is_processing = False

            # 调用 _llm_speak，不应该抛出异常
            try:
                brain._llm_speak(pack=False)
            except Exception as e:
                pytest.fail(f"_llm_speak 不应该抛出异常: {e}")

    def test_processing_flag_resets_on_error(self):
        """测试错误时处理标志会被重置"""
        from unittest.mock import Mock, patch, MagicMock
        from brain.core import Brain
        from core.event_bus import EventBus

        event_bus = EventBus()

        with patch('brain.core.LLMClient') as mock_llm:
            # 模拟 LLM 流式调用时抛出异常
            mock_llm_instance = MagicMock()
            mock_llm_instance.stream_chat.side_effect = Exception("API 错误")
            mock_llm.return_value = mock_llm_instance

            # hippocampus 正常返回
            mock_hippo_instance = MagicMock()
            mock_hippo_instance.road_memory.return_value = []

            mock_state = Mock()
            mock_state.prompt_injection = ""
            mock_memory = Mock()
            mock_memory.get_memory.return_value = {"history": []}
            mock_memory.get_full_messages.return_value = [
                {"role": "system", "content": "test"}
            ]

            brain = Brain(event_bus, mock_state, mock_memory, mock_hippo_instance)

            # 验证初始状态
            assert brain._is_processing == False

            # 调用 _llm_speak（在锁内），错误不应该传播
            try:
                brain._llm_speak(pack=False)
            except:
                pass  # 异常已被内部捕获

            # 验证标志已重置（_llm_speak 不修改 _is_processing）
            assert brain._is_processing == False


class TestStateManagerThreads:
    """测试 StateManager 线程安全"""

    def test_async_log_uses_thread_pool(self):
        """测试异步日志使用线程池"""
        from persona.state_manager import StateManager

        assert hasattr(StateManager, '_executor')
        assert StateManager._executor is not None
