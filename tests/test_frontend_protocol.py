"""Protocol checks use stubs, temporary files and the suite's isolated database."""
import asyncio
import json
import uuid
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.event_bus import EventBus, Event
from memory.db_memory import DBMemory
from server import EmberServer, ConnectionManager
from brain.tts import TTSGenerationError


def stub_server():
    server = object.__new__(EmberServer)
    server.app = FastAPI()
    server.manager = ConnectionManager()
    server.event_bus = EventBus()
    server.state_manager = SimpleNamespace(ready=True)
    server.brain = SimpleNamespace(_is_processing=False)
    server.db_memory = Mock()
    server.db_memory.get_message.return_value = None
    server._accepted_inputs = OrderedDict()
    server._setup_routes()
    return server


def test_retried_input_is_acknowledged_without_running_dialogue_twice():
    server = stub_server()
    inputs = []
    server.event_bus.subscribe('user.input', lambda event: inputs.append(event.data))
    key = str(uuid.uuid4())
    with TestClient(server.app).websocket_connect('/ws/chat') as socket:
        message = {'content': '合成测试消息', 'client_request_id': key}
        socket.send_json(message)
        assert socket.receive_json()['client_request_id'] == key
        socket.send_json({**message, 'retry': True})
        assert socket.receive_json()['message_id'] == key
        socket.send_json({**message, 'content': '不同内容'})
        assert socket.receive_json()['type'] == 'error'
    assert len(inputs) == 1


def test_retry_after_restart_uses_saved_message_and_database_failure_is_visible():
    server = stub_server()
    key = str(uuid.uuid4())
    server.db_memory.get_message.return_value = {'content': '原消息', 'message_id': key, 'timestamp': 1000}
    inputs = []
    server.event_bus.subscribe('user.input', inputs.append)
    with TestClient(server.app).websocket_connect('/ws/chat') as socket:
        socket.send_json({'content': '原消息', 'client_request_id': key, 'retry': True})
        assert socket.receive_json()['message_id'] == key
        server.db_memory.get_message.side_effect = RuntimeError('database offline')
        socket.send_json({'content': '原消息', 'client_request_id': key, 'retry': True})
        assert socket.receive_json()['type'] == 'error'
    assert inputs == []


def test_invalid_input_id_cannot_be_used_as_a_spool_path():
    server = stub_server()
    with TestClient(server.app).websocket_connect('/ws/chat') as socket:
        socket.send_json({'content': '测试', 'client_request_id': '../escape'})
        assert socket.receive_json()['type'] == 'error'
        socket.send_json({'content': '测试', 'client_request_id': 'urn:uuid:' + str(uuid.uuid4())})
        assert socket.receive_json()['type'] == 'error'
    server.db_memory.get_message.assert_not_called()


def test_cancel_reports_stopping_until_the_worker_confirms_terminal_state():
    server = stub_server()
    turn = server.event_bus.turns.begin(1000)
    with TestClient(server.app).websocket_connect('/ws/chat') as socket:
        socket.send_json({'type': 'cancel_turn', 'turn_id': turn.id})
        result = socket.receive_json()
        assert result['type'] == 'turn.stopping'
        assert result['status'] == 'stopping'
        socket.send_json({'type': 'cancel_turn', 'turn_id': 'stale'})
        assert socket.receive_json()['action'] == 'cancel_turn'
        turn.finish('interrupted')
        turn.release()
        socket.send_json({'type': 'cancel_turn', 'turn_id': turn.id})
        assert socket.receive_json()['status'] == 'interrupted'


def test_late_cancel_preserves_an_already_completed_turn():
    server = stub_server()
    turn = server.event_bus.turns.begin(1000)
    turn.finish('completed')
    turn.release()
    with TestClient(server.app).websocket_connect('/ws/chat') as socket:
        socket.send_json({'type': 'cancel_turn', 'turn_id': turn.id})
        result = socket.receive_json()
        assert result['type'] == 'turn.finished'
        assert result['status'] == 'completed'
    assert not turn.cancelled.is_set()


def test_cancel_does_not_confirm_completion_before_worker_release():
    server = stub_server()
    turn = server.event_bus.turns.begin(1000)
    turn.finish('completed')
    with TestClient(server.app).websocket_connect('/ws/chat') as socket:
        socket.send_json({'type': 'cancel_turn', 'turn_id': turn.id})
        result = socket.receive_json()
        assert result['type'] == 'turn.stopping'
        assert result['released'] is False
        turn.release()
        socket.send_json({'type': 'cancel_turn', 'turn_id': turn.id})
        assert socket.receive_json()['type'] == 'turn.finished'


def test_manual_audio_is_targeted_and_never_reads_monologue():
    server = object.__new__(EmberServer)
    server._tts_semaphore = asyncio.Semaphore(3)
    server.tts_manager = SimpleNamespace(generate_base64=AsyncMock(return_value='synthetic-audio'))
    server.manager = SimpleNamespace(_safe_send=AsyncMock(), broadcast=AsyncMock())
    target = object()
    asyncio.run(server._process_tts('<think>私有推理</think><thought>私下的独白</thought><response>你好</response>', target=target, message_id='reply', request_id='request'))
    server.tts_manager.generate_base64.assert_awaited_once_with('你好')
    server.manager.broadcast.assert_not_awaited()
    payload = json.loads(server.manager._safe_send.call_args.args[1])
    assert payload['message_id'] == 'reply'
    assert payload['request_id'] == 'request'


def test_tts_failure_returns_a_request_correlated_error():
    server = object.__new__(EmberServer)
    server._tts_semaphore = asyncio.Semaphore(3)
    server.tts_manager = SimpleNamespace(generate_base64=AsyncMock(side_effect=RuntimeError('unavailable')))
    server.manager = SimpleNamespace(_safe_send=AsyncMock(), broadcast=AsyncMock())
    asyncio.run(server._process_tts('你好', target=object(), request_id='request'))
    payload = json.loads(server.manager._safe_send.call_args.args[1])
    assert payload['type'] == 'tts_error'
    assert payload['request_id'] == 'request'


def test_automatic_tts_failure_is_broadcast_with_message_and_turn():
    server = object.__new__(EmberServer)
    server._tts_semaphore = asyncio.Semaphore(3)
    server.tts_manager = SimpleNamespace(generate_base64=AsyncMock(side_effect=
        TTSGenerationError('http_403', '语音服务拒绝请求（403）')))
    server.manager = SimpleNamespace(_safe_send=AsyncMock(), broadcast=AsyncMock())
    asyncio.run(server._process_tts('你好', message_id='reply', turn_id='turn'))
    payload = server.manager.broadcast.call_args.args[0]
    assert payload == {'type': 'tts_error', 'code': 'http_403', 'content': '语音服务拒绝请求（403）',
        'message_id': 'reply', 'turn_id': 'turn', 'request_id': None}
    server.manager._safe_send.assert_not_awaited()


def test_empty_tts_result_never_broadcasts_invalid_audio():
    server = object.__new__(EmberServer)
    server._tts_semaphore = asyncio.Semaphore(3)
    server.tts_manager = SimpleNamespace(generate_base64=AsyncMock(return_value=None))
    server.manager = SimpleNamespace(_safe_send=AsyncMock(), broadcast=AsyncMock())
    asyncio.run(server._process_tts('你好', message_id='reply'))
    payload = server.manager.broadcast.call_args.args[0]
    assert payload['type'] == 'tts_error'
    assert payload['code'] == 'no_audio'


def test_persistence_ack_is_only_emitted_after_commit():
    bus = EventBus()
    db = DBMemory(bus)
    received = []
    bus.subscribe('message.persisted', lambda event: received.append(event.data['message_id']))
    try:
        key = str(uuid.uuid4())
        bus.publish(Event('user.input', {'text': '确认写入测试', 'message_id': key}))
        assert db.flush()
        assert key in received
        assert db.get_message(key)['content'] == '确认写入测试'
    finally:
        db.stop()


def test_failed_write_never_acknowledges_saved_and_replay_acknowledges_success():
    bus = EventBus()
    received = []
    bus.subscribe('message.persisted', lambda event: received.append(event.data['message_id']))
    key = str(uuid.uuid4())
    with patch.object(DBMemory, '_init_db'):
        db = DBMemory(bus)
    db.MAX_ATTEMPTS = 1
    try:
        with patch.object(db, '_write', side_effect=RuntimeError('simulated failed transaction')):
            bus.publish(Event('user.input', {'text': '失败不会标记已保存', 'message_id': key}))
            db.store_queue.join()
        assert received == []
        assert (db.spool_dir / (key + '.json')).is_file()
        db.ready = True
        with patch.object(db, '_write'):
            db.retry_failed()
            assert db.flush()
        assert received == [key]
    finally:
        db.stop()
