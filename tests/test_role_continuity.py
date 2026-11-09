import copy
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from persona import continuity
from core.companionship import offers
from core.proactive import ProactivePolicy
from core.proactive_events import EventProactivePolicy
from core.time_utils import to_epoch
from core.experience import Experience
from tests.test_persistence_scene import make_state_manager
from tests.test_restore_experience import make_server, finish
from fastapi.testclient import TestClient
from core.event_bus import Event
from tests.test_turn_cancellation import make_brain

NOW = to_epoch('2026-11-08 15:00:00')


def base():
    return {'P': 6, 'A': 5, 'D': 5, 'scene': {'character_location': '宿舍', 'user_location': None,
        'communication_mode': 'unknown', 'remote_contact_confirmed': False}, '客观情境': '宿舍，整理笔记',
        '内心活动': '', '近期综合轨迹': '', '近期目标': '', '对应时间': '2026-11-08 15:00:00',
        'scene_status': 'ready', 'state_source': 'interaction'}


def start():
    candidate = dict(base(), role_activity_request={'action': 'start', 'kind': 'notes'})
    return continuity.commit(base(), candidate, NOW, duration=120)


def test_same_input_affect_is_not_counted_twice_and_new_input_can_change_it():
    candidate = dict(base(), P=2, D=2, reply_perspective={'feeling': '有点委屈'})
    first = continuity.commit(base(), candidate, NOW, input_id='one')
    again = continuity.commit(first, dict(candidate, P=0, D=0), NOW + 60, input_id='one')
    assert again['P'] > first['P'] and again['D'] > first['D']
    assert again['role_continuity']['emotion']['basis_id'] == 'one'
    changed = continuity.commit(again, dict(candidate, P=8), NOW + 61, input_id='two')
    assert changed['P'] == 8
    assert changed['role_continuity']['processed_inputs'] == ['one', 'two']


def test_decay_depends_on_elapsed_world_time_not_number_of_heartbeats():
    first = continuity.commit(base(), dict(base(), P=2), NOW, input_id='one')
    once = continuity.advance(first, NOW + 3600)
    many = first
    for seconds in range(60, 3601, 60):
        many = continuity.advance(many, NOW + seconds)
    assert once['P'] == many['P'] == 4
    assert once['role_continuity']['emotion']['remaining'] == .5


def test_faded_emotion_is_not_reintroduced_as_current_feeling_but_original_record_remains():
    first = continuity.commit(base(), dict(base(), P=2, reply_perspective={'feeling': '合成的旧委屈'}), NOW, input_id='one')
    later = continuity.commit(first, dict(first, reply_perspective={'feeling': '重复解读的委屈'}), NOW + 4 * 3600)
    assert later['reply_perspective']['feeling'] == ''
    assert later['role_continuity']['emotion']['feeling'] == '合成的旧委屈'
    assert '合成的旧委屈' not in continuity.context(later)


def test_character_activity_progress_completion_and_serialized_restart_are_deterministic():
    state = start()
    midway = continuity.advance(json.loads(json.dumps(state)), NOW + 60)
    done = continuity.advance(midway, NOW + 120)
    assert [row['kind'] for row in done['role_continuity']['events']] == ['started', 'progress', 'completed']
    assert done['role_continuity']['activity']['status'] == 'completed'
    again = continuity.advance(done, NOW + 10000)
    assert again['role_continuity']['events'] == done['role_continuity']['events']
    assert all(row['participants'] == ['character'] for row in again['role_continuity']['events'])
    assert done['scene']['user_location'] is None


def test_pause_resume_does_not_count_paused_time_or_teleport_character():
    state = start()
    paused = continuity.commit(state, dict(state, role_activity_request={'kind': 'notes', 'action': 'pause'}), NOW + 30)
    resumed = continuity.commit(paused, dict(paused, role_activity_request={'kind': 'notes', 'action': 'resume'}), NOW + 300)
    assert resumed['role_continuity']['activity']['elapsed'] == 30
    done = continuity.advance(resumed, NOW + 390)
    assert done['role_continuity']['activity']['status'] == 'completed'
    moved = copy.deepcopy(state)
    moved['scene']['character_location'] = '食堂'
    interrupted = continuity.advance(moved, NOW + 30)
    assert interrupted['role_continuity']['activity']['status'] == 'cancelled'


def test_offline_events_are_recorded_but_not_pushed_as_fresh_news():
    state = continuity.advance(start(), NOW + 120, offline=True)
    assert state['role_continuity']['events'][-1]['origin'] == 'offline_simulation'
    assert all(row['kind'] != 'role_event' for row in offers(state, {}, [], NOW + 120))
    rewound = continuity.advance(start(), NOW - 30)
    assert rewound['role_continuity']['activity']['elapsed'] == 0
    assert rewound['role_continuity']['activity']['status'] == 'paused'
    assert rewound['role_continuity']['events'][0]['timeline_status'] == 'future_snapshot'
    assert 'started' not in continuity.context(rewound, NOW - 30)
    resumed = continuity.commit(rewound, dict(rewound, role_activity_request={'kind': 'notes', 'action': 'resume'}), NOW - 20)
    current_branch = continuity.advance(resumed, NOW + 100)
    assert current_branch['role_continuity']['activity']['status'] == 'completed'
    assert current_branch['role_continuity']['events'][-1]['kind'] == 'completed'
    assert current_branch['role_continuity']['activity']['id'] != start()['role_continuity']['activity']['id']


def test_sleep_transition_is_a_single_physical_event_and_location_changes_cancel_immediately():
    asleep = continuity.commit(base(), dict(base(), A=0), NOW, sleeping=True)
    assert asleep['A'] == 0 and asleep['role_continuity']['sleeping']
    resting = continuity.commit(asleep, dict(asleep, A=9), NOW + 3600, sleeping=True)
    assert resting['A'] == 1
    assert resting['role_continuity']['emotion']['basis_id'] == asleep['role_continuity']['emotion']['basis_id']
    state = start()
    moved = copy.deepcopy(state)
    moved['scene']['character_location'] = '食堂'
    result = continuity.commit(state, moved, NOW + 10)
    assert result['role_continuity']['activity']['status'] == 'cancelled'
    manager = make_state_manager()
    manager.current_state = asleep
    manager.is_sleeping = True
    proposal = dict(asleep, action_pulse={'is_sleeping': False})
    with patch.object(manager, '_ask_llm_with_tools', return_value=json.dumps(proposal)), \
            patch.object(manager.llm_client, '_extract_json', side_effect=json.loads):
        manager._generate_state('合成唤醒', NOW + 1, user_text='醒了吗')
    assert not manager.is_sleeping and not manager.current_state['role_continuity']['sleeping']


def test_model_cannot_finish_activity_by_saying_completed_or_invent_a_user_participant():
    manager = make_state_manager()
    manager.current_state = start()
    proposal = copy.deepcopy(manager.current_state)
    proposal['role_continuity']['activity']['status'] = 'completed'
    with patch.object(manager, '_ask_llm_with_tools', return_value=json.dumps(proposal)), \
            patch.object(manager.llm_client, '_extract_json', side_effect=json.loads):
        manager._generate_state('合成推演', NOW + 10, allow_inferred=True)
    assert manager.current_state['role_continuity']['activity']['status'] == 'running'
    assert len(manager.current_state['role_continuity']['events']) == 1


def test_role_event_memory_outbox_is_durable_and_retry_preserves_provenance():
    manager = make_state_manager()
    state = start()
    with patch('persona.state_manager.append_event_once', side_effect=OSError('synthetic disk failure')):
        manager._update_state(state, NOW)
    assert manager.current_state['role_continuity']['events'][0]['memory_pending'] is True
    manager._drain_role_memory()
    manager._drain_role_memory()
    sources = manager.short_term_memory.working.snapshot()['sources']
    assert len(sources) == 1
    assert sources[0]['source_type'] == 'role_simulation_record'
    assert not manager.current_state['role_continuity']['events'][0]['memory_pending']
    assert 'role_simulation_record' in manager.short_term_memory.working.prompt_context()


def offer(key='event', kind='role_event', topic=None):
    return {'id': key, 'kind': kind, 'topic': topic or key, 'summary': '合成事件'}


def policy(tmp_path, clock):
    return EventProactivePolicy(600, tmp_path/'proactive.json', lambda: clock[0], minimum_gap=120)


def test_event_has_shorter_gap_than_ordinary_chat_without_bypassing_global_budget(tmp_path):
    clock = [1000.]
    delivery = policy(tmp_path, clock)
    assert delivery.reserve([], offer('first'))
    clock[0] += 120
    assert delivery.check([], offer('second'))['allowed']
    assert delivery.check([])['reason'] == 'conversation_cooldown'
    assert delivery.reserve([], offer('second'))
    clock[0] += 120
    assert delivery.reserve([], offer('third'))
    clock[0] += 120
    assert delivery.check([], offer('fourth'))['reason'] == 'hourly_budget'
    assert delivery.check([], offer('reminder', 'reminder'))['allowed']


def test_semantic_event_duplicate_dedup_survives_restart_and_legacy_roundtrip(tmp_path):
    clock = [1000.]
    delivery = policy(tmp_path, clock)
    assert delivery.reserve([], offer('stable', topic='same_activity'))
    delivery.finish('stable', 'delivered')
    clock[0] += 600
    reloaded = policy(tmp_path, clock)
    assert reloaded.check([], offer('stable'))['reason'] == 'already_attempted'
    assert reloaded.check([], offer('different', topic='same_activity'))['reason'] == 'same_topic'
    legacy = ProactivePolicy(0, tmp_path/'proactive.json', lambda: clock[0])
    assert legacy.reserve([])
    assert policy(tmp_path, clock).data['seen']['stable'] == 'delivered'


@pytest.mark.parametrize('gate', ['quiet', 'sleeping', 'paused'])
def test_all_notification_types_obey_do_not_disturb(tmp_path, gate):
    delivery = policy(tmp_path, [1000.])
    assert delivery.check([], offer('r', 'reminder'), **{gate: True})['reason'] == gate
    assert not delivery.reserve([], offer('r', 'reminder'), **{gate: True})


def test_unanswered_chat_stays_quiet_but_explicit_shared_progress_and_reminders_can_deliver(tmp_path):
    delivery = policy(tmp_path, [1000.])
    history = [{'role': 'user', 'content': '合成消息'}, {'role': 'assistant', 'content': '合成主动消息', 'turn_source': 'proactive'}]
    assert delivery.check(history, offer())['reason'] == 'awaiting_user'
    assert delivery.check(history, offer('shared', 'shared_timer'))['allowed']
    assert delivery.check(history, offer('reminder', 'reminder'))['allowed']
    assert delivery.check(history, offer('reminder', 'reminder'), online=False)['reason'] == 'offline'


def test_failed_reservation_never_marks_event_as_delivered_and_failure_receipt_is_not_retried(tmp_path):
    delivery = policy(tmp_path, [1000.])
    with patch.object(delivery, '_save', side_effect=OSError('disk failure')):
        with pytest.raises(OSError):
            delivery.reserve([], offer())
    assert delivery.data['seen'] == {}
    assert delivery.reserve([], offer())
    delivery.finish('event', 'failed')
    assert policy(tmp_path, [9999.]).check([], offer())['reason'] == 'already_attempted'


def test_reminder_requires_explicit_enable_and_confirmed_agreement_and_does_not_claim_completion():
    experience = Experience()
    source = {'message_id': 'source', 'content': '一会儿记得休息', 'role': 'user'}
    experience.agreement('休息一下', source)
    key = experience.snapshot()['agreements'][0]['id']
    with pytest.raises(ValueError, match='核对确认'):
        experience.set_reminder(key, True, '2026-11-08 15:05', NOW)
    experience.update_agreement(key, 'confirmed')
    assert all(row['kind'] != 'reminder' for row in offers({}, experience.snapshot(), [], NOW + 300))
    experience.set_reminder(key, True, '2026-11-08 15:05', NOW)
    due = offers({}, experience.snapshot(), [], NOW + 300)[0]
    assert due['kind'] == 'reminder' and '休息一下' in due['summary']
    assert all(row['kind'] != 'reminder' for row in offers({}, experience.snapshot(), [], NOW + 1300))
    experience.set_reminder(key, False)
    assert all(row['kind'] != 'reminder' for row in offers({}, experience.snapshot(), [], NOW + 300))
    experience.set_reminder(key, True, '2026-11-08 15:05', NOW)
    experience.update_agreement(key, 'cancelled')
    assert not experience.snapshot()['agreements'][0]['notification_enabled']


def test_model_context_does_not_expand_with_delivery_and_processed_input_bookkeeping():
    manager = make_state_manager()
    manager.current_state = start()
    manager.current_state['role_continuity']['processed_inputs'] = ['private-bookkeeping-' + str(index) for index in range(128)]
    context = manager._model_state()
    assert 'private-bookkeeping' not in json.dumps(context)
    assert context['role_continuity']['activity']['status'] == 'running'


def test_continuity_flag_can_disable_evolution_without_erasing_records(monkeypatch):
    from config.settings import settings
    manager = make_state_manager()
    manager.current_state = start()
    previous = copy.deepcopy(manager.current_state['role_continuity'])
    proposal = dict(manager.current_state, role_activity_request={'kind': 'notes', 'action': 'pause'})
    monkeypatch.setattr(settings, 'ROLE_CONTINUITY_ENABLED', False)
    with patch.object(manager, '_ask_llm_with_tools', return_value=json.dumps(proposal)), \
            patch.object(manager.llm_client, '_extract_json', side_effect=json.loads):
        manager._generate_state('合成更新', NOW + 10)
        manager._advance_role(NOW + 1000)
    assert manager.current_state['role_continuity'] == previous
    assert 'role_activity_request' not in manager.current_state


def test_shared_timer_is_not_a_personal_timer_or_a_completed_user_task():
    timer = {'id': 'timer', 'status': 'running', 'shared': False, 'elapsed_seconds': 60, 'target_seconds': 60}
    assert offers({}, {'activities': [timer]}, [], NOW)[0]['kind'] == 'ordinary'
    timer['shared'] = True
    shared = offers({}, {'activities': [timer]}, [], NOW)[0]
    assert shared['kind'] == 'shared_timer' and '不代表' in shared['summary']


def test_real_archive_roundtrip_restores_role_clock_activity_emotion_and_memory():
    server = make_server()
    server.state_manager.current_state = start()
    server.state_manager._update_state(server.state_manager.current_state, NOW)
    server.event_bus.restore_logical_time('2026-11-08 15:00:00')
    client = TestClient(server.app)
    try:
        epoch = server.protocol.epoch
        assert client.post('/api/archive/tasks', json={'epoch': epoch, 'operation': 'create', 'slot_name': 'role'}).status_code == 202
        assert finish(client)['status'] == 'completed'
        server.state_manager._update_state(continuity.advance(server.state_manager.current_state, NOW + 200), NOW + 200)
        assert client.post('/api/archive/tasks', json={'epoch': epoch, 'operation': 'load', 'slot_name': 'role'}).status_code == 202
        assert finish(client)['status'] == 'completed'
        restored = server.state_manager.current_state['role_continuity']['activity']
        assert restored['status'] == 'running' and restored['elapsed'] == 0
        assert abs(server.event_bus.logical_now - NOW) < 5
    finally:
        server.db_memory.stop()


def test_due_reminder_is_saved_without_model_generation_and_cannot_repeat():
    brain, bus, memory, state = make_brain()
    due = offer('confirmed-reminder', 'reminder')
    due['summary'] = '你开启的网页提醒到时间了：合成休息提醒'
    state.proactive_offer = lambda: due
    state.delivery_gates = lambda: {'online': True}
    finished = []
    bus.subscribe('llm.finished', lambda event: finished.append(event.data))
    with patch('brain.core.threading.Thread', side_effect=lambda **kw: SimpleNamespace(start=kw['target'])):
        bus.publish(Event('idle_speak', {'offer_id': due['id']}))
        bus.publish(Event('idle_speak', {'offer_id': due['id']}))
    brain.llm_client.stream_chat.assert_not_called()
    assert len(finished) == 1 and due['summary'] in finished[0]['text']
    assert memory.get_memory()['history'][-1]['turn_source'] == 'proactive'
    assert bus.turns.snapshot()['released']
    assert brain.proactive_policy.data['seen'][due['id']] == 'delivered'


def test_silent_opportunity_is_released_and_stale_event_cannot_change_it():
    brain, bus, _memory, state = make_brain()
    selected = offer('current-event')
    state.proactive_offer = lambda: selected
    state.delivery_gates = lambda: {'online': True}
    brain.llm_client.stream_chat.return_value = iter([])
    with patch('brain.core.threading.Thread', side_effect=lambda **kw: SimpleNamespace(start=kw['target'])):
        bus.publish(Event('idle_speak', {'offer_id': 'stale-event'}))
        brain.llm_client.stream_chat.assert_not_called()
        bus.publish(Event('idle_speak', {'offer_id': selected['id']}))
    assert bus.turns.snapshot()['released']
    assert brain.proactive_policy.data['seen'][selected['id']] == 'silent'


def test_reminder_http_opt_in_and_cancellation_are_explicit_world_operations():
    server = make_server()
    server.event_bus.restore_logical_time('2026-11-08 15:00:00')
    source = {'message_id': 'source', 'role': 'user', 'content': '合成休息约定'}
    server.experience.agreement('休息', source)
    key = server.experience.snapshot()['agreements'][0]['id']
    client = TestClient(server.app)
    payload = {'epoch': server.protocol.epoch, 'action': 'agreement_reminder', 'record_id': key,
        'enabled': True, 'when': '2026-11-08T15:10'}
    try:
        assert client.post('/experience', json=payload).status_code == 400
        server.experience.update_agreement(key, 'confirmed')
        assert client.post('/experience', json=dict(payload, when='十分钟后')).status_code == 400
        assert client.post('/experience', json=payload).status_code == 200
        assert client.get('/config').json()['experience']['agreements'][0]['notification_status'] == 'waiting'
        assert client.post('/experience', json=dict(payload, enabled=False)).status_code == 200
        assert not server.experience.snapshot()['agreements'][0]['notification_enabled']
        assert server.memory.get_memory()['history'] == []
    finally:
        server.db_memory.stop()


def test_simulated_event_memory_keeps_its_provenance_even_when_encoder_mislabels_it():
    from memory.memory_process import Hippocampus
    hippocampus = object.__new__(Hippocampus)
    hippocampus.event_bus = make_state_manager().event_bus
    source = {'message_id': 'role-event', 'role': 'system_event', 'timestamp': int(NOW * 1000),
        'source_type': 'role_simulation_record', 'content': '角色整理笔记'}
    raw = [{'content': '整理笔记', 'source_ids': ['role-event'], 'importance': 1, 'confidence': 1}]
    encoded = hippocampus._validate_memories(raw, [source], json.dumps(source))[0]
    assert encoded['source_type'] == 'role_simulation_record'
    assert encoded['content'].startswith('角色虚构世界记录')
    assert encoded['evidence'][0]['source_type'] == 'role_simulation_record'


def test_corrupt_event_ledger_is_preserved_and_fails_closed(tmp_path):
    path = tmp_path/'proactive.json'
    content = json.dumps({'version': 2, 'last_attempt_at': 1000, 'attempts': [], 'topics': {}, 'seen': {'bad': []}})
    path.write_text(content, encoding='utf-8')
    delivery = policy(tmp_path, [2000.])
    assert delivery.check([], offer())['reason'] == 'storage_error'
    assert path.read_text(encoding='utf-8') == content


def test_corrupt_role_archive_is_rejected_before_any_world_import(tmp_path):
    from archive.utils.validate import validate_archive
    folder = tmp_path/'bad-archive'
    folder.mkdir()
    (folder/'manifest.json').write_text(json.dumps({'version': '1', 'created_at': 'synthetic'}), encoding='utf-8')
    state = start()
    state['role_continuity']['activity']['elapsed'] = 'invalid'
    (folder/'state.json').write_text(json.dumps(state), encoding='utf-8')
    valid, reason = validate_archive(str(folder))
    assert not valid and '连续性' in reason


def test_role_event_encodes_embeds_stores_and_recalls_without_becoming_user_history():
    from tests.test_memory_pipeline import harness
    from memory.postgres import connection
    bus, memory, store, hippocampus, _llm = harness()
    manager = make_state_manager()
    manager.event_bus = bus
    manager.short_term_memory = memory
    try:
        manager._update_state(start(), NOW)
        key = manager.current_state['role_continuity']['events'][0]['id']
        bus.publish(Event('memory.preprocess', {}))
        store.store_queue.join()
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT content,metadata FROM episodic_memory WHERE metadata->'source_ids' ? %s", (key,))
            content, metadata = cur.fetchone()
        assert metadata['source_type'] == 'role_simulation_record'
        assert content.startswith('角色虚构世界记录')
        result = hippocampus.query_memory('笔记整理', ['pipeline-test'])
        assert result['found'] and '角色虚构世界记录' in json.dumps(result, ensure_ascii=False)
        assert memory.get_memory()['history'] == []
    finally:
        store.stop()
