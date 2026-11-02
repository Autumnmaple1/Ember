import threading
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.event_bus import EventBus, Event
from core.turns import Turn, TurnCancelled
from brain.core import Brain
from brain.llm_client import LLMClient
from config.settings import settings
from memory.db_memory import DBMemory
from memory.postgres import connection, initialize_history
from memory.short_term import ShortTermMemory


def make_brain():
    bus = EventBus()
    memory = ShortTermMemory(base_prompt='合成测试角色')
    state = SimpleNamespace(prepare_dialogue=Mock(), prompt_injection='', speaking_prompt_injection='')
    tools = Mock()
    tools.build_system_prompt_with_tools.side_effect = lambda prompt: prompt
    tools.extract_tool_calls.return_value = []
    tools.has_tool_calls.return_value = False
    tools.remove_tool_calls.side_effect = lambda text: text
    with patch('brain.core.LLMClient', return_value=Mock()):
        brain = Brain(bus, state, memory, Mock(), tools)
    return brain, bus, memory, state


def wait_for_turn(bus, done):
    assert done.wait(5), 'The cancelled worker did not reach its terminal state'
    assert bus.pending_dialogues == 0


def test_completion_and_cancel_have_one_terminal_winner():
    completed = Turn('complete', 1000)
    assert completed.finish('completed')
    assert not completed.request_cancel()
    assert not completed.finish('interrupted')
    cancelled = Turn('cancel', 1000)
    assert cancelled.request_cancel()
    assert cancelled.status == 'stopping'
    with pytest.raises(TurnCancelled):
        cancelled.finish('completed')
    assert cancelled.finish('interrupted')
    assert not cancelled.finish('failed')


def test_a_stale_turn_id_cannot_stop_the_current_turn():
    bus = EventBus()
    current = bus.turns.begin(1000)
    assert bus.turns.cancel('old-id') is None
    assert not current.cancelled.is_set()


def test_cancelling_during_scene_preparation_waits_for_the_stage_and_never_starts_llm():
    brain, bus, memory, state = make_brain()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    def prepare(_text):
        entered.set()
        assert release.wait(5)
    state.prepare_dialogue.side_effect = prepare
    bus.subscribe('turn.finished', lambda _event: done.set())
    key = str(uuid.uuid4())
    bus.publish(Event('user.input', {'text': '合成场景变化', 'message_id': key}))
    try:
        assert entered.wait(5)
        snapshot = bus.turns.cancel(key)
        assert snapshot['status'] == 'stopping'
        assert not done.is_set()
    finally:
        release.set()
    wait_for_turn(bus, done)
    brain.llm_client.stream_chat.assert_not_called()
    assert bus.turns.snapshot()['status'] == 'interrupted'
    assert len(memory.memory) == 1


def test_stream_cancel_saves_only_the_interrupted_fragment_and_releases_the_worker():
    brain, bus, memory, _state = make_brain()
    db = DBMemory(bus)
    done = threading.Event()
    completed = []
    brain.llm_client.stream_chat.return_value = iter(['<response>已经说出的片段', '不应继续的内容</response>'])
    def cancel_after_first(event):
        if event.data.get('text'):
            bus.turns.cancel(event.data['turn_id'])
    bus.subscribe('llm.chunk', cancel_after_first)
    bus.subscribe('llm.finished', completed.append)
    bus.subscribe('turn.finished', lambda _event: done.set())
    key = str(uuid.uuid4())
    try:
        bus.publish(Event('user.input', {'text': '请说一段测试内容', 'message_id': key}))
        wait_for_turn(bus, done)
        assert db.flush()
        turn = bus.turns.current
        assert turn.status == 'interrupted'
        assert completed == []
        assert memory.memory[-1]['completion_status'] == 'interrupted'
        assert '不应继续' not in memory.memory[-1]['content']
        with connection() as conn, conn.cursor() as cur:
            cur.execute('SELECT text,completion_status,turn_id FROM message_list WHERE message_id=%s', (turn.message_id,))
            assert cur.fetchone() == ('已经说出的片段', 'interrupted', key)
        saved = next(row for row in db.get_history(100) if row['message_id'] == turn.message_id)
        assert saved['completion_status'] == 'interrupted'
    finally:
        db.stop()


def test_a_cancelled_thought_only_turn_creates_no_assistant_dialogue():
    brain, bus, memory, _state = make_brain()
    brain.llm_client.stream_chat.return_value = iter(['<thought>还没开口', '不应继续'])
    bus.subscribe('llm.chunk', lambda event: bus.turns.cancel(event.data['turn_id']))
    brain.process_dialogue('合成问题')
    assert bus.turns.snapshot()['status'] == 'interrupted'
    assert all(message['role'] == 'user' for message in memory.memory)


def test_stream_transport_is_closed_to_unblock_a_pending_read():
    entered, released = threading.Event(), threading.Event()
    class BlockingStream:
        closed = False
        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='一段', reasoning_content=None))], usage=None)
            entered.set()
            assert released.wait(5)
        def close(self):
            self.closed = True
            released.set()
    stream = BlockingStream()
    client = object.__new__(LLMClient)
    client.large_client = Mock()
    client.small_client = Mock()
    client.large_client.chat.completions.create.return_value = stream
    token = Turn('transport', 1000)
    received, cancelled = [], []
    def consume():
        try:
            received.extend(client.stream_chat(settings.LARGE_LLM, [], cancel_token=token))
        except TurnCancelled:
            cancelled.append(True)
    worker = threading.Thread(target=consume)
    worker.start()
    try:
        assert entered.wait(5)
        token.request_cancel()
        worker.join(5)
        assert not worker.is_alive()
        assert stream.closed and cancelled == [True]
        assert received == ['一段']
    finally:
        released.set()
        worker.join(5)


def test_interrupted_history_is_explicit_in_the_next_prompt():
    brain, _bus, memory, _state = make_brain()
    memory.add_message('assistant', '未完成的承诺片段', timestamp=1000, completion_status='interrupted')
    brain.llm_client.stream_chat.return_value = iter(['正常回复'])
    brain.process_dialogue('接着聊')
    messages = brain.llm_client.stream_chat.call_args.kwargs['messages']
    assert '已中断的未完成片段，不是完整承诺' in messages[0]['content']
    assert messages[1] == {'role': 'assistant', 'content': '未完成的承诺片段'}
    assert messages[-1] == {'role': 'user', 'content': '接着聊'}


def test_cancel_after_tool_result_prevents_a_follow_up_model_call():
    brain, bus, _memory, _state = make_brain()
    brain.llm_client.stream_chat.return_value = iter(['<tool_call>{"name":"synthetic"}</tool_call>'])
    brain.tool_processor.extract_tool_calls.return_value = [{'name': 'synthetic', 'parameters': {}}]
    def execute(*_args, **_kwargs):
        bus.turns.cancel(bus.turns.current.id)
        return []
    brain.tool_processor.execute_tool_calls.side_effect = execute
    brain.process_dialogue('工具期间停止')
    assert bus.turns.snapshot()['status'] == 'interrupted'
    assert brain.llm_client.stream_chat.call_count == 1


def test_transport_close_error_after_cancel_is_still_an_interruption():
    brain, bus, memory, _state = make_brain()
    def stream(**_kwargs):
        yield '已经显示的片段'
        bus.turns.cancel(bus.turns.current.id)
        raise OSError('transport closed during pending read')
    brain.llm_client.stream_chat.side_effect = stream
    brain.process_dialogue('连接关闭时停止')
    assert bus.turns.snapshot()['status'] == 'interrupted'
    assert memory.memory[-1]['completion_status'] == 'interrupted'


def test_legacy_schema_gets_safe_completion_defaults():
    assert settings.PG_DB.startswith('ember_test_')
    with connection() as conn, conn.cursor() as cur:
        cur.execute('ALTER TABLE message_list DROP COLUMN completion_status, DROP COLUMN turn_id')
        initialize_history(conn)
        cur.execute("SELECT count(*) FROM message_list WHERE completion_status IS NULL")
        assert cur.fetchone()[0] == 0


def test_interrupted_record_survives_archive_round_trip(tmp_path):
    from archive.exporters.postgres_exporter import PostgresExporter
    from archive.importers.postgres_importer import PostgresImporter
    assert settings.PG_DB.startswith('ember_test_')
    key = str(uuid.uuid4())
    with connection() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO message_list(sender,text,message_id,completion_status,turn_id) VALUES(%s,%s,%s,%s,%s)',
            ('assistant', '存档中的中断片段', key, 'interrupted', 'synthetic-turn'))
    assert PostgresExporter(str(tmp_path)).run().success
    result = PostgresImporter(str(tmp_path)).run()
    assert result.success, result.error
    with connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT text,completion_status,turn_id FROM message_list WHERE message_id=%s', (key,))
        assert cur.fetchone() == ('存档中的中断片段', 'interrupted', 'synthetic-turn')
