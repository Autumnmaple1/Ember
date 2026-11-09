"""Explicit UI scene settings use program validation, no fake chat or model call."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from memory.postgres import connection
from core.time_utils import to_epoch
from tests.test_restore_experience import make_server, finish


@pytest.fixture
def world():
    server = make_server()
    server.state_manager.llm_client = Mock()
    server.state_manager._ask_llm_with_tools = Mock(side_effect=AssertionError('UI scene must not call a model'))
    try: yield server, TestClient(server.app)
    finally: server.db_memory.stop()


def body(server, **changes):
    previous = server.state_manager.scene_snapshot()['scene']
    return {'epoch': server.protocol.epoch, 'expected_scene': previous, 'scene': {**previous, **changes}}


def test_direct_save_is_immediate_persistent_sourced_and_creates_no_chat(world):
    server, client = world
    previous = copy.deepcopy(server.state_manager.current_state)
    events = []
    server.event_bus.subscribe('user.input', events.append)
    server.event_bus.subscribe('llm.started', events.append)
    result = client.post('/scene', json=body(server, character_location=' 图书馆 ', user_location='图书馆', communication_mode='face_to_face'))
    assert result.status_code == 200, result.text
    state = result.json()['state']
    assert state['scene']['character_location'] == '图书馆'
    assert state['scene']['communication_mode'] == 'face_to_face'
    assert state['state_source'] == 'user_ui'
    assert state['scene_confirmation']['source'] == 'user_ui'
    assert state['scene_confirmation']['scene'] == state['scene']
    assert abs(to_epoch(state['对应时间']) - server.event_bus.logical_now) < 2
    for key in ('P', 'A', 'D', '近期目标', '内心活动'): assert state[key] == previous[key]
    assert json.loads(Path('config/state.json').read_text(encoding='utf-8'))['scene'] == state['scene']
    assert server.db_memory.flush()
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT text FROM state_list ORDER BY id DESC LIMIT 1")
        assert json.loads(cursor.fetchone()[0])['scene_confirmation'] == state['scene_confirmation']
    assert not events
    assert server.memory.get_memory()['history'] == []
    server.state_manager._ask_llm_with_tools.assert_not_called()


@pytest.mark.parametrize('changes', [
    {'character_location': '宿舍', 'user_location': '图书馆', 'communication_mode': 'face_to_face'},
    {'communication_mode': 'remote', 'remote_contact_confirmed': False},
    {'communication_mode': 'telepathy'}, {'remote_contact_confirmed': 'false'},
    {'user_location': 'a' * 151}, {'user_location': '楼内\n房间'}, {'P': 10},
])
def test_invalid_scene_is_rejected_without_writing(world, changes):
    server, client = world
    previous = copy.deepcopy(server.state_manager.current_state)
    assert client.post('/scene', json=body(server, **changes)).status_code == 422
    assert server.state_manager.current_state == previous


def test_stale_scene_or_world_cannot_overwrite_current_scene(world):
    server, client = world
    stale = body(server, character_location='图书馆')
    assert client.post('/scene', json=body(server, character_location='宿舍')).status_code == 200
    assert client.post('/scene', json=stale).status_code == 409
    changed = body(server, character_location='食堂')
    server.protocol.rotate()
    assert client.post('/scene', json=changed).status_code == 409
    assert server.state_manager.current_state['scene']['character_location'] == '宿舍'


@pytest.mark.parametrize('field', ['paused', 'pending_dialogues'])
def test_scene_save_waits_for_archive_or_accepted_input(world, field):
    server, client = world
    setattr(server.event_bus, field, True if field == 'paused' else 1)
    assert client.post('/scene', json=body(server, character_location='图书馆')).status_code == 409


def test_state_write_failure_preserves_original_and_can_be_retried(world):
    server, client = world
    previous = copy.deepcopy(server.state_manager.current_state)
    with patch.object(Path, 'replace', side_effect=OSError('synthetic disk failure')):
        assert client.post('/scene', json=body(server, character_location='图书馆')).status_code == 503
    assert server.state_manager.current_state == previous
    assert client.post('/scene', json=body(server, character_location='图书馆')).status_code == 200


def test_confirmation_refreshes_presence_without_inventing_a_user_message_and_restores(world):
    server, client = world
    server.event_bus.restore_logical_time('2026-11-07 15:00:00')
    server.state_manager.last_dialogue_logical_time = to_epoch('2026-11-07 12:00:00')
    scene = body(server, character_location='图书馆', user_location='图书馆', communication_mode='face_to_face')
    assert client.post('/scene', json=scene).status_code == 200
    assert server.state_manager.scene_snapshot()['scene']['user_location'] == '图书馆'
    epoch = server.protocol.epoch
    assert client.post('/api/archive/tasks', json={'epoch': epoch, 'operation': 'create', 'slot_name': 'ui-scene'}).status_code == 202
    assert finish(client)['status'] == 'completed'
    assert client.post('/scene', json=body(server, user_location=None, communication_mode='unknown')).status_code == 200
    assert client.post('/api/archive/tasks', json={'epoch': epoch, 'operation': 'load', 'slot_name': 'ui-scene'}).status_code == 202
    assert finish(client)['status'] == 'completed'
    assert server.state_manager.scene_snapshot()['scene']['communication_mode'] == 'face_to_face'
    assert server.memory.get_memory()['history'] == []


def test_models_cannot_forge_ui_confirmation_metadata(world):
    server, _client = world
    manager = server.state_manager
    original = {'source': 'user_ui', 'timestamp': 1000, 'id': 'confirmed'}
    manager.current_state['scene_confirmation'] = copy.deepcopy(original)
    proposed = copy.deepcopy(manager.current_state)
    proposed['scene_confirmation'] = {'source': 'user_ui', 'timestamp': 9999999999999, 'id': 'forged'}
    with patch.object(manager, '_ask_llm_with_tools', return_value=json.dumps(proposed)), \
         patch.object(manager.llm_client, '_extract_json', side_effect=json.loads):
        manager._generate_state('synthetic', manager.event_bus.logical_now)
    assert manager.current_state['scene_confirmation'] == original


@pytest.mark.parametrize('stored_quiet', [False, True])
def test_world_load_does_not_rewind_the_current_proactive_preference(world, stored_quiet):
    server, client = world
    epoch = server.protocol.epoch
    assert client.post('/experience', json={'epoch': epoch, 'action': 'quiet', 'enabled': stored_quiet}).status_code == 200
    assert client.post('/api/archive/tasks', json={'epoch': epoch, 'operation': 'create', 'slot_name': 'permission'}).status_code == 202
    assert finish(client)['status'] == 'completed'
    assert client.post('/experience', json={'epoch': epoch, 'action': 'quiet', 'enabled': not stored_quiet}).status_code == 200
    assert client.post('/api/archive/tasks', json={'epoch': epoch, 'operation': 'load', 'slot_name': 'permission'}).status_code == 202
    assert finish(client)['status'] == 'completed'
    assert server.event_bus.quiet is not stored_quiet
    assert server.experience.snapshot()['quiet'] is not stored_quiet
