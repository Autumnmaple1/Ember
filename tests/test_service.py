"""Hosted control plane only in an isolated test DB/schema, never business IO."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import uuid

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from psycopg2 import sql
from config.settings import settings
from core.service_policy import install
from memory.postgres import connection
from service.auth import AuthConfig, install_auth, password_hash
from service.store import ServiceStore, BudgetExceeded
from service.runtime import WorldLock, ServiceRuntime
from tests.test_restore_experience import make_server, finish


@pytest.fixture
def store():
    assert settings.PG_DB.startswith('ember_test_')
    schema = 'ember_service_test_' + uuid.uuid4().hex
    store = ServiceStore(schema=schema, limits={'chat': 1, 'embedding': 1, 'tts': 0})
    store.initialize()
    try:
        yield store
    finally:
        install(None)
        with connection() as conn, conn.cursor() as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.fixture
def auth(store):
    return AuthConfig(password_hash('synthetic-access-code-long-enough'))


def app_for(store, auth):
    app = FastAPI()
    install_auth(app, store, auth)
    @app.get('/config')
    def config():
        return {'private': True}
    @app.post('/experience')
    def mutate():
        return {'success': True}
    @app.websocket('/ws/chat')
    async def chat(ws: WebSocket):
        await ws.accept()
        while True:
            try:
                message = await ws.receive_text()
            except WebSocketDisconnect:
                return
            await ws.send_text(message)
    return app


def login(client, label='合成手机'):
    response = client.post('/auth/login', json={'access_code': 'synthetic-access-code-long-enough', 'label': label})
    assert response.status_code == 200, response.text
    return {'X-Ember-CSRF': response.json()['csrf']}


def test_http_login_csrf_origin_and_private_routes(store, auth):
    client = TestClient(app_for(store, auth))
    assert client.get('/config').status_code == 401
    assert client.get('/history').status_code == 401
    assert client.post('/scene', json={}).status_code == 401
    assert client.get('/audio/private.wav').status_code == 401
    assert client.get('/live2d.min.js').status_code == 404, 'Public runtime reaches the static handler rather than auth'
    assert client.get('/auth/session').json()['required']
    assert client.post('/auth/login', json={'access_code': 'wrong'}).status_code == 401
    headers = login(client)
    assert client.get('/config').json()['private']
    assert 'httponly' in str(client.cookies).lower() or 'ember_session' in client.cookies
    assert client.post('/experience').status_code == 403
    assert client.post('/experience', headers=headers).status_code == 200
    assert client.post('/experience', headers={**headers, 'Origin': 'https://untrusted.example'}).status_code == 403
    assert client.get('/auth/devices').json()['items'][0]['label'] == '合成手机'
    assert client.post('/auth/logout', headers=headers).status_code == 200
    assert client.get('/config').status_code == 401


def test_revocation_applies_to_existing_websocket_and_cookie_survives_new_app(store, auth):
    app = app_for(store, auth)
    phone, desktop = TestClient(app), TestClient(app)
    login(phone, 'phone')
    headers = login(desktop, 'desktop')
    phone_id = phone.get('/auth/session').json()['session_id']
    reboot = TestClient(app_for(ServiceStore(schema=store.s), auth))
    reboot.cookies.update(phone.cookies)
    assert reboot.get('/config').status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with phone.websocket_connect('/ws/chat', headers={'Origin': 'https://untrusted.example'}):
            pass
    with phone.websocket_connect('/ws/chat', headers={'Origin': 'http://testserver'}) as ws:
        ws.send_text('first'); assert ws.receive_text() == 'first'
        assert desktop.post(f'/auth/devices/{phone_id}/revoke', headers=headers).status_code == 200
        ws.send_text('must be rejected')
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert phone.get('/config').status_code == 401
    assert desktop.get('/config').status_code == 200


def test_expired_sessions_and_credential_rotation_fail_closed(store, auth):
    client = TestClient(app_for(store, auth))
    login(client)
    new_app = TestClient(app_for(store, AuthConfig(password_hash('another-valid-owner-code'))))
    new_app.cookies.update(client.cookies)
    assert new_app.get('/config').status_code == 401
    with store.cursor() as c:
        c.execute(f"UPDATE {store.s}.sessions SET expires_at=now()-interval '1 second'")
    assert client.get('/config').status_code == 401


def test_login_attempts_are_persistent_and_limited(store):
    assert all(store.login_allowed('synthetic-ip') for _ in range(10))
    assert not ServiceStore(schema=store.s).login_allowed('synthetic-ip')


def test_readiness_uses_live_database_probe_and_recovers(store):
    server = make_server()
    server.service_runtime = SimpleNamespace(store=store)
    client = TestClient(server.app)
    try:
        assert client.get('/health/ready').status_code == 200
        original = store.connection
        store.connection = Mock(side_effect=OSError('synthetic outage'))
        assert client.get('/health/ready').status_code == 503
        assert client.get('/health/live').status_code == 200
        store.connection = original
        assert client.get('/health/ready').status_code == 200
    finally:
        server.db_memory.stop()


def test_world_tables_do_not_follow_owner_schema(store):
    from memory.postgres import initialize_history
    with connection() as conn, conn.cursor() as c:
        # Emulate PostgreSQL's default "$user",public choosing an owner schema.
        c.execute(sql.SQL('SET search_path TO {},public').format(sql.Identifier(store.s)))
        initialize_history(conn)
        c.execute('SELECT current_schema() AS schema')
        assert c.fetchone()[0] == 'public'
        c.execute('SELECT count(*) FROM information_schema.tables WHERE table_schema=%s AND table_name=%s',
                  (store.s, 'message_list'))
        assert c.fetchone()[0] == 0


@pytest.mark.parametrize('statement', [
    "INSERT INTO ember_service.message_list (id,text) VALUES (1,'preserve me');",
    'COPY public.message_list (id,text) FROM stdin;\n1\tpreserve me\n\\.\n',
])
def test_unrecognized_archive_data_is_never_silently_empty(statement, tmp_path):
    from archive.importers.postgres_importer import PostgresImporter
    with pytest.raises(ValueError, match='拒绝当作空表'):
        PostgresImporter(str(tmp_path))._batch_insert(Mock(), 'message_list', statement)


def test_job_claim_is_atomic_and_crash_lease_is_recoverable(store):
    row = store.enqueue('maintenance.backup', 'same-backup')
    assert store.enqueue('maintenance.backup', 'same-backup')['id'] == row['id']
    with ThreadPoolExecutor(2) as pool:
        jobs = list(pool.map(store.claim, ['one','two']))
    assert sum(x is not None for x in jobs) == 1
    active = next(x for x in jobs if x)
    with store.cursor() as c:
        c.execute(f"UPDATE {store.s}.jobs SET lease_until=now()-interval '1 second'")
    recovered = ServiceStore(schema=store.s).claim('restarted')
    assert recovered['id'] == active['id'] and recovered['attempts'] == 2
    assert not store.complete(active['id'], active['owner'], {'wrong_owner': True})
    assert store.complete(recovered['id'], 'restarted', {'saved': True})
    assert store.claim('again') is None


def test_usage_cap_is_atomic_and_not_rewound_by_game_time(store):
    def reserve(_):
        try: return store.reserve('chat', 'test', 'dialogue')
        except BudgetExceeded: return None
    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(reserve, range(2)))
    assert sum(x is not None for x in rows) == 1
    store.finish_usage(next(x for x in rows if x), usage=SimpleNamespace(prompt_tokens=123, completion_tokens=4))
    row = store.snapshot()['usage']['chat']
    assert row['requests'] == 1 and row['input_tokens'] == 123 and row['output_tokens'] == 4


def test_model_and_tts_limits_apply_before_external_calls(store):
    from brain.llm_client import LLMClient
    from brain.tts import TTSManager, TTSGenerationError
    install(store)
    client = object.__new__(LLMClient)
    client.large_client = Mock()
    client.large_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='合成回复'))], usage=None)
    assert client.one_chat(settings.LARGE_LLM, []) == '合成回复'
    with pytest.raises(BudgetExceeded): client.one_chat(settings.LARGE_LLM, [])
    assert client.large_client.chat.completions.create.call_count == 1
    tts = TTSManager()
    tts._do_tts = AsyncMock(return_value=b'audio')
    with pytest.raises(TTSGenerationError, match='额度'):
        asyncio.run(tts.generate_base64('合成语音'))
    tts._do_tts.assert_not_called()


def test_world_restore_preserves_identity_jobs_and_usage(store, auth):
    token = 'synthetic-cookie'
    store.issue(token, auth.credential, 'device', 3600)
    job = store.enqueue('maintenance.backup', 'must-survive')
    store.reserve('chat')
    server = make_server()
    client = TestClient(server.app)
    try:
        epoch = server.protocol.epoch
        assert client.post('/api/archive/tasks', json={'operation': 'create', 'slot_name': 'service-test', 'epoch': epoch}).status_code == 202
        assert finish(client)['status'] == 'completed'
        assert client.post('/api/archive/tasks', json={'operation': 'load', 'slot_name': 'service-test', 'epoch': epoch}).status_code == 202
        assert finish(client)['status'] == 'completed'
        assert store.session(token, auth.credential)
        assert store.enqueue('maintenance.backup', 'must-survive')['id'] == job['id']
        with pytest.raises(BudgetExceeded): store.reserve('chat')
    finally:
        server.db_memory.stop()


def test_committed_backup_file_is_a_recoverable_job_receipt(store, auth, tmp_path):
    runtime = ServiceRuntime(store, auth)
    file = tmp_path/'already-written.ember'
    file.write_text('synthetic valid file')
    manager = SimpleNamespace(_archive_path=lambda _: file, get_archive_preview=lambda _: object())
    runtime.server = SimpleNamespace(archive_manager=manager)
    runtime.backup_control = Mock(return_value='synthetic-control.sql')
    assert runtime.backup({'id':'existing'})['recovered_receipt']
    runtime.backup_control.assert_called_once()


def test_only_one_process_can_own_the_same_world(tmp_path):
    lock = WorldLock(tmp_path/'world.lock')
    try:
        with pytest.raises(RuntimeError, match='已有运行进程'):
            WorldLock(tmp_path/'world.lock')
    finally: lock.close()
    WorldLock(tmp_path/'world.lock').close()
