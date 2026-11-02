"""Opt-in real-model smoke on a disposable ember_test_* database and temp files."""
import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-model', action='store_true')
    args = parser.parse_args()
    if not args.real_model:
        parser.error('Real model calls require --real-model')
    # Enforce isolation before loading any environment from the business project.
    if os.environ.get('PG_PORT') != '55433':
        parser.error('Set PG_PORT=55433 for the isolated test cluster')
    import psycopg2
    from psycopg2 import sql
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    admin = {key: os.environ[name] for key, name in [('host','PG_HOST'), ('port','PG_PORT'), ('user','PG_USER'), ('password','PG_PASSWORD')]}
    name = 'ember_test_' + uuid.uuid4().hex
    os.environ.update(PG_DB=name, ENABLE_NEO4J='False', START_TIME='', TIME_ACCEL_FACTOR='1')
    conn = psycopg2.connect(**admin, dbname='postgres')
    conn.autocommit = True
    with conn.cursor() as cur: cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    original, store = Path.cwd(), None
    try:
        with tempfile.TemporaryDirectory(prefix='ember-memory-smoke-') as directory:
            folder = Path(directory)
            (folder / 'config').mkdir()
            shutil.copy2(ROOT / 'config/prompts.yaml', folder / 'config/prompts.yaml')
            os.chdir(folder); sys.path.insert(0, str(ROOT))
            from core.event_bus import EventBus, Event
            from memory.short_term import ShortTermMemory
            from memory.episodic_memory import EpisodicMemory
            from memory.memory_process import Hippocampus
            from memory.postgres import connection
            from brain.llm_client import LLMClient
            from config.settings import settings
            from tools.registry import ToolRegistry
            from tools.processor import ToolCallProcessor
            from tools.builtin.memory_query_tool import MemoryQueryTool
            from tools.base import ToolResult

            bus = EventBus()
            memory = ShortTermMemory(max_memory_size=2)
            store = EpisodicMemory(bus)
            hippo = Hippocampus(bus, memory.working, memory)
            for role, content in [('user','请记住，合成测试用户喜欢安静的窗边座位。'),
                    ('assistant','安静的窗边，我记下这句了。'), ('user','另外，合成测试用户不喝咖啡，喜欢桂花茶。')]:
                memory.add_message(role, content, timestamp=int(bus.logical_now * 1000), message_id=str(uuid.uuid4()))
            bus.publish(Event('memory.preprocess', {}))
            drained = threading.Event()
            threading.Thread(target=lambda: (store.store_queue.join(), drained.set()), daemon=True).start()
            assert drained.wait(90), 'Isolated vector write did not finish'
            assert not memory.working.snapshot()['jobs'], hippo.status()
            assert not Path('config/chat_history.log').read_text(encoding='utf-8'), hippo.status()
            result = hippo.query_memory('合成测试用户喜欢喝什么饮品', ['桂花茶','咖啡'])
            assert result.get('found') and result.get('episodic_records'), result.get('error')
            registry = ToolRegistry(); registry.register(MemoryQueryTool(hippo))
            payload = ToolCallProcessor(registry).format_tool_results_for_prompt([
                {'tool_name':'memory_query','result':ToolResult.ok(data=result)}])
            assert '桂花茶' in payload and 'source_ids' in payload
            answer = LLMClient().one_chat(settings.LARGE_LLM, [
                {'role':'system','content':'只根据提供的记忆原文回答；没有证据时明确说不知道。'},
                {'role':'user','content':payload + '\n问题：合成测试用户的饮品偏好是什么？一句话回答。'}], call_type='memory_smoke')
            assert answer and '桂花茶' in answer, answer
            with connection() as db, db.cursor() as cur:
                cur.execute('SELECT count(*),count(*) FILTER (WHERE embedding IS NOT NULL) FROM episodic_memory')
                rows, vectors = cur.fetchone()
            assert rows > 0 and rows == vectors
            restarted = ShortTermMemory(max_memory_size=2)
            assert restarted.working.snapshot()['sources']
            print(json.dumps({'result':'passed','stored_memories':rows,'vectors':vectors,
                'middle_buffer':len(restarted.working.snapshot()['sources']), 'notes':len(restarted.working.snapshot()['notes']),
                'model_answer':answer,'business_database_accessed':False},ensure_ascii=False),flush=True)
            store.stop(); store = None
            os.chdir(original)
    finally:
        if store: store.stop()
        os.chdir(original)
        with conn.cursor() as cur: cur.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        conn.close()


if __name__ == '__main__':
    main()
