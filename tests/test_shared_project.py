"""Shared activity rules, canonical sources and saves use only suite isolation."""
import copy
import json
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.experience import Experience
from core.shared_project import ProjectConflict, validate_project
from memory.postgres import connection
from tests.test_restore_experience import make_server, finish


def source():
    return {'message_id': str(uuid.uuid4()), 'role': 'ai', 'completion_status': 'completed',
        'content': '我们可以先整理一份虚构资料，再一起决定怎么写。'}


def command(experience, action, **kwargs):
    if action != 'start':
        item = experience.snapshot()['projects'][-1]
        kwargs = {'record_id': item['id'], 'expected_version': item['version'], **kwargs}
    return experience.project(action, request_id=str(uuid.uuid4()), logical_time='2026-11-02 15:00:00', **kwargs)


@pytest.mark.parametrize('route', ['window', 'archive'])
@pytest.mark.parametrize('resolution,budget,edition', [('verify', 0, '核对版'), ('broaden', 1, '细节版'), ('leave_open', 2, '待续版')])
def test_each_route_and_choice_yields_an_explainable_page(route, resolution, budget, edition):
    exp = Experience()
    command(exp, 'start', source=source())
    command(exp, 'choose', choice=route)
    item = command(exp, 'choose', choice=resolution)['projects'][-1]
    assert item['budget'] == budget and item['view']['edition'] == edition
    assert len(item['cards']) == (1 if resolution == 'leave_open' else 2)
    assert item['status'] == 'active', 'Dialogue or collecting cards cannot complete a page'
    item = command(exp, 'finish', title='这一页的名字', note='把未知留到下一次')['projects'][-1]
    assert item['phase'] == 'finished' and item['status'] == 'completed'
    assert [x['choice'] for x in item['decisions']] == [route, resolution]
    validate_project(item)
    assert command(exp, 'edit', title=item['title'], note='下次继续核对')['projects'][-1]['note'] == '下次继续核对'
    restored = Experience()
    assert restored.snapshot()['projects'][-1]['status'] == 'completed'
    assert '虚构资料推演' in restored.prompt_context()
    assert '不表示双方外出' in restored.prompt_context()
    assert '这一页的名字' in restored.prompt_context()


def test_restart_pause_preserves_choices_and_does_not_simulate_progress():
    exp = Experience()
    command(exp, 'start', source=source())
    command(exp, 'choose', choice='archive')
    before = exp.snapshot()['projects'][-1]
    restored = Experience(clock=lambda: 90000000)
    item = restored.snapshot()['projects'][-1]
    assert item['status'] == 'paused' and item['budget'] == before['budget']
    assert item['cards'] == before['cards'] and item['phase'] == 'investigate'
    with pytest.raises(ValueError): command(restored, 'choose', choice='verify')
    command(restored, 'resume')
    assert command(restored, 'choose', choice='verify')['projects'][-1]['phase'] == 'compose'


def test_stopping_preserves_the_fragment_and_allows_a_new_page():
    exp = Experience()
    command(exp, 'start', source=source())
    command(exp, 'choose', choice='archive')
    fragment = command(exp, 'abandon')['projects'][-1]
    assert fragment['status'] == 'abandoned' and len(fragment['cards']) == 1
    assert fragment['view']['choices'] == []
    validate_project(fragment)
    with pytest.raises(ValueError): command(exp, 'resume')
    command(exp, 'start', source=source())
    assert len(exp.snapshot()['projects']) == 2
    assert Experience().snapshot()['projects'][0]['status'] == 'abandoned'


def test_duplicate_commands_are_idempotent_and_identifier_reuse_cannot_change_a_choice():
    exp = Experience()
    request = dict(request_id=str(uuid.uuid4()), source=source())
    initial = exp.project('start', **request)
    assert exp.project('start', **request) == initial
    item = initial['projects'][0]
    choose = dict(request_id=str(uuid.uuid4()), record_id=item['id'], expected_version=0, choice='window')
    updated = exp.project('choose', **choose)
    assert exp.project('choose', **choose) == updated
    assert len(updated['projects']) == 1 and len(updated['projects'][0]['decisions']) == 1
    with pytest.raises(ProjectConflict): exp.project('choose', **{**choose, 'choice': 'archive'})
    with pytest.raises(ProjectConflict): exp.project('choose', **{**choose, 'request_id': str(uuid.uuid4())})
    assert exp.snapshot() == updated


def test_concurrent_choices_only_commit_one_version():
    exp = Experience()
    item = command(exp, 'start', source=source())['projects'][0]
    results = []
    def choose(choice):
        try:
            exp.project('choose', request_id=str(uuid.uuid4()), record_id=item['id'], expected_version=0, choice=choice)
            results.append('saved')
        except ProjectConflict: results.append('stale')
    threads = [threading.Thread(target=choose, args=(choice,)) for choice in ('window', 'archive')]
    for worker in threads: worker.start()
    for worker in threads: worker.join(3)
    assert sorted(results) == ['saved', 'stale']
    assert exp.snapshot()['projects'][0]['version'] == 1


def test_invalid_transition_and_write_failure_leave_state_and_receipts_intact(monkeypatch):
    exp = Experience()
    with pytest.raises(ValueError): command(exp, 'start', source={**source(), 'role': 'user'})
    command(exp, 'start', source=source())
    before, disk = exp.snapshot(), exp.path.read_bytes()
    with pytest.raises(ValueError): command(exp, 'finish', title='提前完成')
    with pytest.raises(ValueError): command(exp, 'choose', choice='verify')
    with pytest.raises(ProjectConflict): command(exp, 'start', source=source())
    def fail(): raise OSError('synthetic disk failure')
    monkeypatch.setattr(exp, '_save', fail)
    with pytest.raises(OSError): command(exp, 'choose', choice='window')
    assert exp.snapshot() == before and exp.path.read_bytes() == disk


def test_legacy_experience_is_compatible_and_forged_materials_fail_closed():
    Path('config/experience.json').write_text(json.dumps({'version': 1, 'activities': []}), encoding='utf-8')
    exp = Experience()
    assert exp.snapshot()['projects'] == []
    command(exp, 'start', source=source())
    command(exp, 'choose', choice='window')
    stored = json.loads(exp.path.read_text(encoding='utf-8'))
    stored['projects'][0]['cards'][0]['text'] = '伪造为已经实际外出'
    exp.path.write_text(json.dumps(stored), encoding='utf-8')
    with pytest.raises(ValueError, match='材料'): Experience()


def test_http_requires_canonical_source_epoch_and_version_without_moving_anyone():
    server = make_server()
    client = TestClient(server.app)
    key = str(uuid.uuid4())
    scene = copy.deepcopy(server.state_manager.current_state['scene'])
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO message_list(sender,text,message_id,completion_status) VALUES ('assistant',%s,%s,'completed')", (source()['content'], key))
        payload = {'action': 'project_start', 'epoch': server.protocol.epoch, 'request_id': str(uuid.uuid4()), 'message_id': key}
        assert client.post('/experience', json={**payload, 'epoch': 'old'}).status_code == 409
        assert client.post('/experience', json={**payload, 'message_id': 'missing'}).status_code == 400
        response = client.post('/experience', json=payload)
        assert response.status_code == 200
        project = response.json()['experience']['projects'][0]
        assert project['source']['content'] == source()['content']
        assert client.post('/experience', json=payload).json()['experience']['revision'] == response.json()['experience']['revision']
        choose = {**payload, 'action': 'project_choose', 'message_id': None, 'request_id': str(uuid.uuid4()),
            'record_id': project['id'], 'expected_version': 0, 'choice': 'window'}
        assert client.post('/experience', json=choose).status_code == 200
        assert client.post('/experience', json={**choose, 'request_id': str(uuid.uuid4())}).status_code == 409
        server.event_bus.pause_world(True)
        assert client.post('/experience', json={**choose, 'expected_version': 1}).status_code == 409
        assert server.state_manager.current_state['scene'] == scene
        assert client.get('/config').json()['capabilities']['shared_projects']
    finally:
        server.db_memory.stop()


def test_real_archive_restores_project_choices_and_pauses_it():
    server = make_server()
    client = TestClient(server.app)
    try:
        command(server.experience, 'start', source=source())
        command(server.experience, 'choose', choice='archive')
        assert server.archive_manager.create_archive('shared-page').success
        command(server.experience, 'choose', choice='broaden')
        assert client.post('/api/archive/tasks', json={'operation': 'load', 'slot_name': 'shared-page', 'epoch': server.protocol.epoch}).status_code == 202
        assert finish(client)['status'] == 'completed'
        item = server.experience.snapshot()['projects'][0]
        assert item['status'] == 'paused' and item['phase'] == 'investigate'
        assert len(item['cards']) == 1 and item['resolution'] is None
    finally:
        server.db_memory.stop()
