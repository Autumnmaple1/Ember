"""Exercise actual archive IO and HTTP jobs against only ember_test_* databases."""
import json
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from archive import ArchiveManager
from core.event_bus import EventBus
from core.experience import Experience
from core.world_protocol import WorldProtocol
from core.archive_jobs import ArchiveJobs
from core.player_api import install_player_api
from memory.db_memory import DBMemory
from memory.short_term import ShortTermMemory
from persona.state_manager import StateManager
from server import EmberServer, ConnectionManager
from archive.importers.base import ImportResult


def make_server():
    server = object.__new__(EmberServer)
    server.app, server.event_bus = FastAPI(), EventBus()
    server.event_bus.proactive_channel = 'web'
    server.protocol, server.manager = WorldProtocol(), ConnectionManager()
    server.manager.protocol = server.protocol
    server.experience = Experience()
    server.event_bus.experience = server.experience
    server.memory = ShortTermMemory()
    server.state_manager = StateManager(server.event_bus, Mock(), server.memory, Mock())
    server.state_manager.ready = True
    server.state_manager.current_state = {'P': 5, 'A': 5, 'D': 5, '客观情境': '图书馆，活动待确认',
        '近期综合轨迹': '', '内心活动': '', '近期目标': '', '对应时间': server.event_bus.formatted_logical_now,
        'scene': {'character_location': '图书馆', 'user_location': None, 'communication_mode': 'unknown', 'remote_contact_confirmed': False}}
    Path('config/state.json').write_text(json.dumps(server.state_manager.current_state), encoding='utf-8')
    server.db_memory = DBMemory(server.event_bus)
    server.episodic_memory = SimpleNamespace(ready=True)
    server.brain = SimpleNamespace(_is_processing=False)
    server.heartbeat = SimpleNamespace(start=lambda: None, stop=lambda: None, pause=lambda: None)
    server.loop = None
    server.current_ai_msg_id = server.current_ai_turn_id = None
    server.current_full_text = ''
    server._accepted_inputs = OrderedDict()
    server.archive_manager = ArchiveManager(event_bus=server.event_bus, state_manager=server.state_manager,
        heartbeat=server.heartbeat, short_term_memory=server.memory, db_memory=server.db_memory)
    server.archive_jobs = ArchiveJobs(server.archive_manager, server.safe_broadcast, server._restore_epoch)
    server._setup_routes()
    server._setup_event_handlers()
    install_player_api(server)
    return server


def finish(client):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        task = client.get('/api/archive/tasks/current').json()['task']
        if task and task['status'] != 'running': return task
        time.sleep(0.03)
    raise AssertionError('Archive worker failed to finish')


def test_real_archive_job_restores_activity_and_rotates_world_once():
    server = make_server()
    client = TestClient(server.app)
    old = server.protocol.epoch
    try:
        server.experience.activity('start', duration=15)
        result = client.post('/api/archive/tasks', json={'operation': 'create', 'slot_name': 'safe-checkpoint', 'epoch': old})
        assert result.status_code == 202
        assert finish(client)['status'] == 'completed'
        server.experience.activity('complete', summary='保存之后发生的测试变化')
        result = client.post('/api/archive/tasks', json={'operation': 'load', 'slot_name': 'safe-checkpoint', 'epoch': old})
        assert result.status_code == 202
        task = finish(client)
        assert task['status'] == 'completed', task
        snapshot = client.get('/config').json()
        assert snapshot['epoch'] != old
        assert snapshot['experience']['activities'][-1]['status'] == 'paused'
        assert snapshot['experience']['activities'][-1]['summary'] == ''
        assert not snapshot['status']['paused']
        assert not snapshot['status']['recovery_required']
        assert client.get('/sync').json()['boundary']['epoch'] == snapshot['epoch']
        assert client.post('/experience', json={'action': 'start', 'epoch': old}).status_code == 409
    finally:
        server.db_memory.stop()


def test_archive_waits_for_accepted_work_and_reports_it():
    server = make_server()
    client = TestClient(server.app)
    try:
        with server.event_bus.pending_condition: server.event_bus.pending_dialogues = 1
        response = client.post('/api/archive/tasks', json={'operation': 'quick-save', 'epoch': server.protocol.epoch})
        assert response.status_code == 202
        assert client.get('/config').json()['status']['paused']
        assert client.get('/sync').status_code == 409
        with server.event_bus.pending_condition:
            server.event_bus.pending_dialogues = 0
            server.event_bus.pending_condition.notify_all()
        assert finish(client)['status'] == 'completed'
    finally:
        with server.event_bus.pending_condition:
            server.event_bus.pending_dialogues = 0
            server.event_bus.pending_condition.notify_all()
        server.db_memory.stop()


def test_failed_import_rolls_back_player_records_and_reports_failure(monkeypatch):
    server = make_server()
    client = TestClient(server.app)
    try:
        server.experience.activity('start')
        assert server.archive_manager.create_archive('before').success
        server.experience.activity('complete', summary='保存之后的真实测试记录')
        monkeypatch.setattr('archive.manager.PostgresImporter.run', lambda _: ImportResult(success=False, error='synthetic import failure'))
        assert client.post('/api/archive/tasks', json={'operation': 'load', 'slot_name': 'before', 'epoch': server.protocol.epoch}).status_code == 202
        task = finish(client)
        assert task['status'] == 'failed'
        assert server.experience.snapshot()['activities'][-1]['summary'] == '保存之后的真实测试记录'
        assert not server.event_bus.paused
    finally:
        server.db_memory.stop()


def test_corrupt_archive_is_rejected_before_state_changes():
    server = make_server()
    try:
        assert server.archive_manager.create_archive('before').success
        archive = Path('data/archives/before.ember')
        with zipfile.ZipFile(archive) as source:
            files = {name: source.read(name) for name in source.namelist()}
        files['experience.json'] = b'{"version":1,"activities":[]}'
        with zipfile.ZipFile(archive, 'w') as target:
            for name, value in files.items(): target.writestr(name, value)
        with pytest.raises(Exception, match='校验'):
            server.archive_manager.load_archive('before')
        assert not server.event_bus.paused
    finally:
        server.db_memory.stop()


def test_manual_archive_creation_cannot_overwrite_or_use_reserved_names():
    server = make_server()
    client = TestClient(server.app)
    try:
        assert server.archive_manager.create_archive('keep-me').success
        before = Path('data/archives/keep-me.ember').read_bytes()
        for slot, code in [('keep-me', 409), ('quick_save', 400), ('auto_backup_manual', 400)]:
            result = client.post('/api/archive/tasks', json={'operation': 'create', 'slot_name': slot, 'epoch': server.protocol.epoch})
            assert result.status_code == code
        assert Path('data/archives/keep-me.ember').read_bytes() == before
        assert not server.archive_jobs.busy
    finally:
        server.db_memory.stop()
