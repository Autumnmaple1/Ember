import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from brain.dialogue_context import build_dialogue_messages
from core.event_bus import Event
from core.proactive import ProactivePolicy
from core.time_utils import to_epoch
from tests.test_turn_cancellation import make_brain
from tests.test_persistence_scene import make_state_manager


def test_proactive_cooldown_is_real_time_persistent_and_not_reset_by_new_input(tmp_path):
    clock = [1000.0]
    path = tmp_path/'proactive.json'
    history = [{'role': 'user', 'content': '合成问题'}, {'role': 'assistant', 'content': '合成正常回答'}]
    policy = ProactivePolicy(600, path, lambda: clock[0])
    assert policy.reserve(history)
    clock[0] += 60
    reloaded = ProactivePolicy(600, path, lambda: clock[0])
    assert reloaded.check(history)['reason'] == 'cooldown'
    assert reloaded.check(history + [{'role': 'user', 'content': '新的合成消息'}])['retry_after_seconds'] == 540
    clock[0] += 540
    assert reloaded.check(history)['allowed']


@pytest.mark.parametrize('tail', [
    [{'role': 'assistant', 'content': '主动补充', 'turn_source': 'proactive'}],
    [{'role': 'assistant', 'content': '旧正常回答'}, {'role': 'assistant', 'content': '旧主动补充'}],
])
def test_unanswered_new_and_legacy_proactive_messages_do_not_repeat(tmp_path, tail):
    policy = ProactivePolicy(0, tmp_path/'proactive.json')
    history = [{'role': 'user', 'content': '原问题'}, *tail]
    assert policy.check(history)['reason'] == 'awaiting_user'
    assert policy.check(history + [{'role': 'user', 'content': '我回来了'}])['allowed']


def test_repeated_idle_events_do_not_make_a_self_reply_loop():
    brain, bus, memory, _state = make_brain()
    clock = [1000.0]
    brain.proactive_policy.clock = lambda: clock[0]
    brain.llm_client.stream_chat.side_effect = lambda **kw: iter(['<response>合成回复</response>'])
    brain.process_dialogue('合成问题')
    with patch('brain.core.threading.Thread', side_effect=lambda **kw: SimpleNamespace(start=kw['target'])):
        bus.publish(Event('idle_speak', {}))
        assert memory.get_memory()['history'][-1]['turn_source'] == 'proactive'
        bus.publish(Event('idle_speak', {}))
        clock[0] += 1000
        bus.publish(Event('idle_speak', {}))
        assert brain.llm_client.stream_chat.call_count == 2
        brain.process_dialogue('用户明确继续')
        bus.publish(Event('idle_speak', {}))
    assert brain.llm_client.stream_chat.call_count == 4


def test_corrupt_proactive_ledger_is_preserved_and_cannot_trigger_messages(tmp_path):
    path = tmp_path/'proactive.json'; path.write_text('broken')
    policy = ProactivePolicy(600, path)
    assert policy.check([])['reason'] == 'storage_error'
    assert not policy.reserve([])
    assert path.read_text() == 'broken'


def test_invented_inner_monologue_is_not_used_as_a_reply_script():
    state = make_state_manager()
    state.current_state['内心活动'] = '合成的自卑脚本标记'
    state.current_state['近期综合轨迹'] = '合成的反复自责标记'
    assert '合成的自卑脚本标记' not in state.prompt_injection
    assert '合成的反复自责标记' not in state.prompt_injection
    assert '合成的自卑脚本标记' in state.state_zip_full
    assert state.current_state['内心活动'] == '合成的自卑脚本标记'


@pytest.mark.parametrize('text,repair', [('你说话怎么怪怪的', True), ('好ai的回答', True), ('有没有好AI模型推荐', False)])
def test_legacy_style_feedback_remains_available_for_rollback(text, repair):
    result = build_dialogue_messages('合成人设', [{'role': 'user', 'content': text}], '', to_epoch('2026-11-07 21:30:00'), policy_version='legacy')
    assert ('【表达反馈】' in result[0]['content']) is repair
    assert result[-1]['content'] == text
    assert '星期六' in result[0]['content'] and '2026-11-08 星期日' in result[0]['content']


def test_old_feedback_does_not_trigger_another_proactive_self_explanation():
    result = build_dialogue_messages('合成人设', [{'role': 'user', 'content': '你说话怪怪的'},
        {'role': 'assistant', 'content': '合成回应'}], '', to_epoch('2026-11-07 21:30:00'), proactive=True)
    assert '【表达反馈】' not in result[0]['content']
    assert '没有新内容可以沉默' in result[0]['content']
