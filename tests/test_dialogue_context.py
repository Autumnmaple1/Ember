"""Objective continuity checks; perceived warmth still needs human evaluation."""
import math
from unittest.mock import patch

import pytest

from brain.dialogue_context import build_dialogue_messages
from core.time_utils import to_epoch
from tests.test_persistence_scene import make_state_manager
from tests.test_turn_cancellation import make_brain


def message(role, text, when=None, **metadata):
    return {'role': role, 'content': text, 'timestamp': int(to_epoch(when) * 1000) if when else None, **metadata}


def build(history, when='2026-11-02 15:20:00', **kwargs):
    return build_dialogue_messages('合成角色设定', history, '合成场景', to_epoch(when), **kwargs)


def test_preserves_actual_roles_and_latest_input_without_task_wrapper():
    history = [message('user', '刚才那页我没看懂。', '2026-11-02 15:19:10'),
        message('assistant', '<thought>角色感受</thought>是哪一段？', '2026-11-02 15:19:20'),
        message('user', '第二段，为什么突然跳到结论？', '2026-11-02 15:20:00')]
    result = build(history)
    assert [x['role'] for x in result] == ['system', 'user', 'assistant', 'user']
    assert result[-1]['content'] == history[-1]['content']
    assert result[2]['content'] == '是哪一段？'
    assert '连续交谈' in result[0]['content']
    assert '角色感受' not in str(result)
    assert '合成场景' in result[0]['content'] and '2026-11-02 15:20:00' in result[0]['content']
    assert '以下是对话历史' not in result[-1]['content']


def test_old_relative_dates_stay_anchored_across_midnight():
    history = [message('user', '明天再看吧，晚安。', '2026-11-01 23:58:00'),
        message('assistant', '好，晚安。', '2026-11-01 23:58:03'),
        message('user', '接着看那一页。', '2026-11-02 15:20:00')]
    result = build(history)
    context = result[0]['content']
    assert '昨天' in context and '下午' in context and '跨越 1 个日期边界' in context
    assert '不证明此刻仍然如此' in context
    assert result[1]['content'] == '明天再看吧，晚安。'
    assert result[-1]['content'] == '接着看那一页。'


@pytest.mark.parametrize('stamp', [None, 'old date', True, math.nan, math.inf, 10**100])
def test_unknown_or_invalid_legacy_time_cannot_become_current_fact(stamp):
    result = build([{'role': 'user', 'content': '今天一起见面', 'timestamp': stamp}, message('user', '你好')])
    assert '未知，不能作为当日事实' in result[0]['content']
    assert '关系陌生或首次见面' in result[0]['content']


def test_rewound_world_does_not_call_future_history_just_now():
    result = build([message('user', '已经出门了', '2026-11-03 15:00:00'),
        message('user', '现在呢', '2026-11-02 15:20:00')])
    assert '时间回退' in result[0]['content']
    assert '未来时间' in result[0]['content']
    assert '连续交谈' not in result[0]['content']


def test_proactive_trigger_is_distinct_from_user_speech_and_context_not_duplicated():
    result = build([message('user', '我去忙一会', '2026-11-02 13:00:00')],
        proactive=True, proactive_instruction='合成主动发言规则')
    assert result[-1]['content'].startswith('【系统唤醒，非用户发言】')
    assert result[0]['content'].count('合成场景') == 1
    assert '合成主动发言规则' in result[0]['content']
    assert '上一条用户消息在 2026-11-02 13:00:00' in result[0]['content']


def test_real_brain_path_passes_native_turns_and_system_facts_to_model():
    brain, bus, memory, _state = make_brain()
    bus.restore_logical_time('2026-11-02 15:20:00')
    memory.add_message('user', '我在宿舍，用微信聊天。', timestamp=int(to_epoch('2026-11-02 15:19:00') * 1000))
    memory.add_message('assistant', '我这页还差一段。', timestamp=int(to_epoch('2026-11-02 15:19:10') * 1000))
    brain.llm_client.stream_chat.return_value = iter(['这段绕了点，我也卡了一会。'])
    brain.process_dialogue('哪一段？')
    messages = brain.llm_client.stream_chat.call_args.kwargs['messages']
    assert messages[-1] == {'role': 'user', 'content': '哪一段？'}
    assert messages[-2] == {'role': 'assistant', 'content': '我这页还差一段。'}
    assert '连续交谈' in messages[0]['content']


def test_preparation_failure_does_not_replace_character_intention_with_service_workflow():
    manager = make_state_manager()
    manager.current_state['客观情境'] = '宿舍里，整理笔记'
    manager.current_state['近期目标'] = '把这页最后一段弄清楚'
    manager._fallback(manager.event_bus.logical_now, 'synthetic failure')
    assert manager.current_state['近期目标'] == '把这页最后一段弄清楚'
    assert manager.current_state['scene_status'] == 'needs_confirmation'
    assert '未知位置' in manager.prompt_injection
    manager._fallback(manager.event_bus.logical_now, 'offline failure', offline=True)
    assert manager.current_state['近期目标'] == ''
