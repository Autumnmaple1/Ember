"""Three-layer memory contracts; model calls and embeddings are synthetic."""
import copy
import json
import time
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from config.settings import settings
from core.event_bus import EventBus, Event
from memory.short_term import ShortTermMemory
from memory.working_memory import WorkingMemory
from memory.memory_process import Hippocampus
from memory.episodic_memory import EpisodicMemory
from memory.journal import append_log, consume_prefix
from memory.postgres import connection
from tools.base import ToolResult
from tools.registry import ToolRegistry
from tools.processor import ToolCallProcessor
from tools.builtin.memory_query_tool import MemoryQueryTool
from archive.exporters.json_exporter import JsonExporter
from archive.importers.json_importer import JsonImporter
from tests.test_restore_experience import make_server, finish
from tests.test_persistence_scene import make_state_manager
from tests.test_turn_cancellation import make_brain


def harness(window=2, with_store=True):
    bus = EventBus()
    memory = ShortTermMemory(max_memory_size=window)
    store_llm = Mock()
    store_llm.get_embedding.return_value = [0.1] * 1536
    with patch('memory.episodic_memory.LLMClient', return_value=store_llm):
        store = EpisodicMemory(bus) if with_store else None
    llm = Mock()
    def encode(_model, messages, **_kwargs):
        context = json.loads(messages[-1]['content'])
        sources = Hippocampus._sources(context['log'])
        return json.dumps({'memories': [{'content': x['content'], 'source_ids': [x['message_id']],
            'importance': 1, 'confidence': 0.8, 'keywords': ['pipeline-test']} for x in sources],
            'working_notes': [{'kind': 'preference', 'source_id': x['message_id'], 'quote': x['content']} for x in sources]})
    llm.one_chat.side_effect = encode
    llm._extract_json.side_effect = json.loads
    with patch('memory.memory_process.LLMClient', return_value=llm):
        hippo = Hippocampus(bus, memory.working, memory)
    return bus, memory, store, hippo, llm


def add(memory, text, role='user'):
    key = str(uuid.uuid4())
    memory.add_message(role, text, timestamp=1793618400000, message_id=key)
    return key


def test_complete_recall_payload_is_distinct_from_short_ui_preview():
    registry = ToolRegistry(); tool = MemoryQueryTool(); registry.register(tool)
    processor = ToolCallProcessor(registry)
    records = [{'id': 1, 'content': '前缀' * 80 + '第一条末尾重要事实', 'time': '2026-11-02 15:20:00', 'confidence': 0.8,
        'metadata': {'source_ids': ['source-a']}}, {'id': 2, 'content': '第二条重要事实'}, {'id': 3, 'content': '第三条重要事实'}]
    result = ToolResult.ok(data={'found': True, 'episodic_records': records, 'episodic_memories': ['预览文本'],
        'graph_entities': {'某人': '具体偏好'}, 'graph_relations': ['某人 喜欢 某活动']})
    rendered = processor.format_tool_results_for_prompt([{'tool_name': 'memory_query', 'result': result}])
    for expected in ('第一条末尾重要事实', '第二条重要事实', '第三条重要事实', '具体偏好', '某活动', 'source-a', '0.8'):
        assert expected in rendered
    assert len(tool.summarize_result(result)) <= 200


def test_recall_budget_omits_whole_records_with_an_explicit_count():
    payload = json.loads(MemoryQueryTool().format_result_for_model(ToolResult.ok(data={
        'found': True, 'episodic_records': [{'content': 'x' * 40000}, {'content': '仍可读的完整记录'}]})))
    assert payload['omitted'] == 1 and payload['episodic_records'] == [{'content': '仍可读的完整记录'}]


def test_evicted_turns_persist_to_middle_memory_before_window_drop():
    memory = ShortTermMemory(max_memory_size=2)
    key = add(memory, '我喜欢安静一点的环境')
    add(memory, '第二条'); add(memory, '第三条')
    assert len(memory.get_memory()['history']) == 2
    assert memory.working.snapshot()['sources'][0]['message_id'] == key
    restarted = ShortTermMemory(max_memory_size=2)
    assert '我喜欢安静一点的环境' in restarted.working.prompt_context()
    assert restarted.working.status()['pending_encoding']


def test_failed_middle_write_cannot_silently_evict_a_turn(monkeypatch):
    memory = ShortTermMemory(max_memory_size=1)
    add(memory, '不能丢掉的旧消息')
    monkeypatch.setattr(memory.working, 'save', Mock(side_effect=OSError('synthetic full disk')))
    with pytest.raises(OSError): add(memory, '新消息')
    assert any(x['content'] == '不能丢掉的旧消息' for x in memory.memory)
    assert '不能丢掉的旧消息' in Path('config/chat_history.log').read_text(encoding='utf-8')


def test_middle_notes_require_exact_sources_and_resolution_evidence():
    working = WorkingMemory()
    source = {'message_id': 'question', 'role': 'user', 'content': '这个地方怎么推导？', 'timestamp': 1}
    answer = {'message_id': 'answer', 'role': 'assistant', 'content': '先把等式两边同时除以二。', 'timestamp': 2}
    working.capture([source])
    notes = [{'kind': 'question', 'source_id': 'question', 'quote': source['content'],
        'resolved_by': 'answer', 'resolution_quote': answer['content']},
        {'kind': 'decision', 'source_id': 'missing', 'quote': '伪造决定'},
        {'kind': 'preference', 'source_id': 'question', 'quote': '来源没有说过的偏好'}]
    working.add_job('log', [], notes, [answer])
    kept = working.snapshot()['notes']
    assert len(kept) == 1 and kept[0]['status'] == 'addressed'
    assert kept[0]['resolution']['source']['message_id'] == 'answer'


def test_corrupt_middle_memory_fails_explicitly():
    Path('config/working_memory.json').write_text('{"version":99}', encoding='utf-8')
    with pytest.raises(ValueError): WorkingMemory()


def test_log_is_consumed_only_after_vector_and_database_acknowledgement():
    bus, memory, store, hippo, _llm = harness()
    try:
        key = add(memory, '一个有来源的偏好')
        bus.publish(Event('memory.preprocess', {}))
        store.store_queue.join()
        assert hippo.working.snapshot()['jobs'] == []
        assert Path('config/chat_history.log').read_text(encoding='utf-8') == ''
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT metadata,time FROM episodic_memory WHERE metadata->'source_ids' ? %s", (key,))
            metadata, stamp = cur.fetchone()
            assert metadata['source_ids'] == [key] and metadata['evidence'][0]['content'] == '一个有来源的偏好'
            assert stamp.year == 2026
        result = hippo.query_memory('偏好', ['pipeline-test'])
        assert 'episodic_records' in result and result['found']
    finally: store.stop()


def test_missing_consumer_keeps_durable_job_and_restarts_without_recoding():
    bus, memory, _store, hippo, llm = harness(with_store=False)
    add(memory, '稍后入库的内容')
    original = Path('config/chat_history.log').read_text(encoding='utf-8')
    bus.publish(Event('memory.preprocess', {}))
    assert hippo.working.snapshot()['jobs'] and hippo.last_error
    assert Path('config/chat_history.log').read_text(encoding='utf-8') == original
    new_bus, new_memory, store, restarted, new_llm = harness()
    try:
        new_bus.publish(Event('memory.preprocess', {})); store.store_queue.join()
        new_llm.one_chat.assert_not_called()
        assert restarted.working.snapshot()['jobs'] == [] and llm.one_chat.call_count == 1
        assert Path('config/chat_history.log').read_text(encoding='utf-8') == ''
    finally: store.stop()


def test_vector_failure_keeps_original_and_same_ids_retry_without_duplicate(monkeypatch):
    bus, memory, store, hippo, llm = harness()
    try:
        add(memory, '必须可以重试的内容')
        store.llm_client.get_embedding.return_value = None
        bus.publish(Event('memory.preprocess', {})); store.store_queue.join()
        job = hippo.working.snapshot()['jobs'][0]
        key = job['memories'][0]['memory_id']
        assert job['text'] == Path('config/chat_history.log').read_text(encoding='utf-8')
        assert (store.spool_dir / (key + '.json')).exists()
        store.llm_client.get_embedding.return_value = [0.1] * 1536
        bus.publish(Event('memory.preprocess', {})); store.store_queue.join()
        assert not hippo.working.snapshot()['jobs'] and llm.one_chat.call_count == 1
        with connection() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM episodic_memory WHERE memory_id=%s', (key,))
            assert cur.fetchone()[0] == 1
    finally: store.stop()


@pytest.mark.parametrize('already_consumed', [False, True])
def test_restart_finishes_commit_without_removing_new_suffix(already_consumed):
    bus, memory, _store, hippo, _llm = harness(with_store=False)
    append_log('config/chat_history.log', 'old-prefix')
    job = memory.working.add_job('old-prefix\n', [], [], [])
    memory.working.begin_commit(job['id'])
    if already_consumed: assert consume_prefix('old-prefix\n')
    append_log('config/chat_history.log', 'new-suffix')
    hippo.after_restore(); hippo._complete_jobs()
    assert Path('config/chat_history.log').read_text(encoding='utf-8') == 'new-suffix\n'
    assert memory.working.snapshot()['jobs'] == []


def test_window_pressure_triggers_encoding_without_idle_model_pulse():
    bus, memory, store, hippo, llm = harness(window=1)
    try:
        add(memory, '第一条'); add(memory, '第二条')
        bus.publish(Event('system.tick', {})); store.store_queue.join()
        assert llm.one_chat.call_count == 1
        assert '第一条' in memory.working.prompt_context()
    finally: store.stop()


def test_periodic_encoding_and_failure_retry_are_rate_limited():
    bus, memory, store, hippo, llm = harness(window=20)
    try:
        add(memory, '尚未满窗口')
        llm.one_chat.side_effect = lambda *_args, **_kwargs: 'invalid-json'
        hippo._last_encoded = time.monotonic() - settings.MEMORY_ENCODE_INTERVAL - 1
        bus.publish(Event('system.tick', {}))
        bus.publish(Event('system.tick', {}))
        assert llm.one_chat.call_count == 1 and hippo.last_error
        assert Path('config/chat_history.log').stat().st_size
    finally: store.stop()


def test_explicit_remember_request_is_not_discarded_when_encoder_returns_empty():
    bus, memory, store, hippo, llm = harness(window=20)
    try:
        add(memory, '请记住，我不喜欢很吵的环境')
        llm.one_chat.side_effect = lambda *_args, **_kwargs: '{"memories":[],"working_notes":[]}'
        bus.publish(Event('system.tick', {})); store.store_queue.join()
        assert llm.one_chat.call_count == 1
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM episodic_memory WHERE content LIKE '用户自述%%很吵%%'")
            assert cur.fetchone()[0] >= 1
    finally: store.stop()


def test_query_failure_is_not_a_successful_empty_recall(monkeypatch):
    bus, _memory, store, hippo, _llm = harness()
    try:
        monkeypatch.setattr(store, '_query_by_similarity', Mock(side_effect=RuntimeError('synthetic failure')))
        result = MemoryQueryTool(hippo).execute({'query': '测试', 'keywords': ['测试']})
        assert not result.success and '查询失败' in result.error
    finally: store.stop()


def test_missing_query_vector_marks_keyword_only_results_as_degraded():
    bus, _memory, store, hippo, _llm = harness()
    try:
        store.llm_client.get_embedding.return_value = None
        result = MemoryQueryTool(hippo).execute({'query': '合成偏好', 'keywords': [str(uuid.uuid4())]})
        assert result.success and result.data['warnings']
        assert '关键词' in MemoryQueryTool().format_result_for_model(result)
        assert store.last_warning
    finally: store.stop()


def test_middle_memory_and_outbox_archive_roundtrip_with_legacy_default(tmp_path):
    memory = ShortTermMemory(max_memory_size=1)
    add(memory, '旧材料'); add(memory, '新材料')
    memory.working.add_job('prefix', [{'memory_id': str(uuid.uuid4()), 'content': '待入库'}], [], [])
    target = tmp_path / 'export'; target.mkdir()
    assert JsonExporter(str(target)).run().success
    memory.working.data['sources'] = []
    memory.working.data['jobs'] = []; memory.working.save()
    assert JsonImporter(str(target)).run().success
    memory.working.reload()
    assert memory.working.snapshot()['sources'] and memory.working.snapshot()['jobs']
    legacy = tmp_path / 'legacy'; legacy.mkdir()
    assert JsonImporter(str(legacy)).run().success
    memory.working.reload()
    assert memory.working.snapshot()['sources'] == [] and memory.working.snapshot()['jobs'] == []


def test_other_world_spool_is_preserved_outside_automatic_replay():
    bus, _memory, store, _hippo, _llm = harness()
    try:
        key = str(uuid.uuid4())
        file = store.spool_dir / (key + '.json')
        file.write_text(json.dumps({'memory_id': key, 'content': '另一个分支'}), encoding='utf-8')
        store.reconcile_spool(set())
        assert not file.exists()
        assert len(list((store.spool_dir / 'retired').glob('*/*.json'))) == 1
    finally: store.stop()


def test_activity_completion_enters_the_middle_buffer_as_a_program_record():
    server = make_server(); client = TestClient(server.app)
    try:
        server.experience.activity('start')
        result = client.post('/experience', json={'action': 'complete', 'epoch': server.protocol.epoch, 'summary': '做了计时记录'})
        assert result.status_code == 200
        records = server.memory.working.snapshot()['sources']
        assert records[-1]['role'] == 'system_event' and records[-1]['source_type'] == 'player_activity_record'
        assert '做了计时记录' in records[-1]['content']
        assert server.memory.working.status()['pending_encoding']
    finally: server.db_memory.stop()


def test_mixed_legacy_and_new_records_preserve_source_uncertainty():
    bus, memory, _store, hippo, _llm = harness(with_store=False)
    key = add(memory, '带标识的新消息')
    source = memory.memory[0]
    text = '旧的无时间日志\n' + json.dumps(source, ensure_ascii=False) + '\n'
    values = [{'content': '旧记录推演', 'source_ids': [], 'source_type': 'legacy_or_simulated', 'confidence': 1},
        {'content': '新消息记录', 'source_ids': [key], 'confidence': 0.9}]
    records = hippo._validate_memories(values, [source], text)
    assert records[0]['confidence'] == 0.5
    assert records[0]['time'] is None and records[0]['time_basis'] == 'unknown'
    assert records[1]['source_ids'] == [key]
    assert records[1]['time_basis'] == 'source_message_time'
    with pytest.raises(ValueError): hippo._validate_memories([{'content': '无证据的新结论'}], [source], json.dumps(source))


def recall_processor():
    hippocampus = Mock()
    hippocampus.query_memory.return_value = {'found': True,
        'episodic_records': [{'id': 1, 'content': '喜欢安静的环境'}, {'id': 2, 'content': '饮品偏好是桂花茶', 'metadata': {'source_ids': ['tea-source']}}],
        'episodic_memories': ['第一条', '第二条'], 'graph_entities': {'合成人物': '不喝咖啡'}, 'graph_relations': ['合成人物 喜欢 桂花茶']}
    registry = ToolRegistry(); registry.register(MemoryQueryTool(hippocampus))
    return ToolCallProcessor(registry)


def test_state_model_consumes_second_memory_and_graph_properties():
    manager = make_state_manager()
    manager.tool_processor = recall_processor()
    manager.llm_client = Mock()
    manager.llm_client.one_chat.side_effect = ['<tool>{"name":"memory_query","parameters":{"query":"饮品偏好","keywords":["偏好"]}}</tool>', '{"P":5}']
    assert manager._ask_llm_with_tools('system', 'prompt') == '{"P":5}'
    payload = manager.llm_client.one_chat.call_args.args[1][-1]['content']
    assert '桂花茶' in payload and 'tea-source' in payload and '不喝咖啡' in payload
    assert manager.llm_client.one_chat.call_count == 2


def test_dialogue_model_consumes_second_memory_in_followup():
    brain, _bus, _memory, _state = make_brain()
    brain.tool_processor = recall_processor()
    brain.llm_client.stream_chat.side_effect = [iter(['<tool>{"name":"memory_query","parameters":{"query":"饮品偏好","keywords":["偏好"]}}</tool>']), iter(['桂花茶，我记得。'])]
    brain.process_dialogue('我喜欢喝什么？')
    assert brain.llm_client.stream_chat.call_count == 2
    payload = brain.llm_client.stream_chat.call_args.kwargs['messages'][-1]['content']
    assert '桂花茶' in payload and 'tea-source' in payload and '不喝咖啡' in payload


def test_interrupted_partial_memory_call_is_not_saved_as_dialogue():
    brain, bus, memory, _state = make_brain()
    brain.llm_client.stream_chat.return_value = iter(['<tool>{"name":"memory_query","parameters":', '不应继续'])
    bus.subscribe('llm.chunk', lambda event: bus.turns.cancel(event.data['turn_id']))
    brain.process_dialogue('合成问题')
    assert all(x['role'] == 'user' for x in memory.memory)


def test_real_archive_restores_middle_outbox_without_current_branch_sources():
    server = make_server(); client = TestClient(server.app)
    try:
        server.memory.working.capture([{'message_id': 'saved-source', 'role': 'user', 'content': '存档内的未完事项'}])
        server.memory.working.add_job('saved-prefix', [{'memory_id': str(uuid.uuid4()), 'content': '存档待入库'}], [], [])
        assert server.archive_manager.create_archive('memory-layers').success
        server.memory.working.capture([{'message_id': 'future-source', 'role': 'user', 'content': '后来的分支内容'}])
        assert client.post('/api/archive/tasks', json={'operation': 'load', 'slot_name': 'memory-layers', 'epoch': server.protocol.epoch}).status_code == 202
        assert finish(client)['status'] == 'completed'
        restored = server.memory.working.snapshot()
        assert [x['message_id'] for x in restored['sources']] == ['saved-source']
        assert restored['jobs'][0]['memories'][0]['content'] == '存档待入库'
    finally: server.db_memory.stop()


def test_failed_archive_import_rolls_back_middle_buffer_and_jobs(monkeypatch):
    from archive.importers.base import ImportResult
    server = make_server(); client = TestClient(server.app)
    try:
        server.memory.working.capture([{'message_id': 'first', 'role': 'user', 'content': '存档时的材料'}])
        assert server.archive_manager.create_archive('memory-before').success
        server.memory.working.capture([{'message_id': 'later', 'role': 'user', 'content': '加载前要保住的新材料'}])
        server.memory.working.add_job('current-prefix', [{'memory_id': str(uuid.uuid4()), 'content': '当前待办'}], [], [])
        before = copy.deepcopy(server.memory.working.snapshot())
        monkeypatch.setattr('archive.manager.PostgresImporter.run', lambda _: ImportResult(success=False, error='synthetic failure'))
        assert client.post('/api/archive/tasks', json={'operation': 'load', 'slot_name': 'memory-before', 'epoch': server.protocol.epoch}).status_code == 202
        assert finish(client)['status'] == 'failed'
        assert server.memory.working.snapshot() == before
    finally:
        server.db_memory.stop()
        assert settings.PG_DB.startswith('ember_test_')
        with connection() as conn, conn.cursor() as cur:
            cur.execute('DROP TABLE IF EXISTS episodic_memory_backup,message_list_backup,state_list_backup')
