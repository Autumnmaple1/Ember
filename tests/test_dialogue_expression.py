"""Check provenance and context routing, never prescribe an emotional answer."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from brain.dialogue_context import build_dialogue_messages
from config.dialogue_policy import STOCK_REPLACEMENTS, compile_persona, validate_policy
from config.settings import settings
from core.time_utils import to_epoch
from persona.reply_view import current_reply_view, make_reply_view
from tests.test_persistence_scene import make_state_manager


NOW = to_epoch('2026-11-07 21:30:00')


def test_exact_default_adaptation_keeps_custom_persona_and_original_yaml():
    core = '合成自定义人物：不爱喝甜饮料。\n'
    source = '\n'.join(old for old, _new in STOCK_REPLACEMENTS) + '\n自定义规则：晚饭后想去散步。'
    compiled, count = compile_persona(core, source, 'expressive_v1')
    assert count == len(STOCK_REPLACEMENTS)
    assert core in compiled and '自定义规则：晚饭后想去散步。' in compiled
    assert all(old not in compiled for old, _new in STOCK_REPLACEMENTS)
    assert source.endswith('自定义规则：晚饭后想去散步。')
    assert compile_persona(core, source, 'legacy') == (core + source, 0)


@pytest.mark.parametrize('style', [{'symbols': 'none'}, {'actions': 'none'}, {'length': 'expansive'}])
def test_style_preferences_do_not_replace_user_persona(style):
    compiled, count = compile_persona('合成人物', '自定义交流方式', 'expressive_v1', style)
    assert '合成人物自定义交流方式' in compiled
    assert count == 0
    assert '表达偏好 expressive_v1' in compiled


@pytest.mark.parametrize('version,style', [('typo', {}), ('expressive_v1', []),
    ('expressive_v1', {'length': 'three_sentences'}), ('expressive_v1', {'unknown': True})])
def test_invalid_expression_configuration_reports_error(version, style):
    with pytest.raises(ValueError):
        validate_policy(version, style)


@pytest.mark.parametrize('text', ['你好ai啊哈哈', '你说话怎么怪怪的', '我认真说的，刚才真的有点难受'])
def test_feedback_does_not_select_a_fixed_reply_route(text):
    messages = build_dialogue_messages('合成人设', [{'role': 'user', 'content': text}], '', NOW,
        policy_version='expressive_v1')
    assert messages[-1] == {'role': 'user', 'content': text}
    assert '【表达反馈】' not in messages[0]['content']


def test_current_view_has_runtime_provenance_and_does_not_import_long_trajectory():
    manager = make_state_manager()
    manager.event_bus.restore_logical_time('2026-11-07 21:30:00')
    candidate = copy.deepcopy(manager.current_state)
    candidate.update({'客观情境': '宿舍内，整理笔记', '内心活动': '旧长独白不直接回灌', '近期综合轨迹': '旧轨迹标记'})
    candidate['reply_perspective'] = {'feeling': '有点不服气，也想听懂对方', 'intent': '说出自己的不同看法',
        'attention': '刚才那个分歧', 'timestamp': 1, 'source': 'invented', 'extra': '不应进入视角'}
    with patch.object(manager, '_ask_llm_with_tools', return_value=json.dumps(candidate)), \
            patch.object(manager.llm_client, '_extract_json', side_effect=json.loads):
        manager._generate_state('合成更新', NOW, user_text='合成输入')
    saved = manager.current_state['reply_perspective']
    assert saved['timestamp'] == int(NOW * 1000) and saved['source'] == 'dialogue'
    assert 'extra' not in saved
    assert saved['feeling'] in manager.prompt_injection
    assert '旧长独白不直接回灌' not in manager.prompt_injection
    assert '旧轨迹标记' not in manager.prompt_injection
    assert '旧轨迹标记' in manager.state_zip_full
    persisted = json.loads(Path('config/state.json').read_text(encoding='utf-8'))
    assert persisted['reply_perspective'] == saved


@pytest.mark.parametrize('offset', [-1, 1801])
def test_future_or_stale_view_is_not_current(offset):
    view = make_reply_view({'内心活动': '合成感受'}, NOW, 'dialogue')
    state = {'reply_perspective': view, 'scene_status': 'ready', 'state_source': 'interaction'}
    assert current_reply_view(state, NOW + offset) is None


def test_missing_view_never_relabels_stale_inner_monologue_as_fresh():
    manager = make_state_manager()
    manager.current_state['客观情境'] = '宿舍内，整理笔记'
    manager.current_state['reply_perspective'] = make_reply_view({'内心活动': '旧视角'}, NOW - 100, 'dialogue')
    candidate = {key: value for key, value in manager.current_state.items()
        if key not in ('reply_perspective', '内心活动', '近期目标')}
    with patch.object(manager, '_ask_llm_with_tools', return_value=json.dumps(candidate)), \
            patch.object(manager.llm_client, '_extract_json', side_effect=json.loads):
        manager._generate_state('合成更新', NOW)
    assert 'reply_perspective' not in manager.current_state
    assert '旧视角' not in manager.prompt_injection


def test_legacy_state_model_uses_only_fresh_candidate_and_bounds_optional_fields():
    view = make_reply_view({'内心活动': '合成感受' * 100, '近期目标': '尚未完成的打算'}, NOW, 'idle')
    assert len(view['feeling']) == 120
    assert view['intent'] == '尚未完成的打算' and view['source'] == 'idle'
    assert make_reply_view({'reply_perspective': {'feeling': [], 'intent': None}}, NOW, 'idle') is None


def test_fallback_and_legacy_mode_do_not_use_a_current_subjective_view(monkeypatch):
    manager = make_state_manager()
    manager.current_state['reply_perspective'] = make_reply_view({'内心活动': '仅视角标记'}, manager.event_bus.logical_now, 'dialogue')
    manager.current_state['scene_status'] = 'ready'
    assert '仅视角标记' in manager.reply_state_zip
    monkeypatch.setattr(settings, 'DIALOGUE_POLICY', 'legacy')
    assert '仅视角标记' not in manager.reply_state_zip
    monkeypatch.setattr(settings, 'DIALOGUE_POLICY', 'expressive_v1')
    manager._fallback(manager.event_bus.logical_now, '合成故障')
    assert '仅视角标记' not in manager.reply_state_zip


def test_state_view_prompt_shares_the_existing_model_request():
    manager = make_state_manager()
    manager.tool_processor.process_llm_output.return_value = {'has_tool_calls': False}
    with patch.object(manager.llm_client, 'one_chat', return_value='{}') as model:
        manager._ask_llm_with_tools('合成人设', '合成状态更新')
    assert model.call_count == 1
    assert 'reply_perspective' in model.call_args.args[1][-1]['content']
