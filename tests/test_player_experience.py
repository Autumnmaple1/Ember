"""No model calls; all files and PostgreSQL connections use suite isolation."""
import asyncio
import json
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.experience import Experience
from core.event_bus import EventBus
from core.world_protocol import WorldProtocol
from core.archive_jobs import ArchiveJobs
from core.player_api import install_player_api, history_query, presentation
from memory.postgres import connection
from archive import ArchiveManager
from archive.models import ArchiveResult
from archive.exporters.json_exporter import JsonExporter
from archive.importers.json_importer import JsonImporter
from server import EmberServer, ConnectionManager


def test_focus_uses_monotonic_time_and_requires_explicit_completion():
    elapsed = [10.0]
    experience = Experience(clock=lambda: elapsed[0])
    experience.activity('start', duration=1)
    elapsed[0] += 70
    item = experience.snapshot()['activities'][-1]
    assert item['status'] == 'running' and item['elapsed_seconds'] == 70
    experience.activity('pause')
    elapsed[0] += 1000
    assert experience.snapshot()['activities'][-1]['elapsed_seconds'] == 70
    experience.activity('resume')
    elapsed[0] += 5
    item = experience.activity('complete', summary='实际完成两页笔记')['activities'][-1]
    assert item['elapsed_seconds'] == 75 and item['summary'] == '实际完成两页笔记'


def test_reload_or_restore_pauses_focus_without_inventing_offline_work():
    elapsed = [0.0]
    experience = Experience(clock=lambda: elapsed[0])
    experience.activity('start')
    elapsed[0] = 30
    experience.checkpoint()
    reloaded = Experience(clock=lambda: 90000)
    item = reloaded.snapshot()['activities'][-1]
    assert item['status'] == 'paused' and item['elapsed_seconds'] == 30


def test_favorites_are_idempotent_and_agreements_keep_unknown_fields():
    experience = Experience()
    source = {'message_id': str(uuid.uuid4()), 'content': '改天可以一起看书'}
    experience.favorite(source, True)
    experience.favorite(source, True)
    assert len(experience.snapshot()['favorites']) == 1
    item = experience.agreement('看书', source)['agreements'][0]
    experience.agreement('看书', source)
    assert len(experience.snapshot()['agreements']) == 1
    assert item['status'] == 'pending' and item['when'] is None and item['location'] is None
    assert item['source'] == source
    experience.update_agreement(item['id'], 'cancelled')
    assert experience.snapshot()['agreements'][0]['status'] == 'cancelled'


def test_activity_does_not_allow_duplicate_start_or_unverified_shared_work():
    experience = Experience()
    with pytest.raises(ValueError):
        experience.activity('start', shared=True)
    experience.activity('start')
    with pytest.raises(ValueError):
        experience.activity('start')
    assert experience.activity('interrupt')['activities'][-1]['status'] == 'interrupted'


def test_world_pause_freezes_clock_and_resume_preserves_continuity(monkeypatch):
    elapsed = [100.0]
    monkeypatch.setattr('core.event_bus.time.monotonic', lambda: elapsed[0])
    bus = EventBus()
    now = bus.logical_now
    bus.pause_world(True)
    elapsed[0] += 3600
    assert bus.logical_now == now
    bus.set_time_accel_factor(8)
    assert bus.logical_now == now
    bus.pause_world(False)
    elapsed[0] += 2
    assert bus.logical_now == now + 16


def test_epoch_drops_stale_events_and_never_replays_audio():
    protocol = WorldProtocol()
    old = protocol.epoch
    first = protocol.envelope({'type': 'state_update'})
    protocol.envelope({'type': 'audio', 'audio_base64': 'synthetic'})
    protocol.envelope({'type': 'world.snapshot'})
    assert first['state_version'] == 1
    assert [x['type'] for x in protocol.tail(old, 0)['events']] == ['state_update']
    protocol.rotate()
    assert protocol.envelope({'type': 'audio'}, old) is None
    assert protocol.tail(old, 0)['complete'] is False


def test_tail_reports_when_bounded_replay_is_incomplete():
    protocol = WorldProtocol()
    for _ in range(520):
        protocol.envelope({'type': 'message'})
    assert not protocol.tail(protocol.epoch, 0)['complete']
    assert protocol.tail(protocol.epoch, 519)['complete']


def test_scene_assets_use_structured_location_and_whitelist():
    state = {'客观情境': '深夜图书馆', 'scene': {'character_location': None}, 'P': 9}
    assert presentation(state, '2026-11-02 14:00:00')['background'] == 'neutral'
    state['scene']['character_location'] = '图书馆自习室'
    result = presentation(state, '2026-11-02 22:00:00')
    assert result['background'] == 'library' and result['period'] == 'night'
    assert result['emotion'] == 'happy' and not result['contact_actions']


def test_experience_archive_round_trip_and_legacy_archive_default(tmp_path):
    experience = Experience()
    experience.activity('start')
    (tmp_path / 'export').mkdir()
    assert JsonExporter(str(tmp_path / 'export')).run().success
    experience.activity('interrupt')
    assert JsonImporter(str(tmp_path / 'export')).run().success
    experience.reload()
    assert experience.snapshot()['activities'][-1]['status'] == 'paused'
    (tmp_path / 'legacy').mkdir()
    assert JsonImporter(str(tmp_path / 'legacy')).run().success
    experience.reload()
    assert experience.snapshot()['activities'] == []


def test_archive_task_reports_real_phases_and_rejects_overlap():
    release, finished = threading.Event(), threading.Event()
    manager = SimpleNamespace(_in_progress=False, recovery_required=False, event_bus=None)
    manager.set_progress_callback = lambda callback: setattr(manager, 'callback', callback)
    def save():
        manager.callback('等待合成任务', 0)
        release.wait(2)
        return ArchiveResult(success=True, message='真实完成')
    manager.quick_save = save
    def publish(payload):
        if payload['task']['status'] == 'completed': finished.set()
    jobs = ArchiveJobs(manager, publish, lambda: None)
    initial = jobs.start('quick-save')
    with pytest.raises(ValueError): jobs.start('quick-save')
    release.set()
    assert finished.wait(3)
    assert jobs.snapshot()['status'] == 'completed'
    assert jobs.snapshot()['task_id'] == initial['task_id']


def test_interrupted_restore_requires_recovery_on_restart():
    path = Path('data/state/archive-job.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'task_id': 'lost', 'operation': 'load', 'status': 'running'}))
    manager = SimpleNamespace(recovery_required=False)
    jobs = ArchiveJobs(manager, lambda _: None, lambda: None)
    assert jobs.snapshot()['recovery_required'] and manager.recovery_required


def test_failed_restore_recovery_requirement_survives_another_restart():
    path = Path('data/state/archive-job.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'task_id': 'failed', 'operation': 'load', 'status': 'failed', 'recovery_required': True, 'revision': 4}))
    manager = SimpleNamespace(recovery_required=False)
    jobs = ArchiveJobs(manager, lambda _: None, lambda: None)
    assert manager.recovery_required and jobs.snapshot()['status'] == 'failed'


def test_unreadable_task_journal_keeps_a_recovery_entry():
    path = Path('data/state/archive-job.json')
    path.parent.mkdir(parents=True)
    path.write_text('{broken journal')
    manager = SimpleNamespace(recovery_required=False)
    jobs = ArchiveJobs(manager, lambda _: None, lambda: None)
    assert manager.recovery_required and jobs.snapshot()['phase']


def player_server():
    server = object.__new__(EmberServer)
    server.app = FastAPI()
    server.protocol = WorldProtocol()
    server.event_bus = EventBus()
    server.experience = Experience()
    server.archive_jobs = SimpleNamespace(busy=False, snapshot=lambda: None)
    server.loop = None
    server.current_ai_msg_id = server.current_ai_turn_id = None
    server.current_full_text = ''
    server.config_snapshot = lambda: {'experience': server.experience.snapshot(), 'turn': None, **server.protocol.snapshot()}
    server.db_memory = SimpleNamespace(get_history=lambda _: [])
    install_player_api(server)
    return server


def test_player_api_rejects_stale_epoch_and_does_not_write_records():
    server = player_server()
    client = TestClient(server.app)
    response = client.post('/experience', json={'action': 'start', 'epoch': 'old'})
    assert response.status_code == 409
    assert server.experience.snapshot()['activities'] == []


def test_restore_started_during_history_read_cannot_return_mixed_snapshot():
    server = player_server()
    def query(_limit):
        server.event_bus.paused = True
        return []
    server.db_memory.get_history = query
    response = TestClient(server.app).get('/sync')
    assert response.status_code == 409


def test_source_search_and_bookmark_api_use_canonical_messages():
    key = str(uuid.uuid4())
    with connection() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO message_list(sender,text,message_id) VALUES(%s,%s,%s)', ('assistant', '完成 100% 计划', key))
    server = player_server()
    client = TestClient(server.app)
    search = client.get('/history/search', params={'q': '100%'}).json()
    assert any(x['message_id'] == key for x in search['items'])
    source = client.get('/history/source/' + key).json()
    assert any(x['message_id'] == key for x in source['items'])
    response = client.post('/experience', json={'action': 'favorite', 'epoch': server.protocol.epoch, 'message_id': key})
    assert response.status_code == 200
    assert response.json()['experience']['favorites'][0]['content'] == '完成 100% 计划'


def test_missing_source_cannot_become_an_agreement():
    server = player_server()
    client = TestClient(server.app)
    response = client.post('/experience', json={'action': 'agreement', 'epoch': server.protocol.epoch,
        'message_id': str(uuid.uuid4()), 'title': '伪造的约定'})
    assert response.status_code == 400 and not server.experience.snapshot()['agreements']


def test_old_tts_is_discarded_after_restore_even_if_it_completes():
    server = object.__new__(EmberServer)
    server.protocol = WorldProtocol()
    old = server.protocol.epoch
    server._tts_semaphore = asyncio.Semaphore(3)
    async def synthesize(_):
        server.protocol.rotate()
        return 'synthetic'
    server.tts_manager = SimpleNamespace(generate_base64=synthesize)
    server.manager = SimpleNamespace(broadcast=AsyncMock(), _safe_send=AsyncMock())
    asyncio.run(server._process_tts('你好', epoch=old))
    server.manager.broadcast.assert_not_awaited()


def test_recovery_failure_keeps_world_paused():
    bus = EventBus()
    manager = ArchiveManager(event_bus=bus)
    manager.recovery_required = True
    manager._resume_system()
    assert bus.paused


def test_failed_record_write_does_not_publish_unsaved_player_facts(monkeypatch):
    experience = Experience()
    before = experience.snapshot()
    def fail(*_args, **_kwargs):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError): experience.activity('start')
    assert experience.snapshot() == before


def test_malformed_activity_archive_is_rejected():
    Path('config/experience.json').write_text(json.dumps({'version': 1, 'activities': [{'status': 'running', 'elapsed_seconds': -2, 'target_seconds': 60}]}))
    with pytest.raises(ValueError): Experience()


def test_failed_compression_keeps_previous_checkpoint(monkeypatch, tmp_path):
    from archive.utils.compress import compress_archive
    import zipfile
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'state.json').write_text('{"good":true}')
    target = tmp_path / 'previous.ember'
    assert compress_archive(str(source), str(target))
    previous = target.read_bytes()
    def fail(*_args, **_kwargs): raise OSError('synthetic full disk')
    monkeypatch.setattr(zipfile.ZipFile, 'write', fail)
    assert not compress_archive(str(source), str(target))
    assert target.read_bytes() == previous


def test_archive_paths_cannot_escape_slots():
    manager = ArchiveManager()
    for name in ('../outside', '..\\outside', 'C:\\outside', 'a/../b', ''):
        with pytest.raises(Exception): manager._archive_path(name)


def test_snapshot_and_archive_progress_have_no_lock_inversion():
    protocol = WorldProtocol()
    manager = SimpleNamespace(_in_progress=False, recovery_required=False, event_bus=None, set_progress_callback=lambda _: None)
    jobs = ArchiveJobs(manager, protocol.envelope, lambda: None)
    jobs.current = {'task_id': 'synthetic', 'status': 'running'}
    def read():
        for _ in range(100):
            with protocol.lock: jobs.snapshot()
    def write():
        for _ in range(100): jobs._phase('synthetic progress', 0)
    readers = threading.Thread(target=read, daemon=True)
    writers = threading.Thread(target=write, daemon=True)
    readers.start(); writers.start()
    readers.join(5); writers.join(5)
    assert not readers.is_alive() and not writers.is_alive()
