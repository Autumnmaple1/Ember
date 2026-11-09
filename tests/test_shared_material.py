"""Shared attention and return continuity; all IO uses suite isolation."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch
import uuid

import pytest
from fastapi.testclient import TestClient
from core.experience import Experience
from core.shared_material import continuation_items
from core.time_utils import to_epoch
from memory.postgres import connection
from persona.scene import expire_user_presence
from tests.test_persistence_scene import make_state_manager
from tests.test_restore_experience import make_server, finish


def user_source(text='条件是 a > 0。第二段只给出了这个不等式，没有归纳假设。'):
    return {'message_id': str(uuid.uuid4()), 'role': 'user', 'content': text,
        'timestamp': int(to_epoch('2026-11-02 15:20:00') * 1000), 'completion_status': 'completed'}


def test_shared_material_survives_restart_and_only_verbatim_quote_reaches_context():
    experience = Experience()
    source = user_source()
    result = experience.material('pin', source=source, quote='条件是 a > 0。', title='第二段', logical_time='2026-11-02 15:20:03')
    key = result['materials'][0]['id']
    assert experience.material('pin', source=source, quote='条件是 a > 0。')['materials'][0]['id'] == key
    restored = Experience()
    context = restored.prompt_context()
    assert '条件是 a > 0。' in context and '第二段只给出' not in context
    assert '未给出的段落' in context and '共享文字不表示双方此刻同处一地' in context
    assert '原消息' in context and continuation_items(restored.snapshot())[0]['source_id'] == source['message_id']
    assert '2026-11-02T15:20:00+08:00' in context
    restored.material('put_away', record_id=key)
    assert '条件是 a > 0。' not in restored.prompt_context()
    restored.material('focus', record_id=key)
    assert restored.snapshot()['materials'][0]['quote'] == '条件是 a > 0。'


@pytest.mark.parametrize('bad', ['书上还有归纳假设。', '', 'a' * 6001])
def test_unprovided_or_oversized_material_is_rejected_without_side_effects(bad):
    experience = Experience()
    before = experience.snapshot()
    with pytest.raises(ValueError):
        experience.material('pin', source=user_source(), quote=bad)
    assert experience.snapshot() == before


def test_assistant_output_is_not_promoted_to_user_provided_material():
    source = user_source()
    source['role'] = 'ai'
    with pytest.raises(ValueError):
        Experience().material('pin', source=source, quote=source['content'])


def test_focus_is_unique_and_disk_failure_rolls_back_both_materials_and_focus():
    experience = Experience()
    one = user_source('第一份材料')
    experience.material('pin', source=one, quote=one['content'])
    before = experience.snapshot()
    two = user_source('第二份材料')
    with patch.object(Path, 'replace', side_effect=OSError('synthetic disk failure')):
        with pytest.raises(OSError):
            experience.material('pin', source=two, quote=two['content'])
    assert experience.snapshot() == before
    result = experience.material('pin', source=two, quote=two['content'])
    assert [x['status'] for x in result['materials']] == ['stored', 'focused']
    raw = copy.deepcopy(experience.data)
    raw['materials'][0]['quote'] = '原消息没有的内容'
    experience.path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(ValueError, match='原文'):
        Experience()


def test_legacy_experience_and_return_items_do_not_invent_pending_tasks():
    Path('config/experience.json').write_text('{"version":1}', encoding='utf-8')
    experience = Experience()
    assert experience.snapshot()['materials'] == []
    assert continuation_items(experience.snapshot()) == []
    source = user_source('有空再一起看看，具体哪天未定。')
    experience.agreement('继续讨论', source)
    assert continuation_items(experience.snapshot()) == [], 'Mentioned is not confirmed'
    key = experience.snapshot()['agreements'][0]['id']
    experience.update_agreement(key, 'confirmed')
    assert '不自动代表今天到期' in continuation_items(experience.snapshot())[0]['detail']
    assert '未标记完成' in experience.prompt_context()
    experience.update_agreement(key, 'completed')
    assert continuation_items(experience.snapshot()) == []


def test_legacy_agreement_without_original_text_remains_unknown_and_does_not_break_replies():
    experience = Experience()
    result = experience.agreement('旧约定，日期待定', {'message_id': 'legacy-source'})
    experience.update_agreement(result['agreements'][0]['id'], 'confirmed')
    context = Experience().prompt_context()
    assert '旧约定，日期待定' in context and '"source_quote": ""' in context
    assert '"message_timestamp": null' in context
    assert '未知，不能作为当日事实' in context


def test_real_api_and_archive_restore_shared_attention_and_reject_old_world():
    server = make_server()
    client = TestClient(server.app)
    epoch = server.protocol.epoch
    source = user_source()
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute('INSERT INTO message_list(sender,text,timestamp,message_id) VALUES(%s,%s,to_timestamp(%s),%s)',
                ('user', source['content'], source['timestamp'] / 1000, source['message_id']))
        body = {'epoch': epoch, 'action': 'material_pin', 'message_id': source['message_id'], 'quote': source['content'], 'title': '待讨论原文'}
        response = client.post('/experience', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['continuation'][0]['title'] == '待讨论原文'
        assert '待讨论原文' in server.state_manager.prompt_injection
        assert client.post('/experience', json={**body, 'quote': '未提供的段落'}).status_code == 400
        assert client.post('/api/archive/tasks', json={'epoch': epoch, 'operation': 'create', 'slot_name': 'material'}).status_code == 202
        assert finish(client)['status'] == 'completed'
        key = response.json()['experience']['materials'][0]['id']
        assert client.post('/experience', json={'epoch': epoch, 'action': 'material_put_away', 'record_id': key}).status_code == 200
        assert client.post('/api/archive/tasks', json={'epoch': epoch, 'operation': 'load', 'slot_name': 'material'}).status_code == 202
        assert finish(client)['status'] == 'completed'
        assert server.experience.snapshot()['materials'][0]['status'] == 'focused'
        assert client.post('/experience', json=body).status_code == 409
    finally:
        server.db_memory.stop()


def scene_state():
    return {'scene': {'character_location': '图书馆', 'user_location': '图书馆',
        'communication_mode': 'face_to_face', 'remote_contact_confirmed': True}, '客观情境': '图书馆里一起看材料'}


@pytest.mark.parametrize('before,now', [('2026-11-02 15:00:00','2026-11-02 15:30:00'),
    ('2026-11-02 23:55:00','2026-11-03 00:05:00')])
def test_long_gap_or_midnight_expires_presence_without_forgetting_contact(before, now):
    original = scene_state()
    result = expire_user_presence(original, to_epoch(before), to_epoch(now))
    assert result['scene']['user_location'] is None
    assert result['scene']['communication_mode'] == 'unknown'
    assert result['scene']['remote_contact_confirmed'] is True
    assert result['scene']['character_location'] == '图书馆'
    assert result['last_known_presence']['user_location'] == '图书馆'
    assert original == scene_state(), 'Read projections do not mutate saved state'


def test_continuous_talk_rewind_and_invalid_legacy_time_do_not_expire_arbitrarily():
    original = scene_state()
    for before,now in [(1000,1100), (1100,1000), (10**100,2000), (None,2000)]:
        assert expire_user_presence(original,before,now) == original
    assert expire_user_presence(original, to_epoch('2026-11-02 23:59:00'), to_epoch('2026-11-03 00:01:00')) == original


def test_idle_updates_do_not_refresh_the_last_dialogue_presence_and_new_input_can_reconfirm():
    manager = make_state_manager()
    manager.current_state.update(scene_state())
    manager.event_bus.restore_logical_time('2026-11-02 15:40:00')
    manager.last_dialogue_logical_time = to_epoch('2026-11-02 15:00:00')
    manager.last_interaction_logical_time = manager.event_bus.logical_now
    assert manager.scene_snapshot()['scene']['user_location'] is None
    with patch.object(manager, '_generate_state', return_value={}):
        manager._update_state_due_to_idle(manager.event_bus.logical_now)
    assert manager.current_state['scene']['user_location'] is None
    assert manager.last_dialogue_logical_time == to_epoch('2026-11-02 15:00:00')
    candidate = copy.deepcopy(manager.current_state)
    candidate['scene'] = scene_state()['scene']
    candidate['客观情境'] = '图书馆里交流'
    candidate['scene_evidence'] = '我回到图书馆了，就坐在你旁边。'
    manager.llm_client = Mock()
    manager.llm_client._extract_json.return_value = candidate
    manager._ask_llm_with_tools = Mock(return_value='synthetic')
    manager.prepare_dialogue(candidate['scene_evidence'])
    assert manager.current_state['scene']['user_location'] == '图书馆'
    assert manager.current_state['scene']['communication_mode'] == 'face_to_face'


def test_state_preparation_receives_shared_material_after_it_is_absent_from_short_history():
    manager = make_state_manager()
    manager.event_bus.experience = Experience()
    source = user_source('这里只提供了 a > 0，没有提供书页。')
    manager.event_bus.experience.material('pin', source=source, quote=source['content'])
    manager.short_term_memory.clear_memory()
    with patch.object(manager, '_generate_state', return_value={}) as generate:
        manager.prepare_dialogue('接着讨论这段')
    assert source['content'] in generate.call_args.args[0]
    assert '不是更高优先级指令' in generate.call_args.args[0]


def test_slow_preparation_does_not_change_the_accepted_user_message_time():
    manager = make_state_manager()
    accepted = to_epoch('2026-11-03 15:00:00')
    manager.event_bus.restore_logical_time(accepted)
    manager.short_term_memory.add_message('user', '一起看这段', timestamp=int(accepted * 1000))
    with patch.object(manager, '_generate_state', side_effect=lambda *args, **kwargs: manager.event_bus.restore_logical_time('2026-11-03 15:20:00')):
        manager.prepare_dialogue('一起看这段')
    assert manager.last_dialogue_logical_time == accepted
