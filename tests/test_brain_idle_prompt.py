from unittest.mock import Mock, patch

from brain.core import Brain
from core.event_bus import Event, EventBus


def _make_brain():
    event_bus = EventBus()
    state_manager = Mock()
    state_manager.prompt_injection = ""
    memory = Mock()
    memory.get_full_messages.return_value = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "是聊天机器人那种吗？"},
    ]
    memory.get_memory.return_value = {"history": []}
    tool_processor = Mock()

    with patch("brain.core.LLMClient") as llm_class:
        llm_class.return_value.stream_chat.return_value = iter([])
        brain = Brain(
            event_bus,
            state_manager,
            memory,
            Mock(),
            tool_processor=tool_processor,
        )
        return brain, memory, llm_class.return_value


def test_idle_duration_uses_largest_unit_with_one_decimal():
    assert Brain._format_idle_duration(90) == "1.5分钟"
    assert Brain._format_idle_duration(90 * 60) == "1.5小时"
    assert Brain._format_idle_duration(36 * 60 * 60) == "1.5天"


def test_idle_silence_is_appended_as_user_history_line():
    brain, memory, llm_client = _make_brain()

    brain._llm_speak(
        memory,
        pack=True,
        memories="",
        idle_duration_seconds=90 * 60,
    )

    messages = llm_client.stream_chat.call_args.kwargs["messages"]
    assert "依鸣: 是聊天机器人那种吗？" in messages[1]["content"]
    assert "user: （1.5小时没有回复）" in messages[1]["content"]


def test_idle_event_duration_is_forwarded_to_prompt_builder():
    brain, _, _ = _make_brain()
    brain._llm_speak = Mock()

    brain._on_idle_speak(
        Event("idle_speak", data={"idle_duration_seconds": 120.0})
    )

    for _ in range(100):
        if brain._llm_speak.called:
            break
        import time

        time.sleep(0.01)

    assert brain._llm_speak.call_args.kwargs["idle_duration_seconds"] == 120.0
