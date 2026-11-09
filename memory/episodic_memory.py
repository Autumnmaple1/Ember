import psycopg2
from psycopg2.extras import Json, execute_values
from pgvector.psycopg2 import register_vector
import numpy as np
import time
import logging
import json
from config.settings import settings
from core.event_bus import EventBus, Event
from brain.llm_client import LLMClient
import threading
import uuid
from pathlib import Path
from functools import wraps
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from memory.postgres import initialize_episodic


logger = logging.getLogger(__name__)


def serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._db_lock:
            return method(self, *args, **kwargs)
    return wrapped


class EpisodicMemory:
    def __init__(self, event_bus: EventBus, allowed_pending_ids=None):
        self.event_bus = event_bus
        self.conn = None
        self.ready = False
        self.last_error = None
        self.last_warning = None
        self._db_lock = threading.RLock()
        self._ensure_connection()
        self._init_db()

        self.store_queue = Queue()
        self.spool_dir = Path('data/failed_memories')
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.llm_client = LLMClient()
        self._pending_lock = threading.Lock()
        self._pending_ids = set()
        self.reconcile_spool(allowed_pending_ids)
        for file in self.spool_dir.glob('*.json'):
            data = json.loads(file.read_text(encoding='utf-8'))
            self._pending_ids.add(data['memory_id'])
            self.store_queue.put({'type': 'store', 'data': data})

        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

        self.event_bus.subscribe("memory.store", self._on_store_request)
        self.event_bus.subscribe("memory.query", self._on_query_request)
        self.event_bus.subscribe("memory.sleep", self._sleep_memory_process)

    def _ensure_connection(self):
        try:
            if self.conn is None or self.conn.closed:
                self.conn = psycopg2.connect(
                    dbname=settings.PG_DB,
                    user=settings.PG_USER,
                    password=settings.PG_PASSWORD,
                    host=settings.PG_HOST,
                    port=settings.PG_PORT,
                    connect_timeout=5,
                    options='-c timezone=Asia/Shanghai -c search_path=public',
                )
                initialize_episodic(self.conn)
                self.conn.commit()
                self.ready, self.last_error = True, None
                logger.debug("EpisodicMemory connected to PostgreSQL.")
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            self.ready, self.last_error = False, str(e)
            if self.conn:
                self.conn.close()
            self.conn = None

    def _init_db(self):
        self._ensure_connection()
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                register_vector(self.conn)

                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS episodic_memory(
                        id SERIAL PRIMARY KEY,
                        content TEXT NOT NULL,
                        embedding vector(1536),
                        insight TEXT,
                        insight_embedding vector(1536),
                        importance FLOAT DEFAULT 1.0,
                        confidence FLOAT DEFAULT 1.0,
                        clarity FLOAT DEFAULT 1.0,
                        access_count INTEGER DEFAULT 0,
                        last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        metadata JSONB,
                        time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_consolidated INTEGER DEFAULT 0
                    )
                    """
                )
                cur.execute(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'episodic_memory'
                            AND column_name = 'is_consolidated'
                        ) THEN
                            ALTER TABLE episodic_memory ADD COLUMN is_consolidated INTEGER DEFAULT 0;
                        END IF;
                    END $$;
                    """
                )
                self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to init DB: {e}")
            if self.conn:
                self.conn.rollback()

    def _on_store_request(self, event: Event):
        event.data.setdefault('memory_id', str(uuid.uuid4()))
        key = event.data['memory_id']
        if str(uuid.UUID(key)) != key:
            raise ValueError('记忆标识无效')
        with self._pending_lock:
            if key not in self._pending_ids:
                file = self.spool_dir / (key + '.json')
                temp = file.with_suffix('.tmp')
                temp.write_text(json.dumps(event.data, ensure_ascii=False), encoding='utf-8')
                temp.replace(file)
                self._pending_ids.add(key)
                self.store_queue.put({"type": "store", "data": event.data})
        return {'accepted': True, 'memory_id': key}

    def reconcile_spool(self, allowed_ids):
        """Retain other-world failures outside automatic replay."""
        if allowed_ids is None:
            return
        for file in self.spool_dir.glob('*.json'):
            if file.stem not in allowed_ids:
                directory = self.spool_dir / 'retired' / str(uuid.uuid4())
                directory.mkdir(parents=True, exist_ok=True)
                if self.spool_dir.resolve() not in file.resolve().parents or self.spool_dir.resolve() not in directory.resolve().parents:
                    raise ValueError('待办隔离路径越界')
                file.rename(directory / file.name)

    def _worker_loop(self):
        """工作线程循环，支持优雅退出"""
        while True:
            try:
                # 使用 timeout 支持中断
                task = self.store_queue.get(timeout=1.0)
            except:
                # 超时检查是否应该退出
                continue

            if task is None:
                self.store_queue.task_done()
                break

            try:
                if task["type"] == "store":
                    for attempt in range(3):
                        try:
                            self._async_store_process(task['data'])
                            (self.spool_dir / (task['data']['memory_id'] + '.json')).unlink(missing_ok=True)
                            self.event_bus.publish(Event('memory.stored', {'memory_id': task['data']['memory_id']}))
                            break
                        except Exception:
                            if attempt == 2:
                                raise
                            time.sleep(1)
                elif task["type"] == "update_access":
                    self._execute_update_access(task["id"])
            except Exception as e:
                self.ready, self.last_error = False, str(e)
                logger.error(f"Error in EpisodicMemory worker loop: {e}")
                if task.get('type') == 'store':
                    self.event_bus.publish(Event('memory.store_failed', {'memory_id': task['data']['memory_id']}))
            finally:
                if task.get('type') == 'store':
                    with self._pending_lock:
                        self._pending_ids.discard(task['data']['memory_id'])
                try:
                    self.store_queue.task_done()
                except:
                    pass

    @serialized
    def _async_store_process(self, event_data):
        content = event_data.get("content", "")
        insight = event_data.get("insight", "")

        try:
            embedding = self.llm_client.get_embedding(settings.EMBEDDING_MODEL, content)
            insight_embedding = self.llm_client.get_embedding(
                settings.EMBEDDING_MODEL, insight or content
            )

            if embedding is None or insight_embedding is None:
                logger.warning(
                    "Failed to get embeddings from LLM, skipping memory store."
                )
                raise ValueError('长期记忆向量生成失败，已保留待重试数据')

            event_data["embedding"] = np.asarray(embedding, dtype=np.float32)
            event_data["insight_embedding"] = np.asarray(insight_embedding, dtype=np.float32)

            self._ensure_connection()
            if self.conn:
                self._add_memory(event_data)
                self.ready, self.last_error = True, None
            else:
                raise RuntimeError('长期记忆数据库不可用')
        except Exception as e:
            logger.error(f"Failed to process memory store request: {e}")
            raise

    @serialized
    def _on_query_request(self, event: Event):
        try:
            self._query_records(event)
        except Exception as exc:
            self.ready, self.last_error = False, str(exc)
            if 'callback' in event.data:
                event.data['callback']({'error': '长期记忆查询失败，请检查数据库或向量服务'})

    def _query_records(self, event):
        query = event.data.get("query", "")
        key_words = event.data.get("key_words", [])
        if not query and not key_words:
            if "callback" in event.data:
                event.data["callback"]([])
            return

        embedding = None
        if query:
            try:
                embedding = self.llm_client.get_embedding(
                    settings.EMBEDDING_MODEL, query
                )
            except Exception as e:
                logger.error(f"Embedding query failed: {e}")

        self._ensure_connection()
        if not self.conn:
            raise RuntimeError('长期记忆数据库不可用')
        if query and embedding is None and not key_words:
            raise RuntimeError('查询向量生成失败')

        memories_sim = self._query_by_similarity(embedding) if embedding else []
        memories_key = self._query_by_keywords(key_words) if key_words else []

        seen_ids = set()
        combined_memories = []
        for m in memories_sim + memories_key:
            if m["id"] not in seen_ids:
                logger.debug(f"Memory matched: {m['content'][:50]}... (ID: {m['id']})")
                combined_memories.append(m)
                seen_ids.add(m["id"])

        self.last_warning = '语义检索暂不可用，当前仅使用关键词检索' if query and embedding is None else None
        if "callback" in event.data:
            event.data["callback"]({'items': combined_memories, 'warnings': [self.last_warning]} if self.last_warning else combined_memories)
        self.ready, self.last_error = True, None

        for m_id in seen_ids:
            self.store_queue.put({"type": "update_access", "id": m_id})

    @serialized
    def _execute_update_access(self, event_id):
        self._ensure_connection()
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE episodic_memory 
                    SET access_count = access_count + 1,
                        last_accessed = CURRENT_TIMESTAMP,
                        clarity = LEAST(5.0, clarity + (importance * exp(-clarity / 1)))
                    WHERE id = %s
                    """,
                    (event_id,),
                )
                self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to update access count for memory {event_id}: {e}")
            if self.conn:
                self.conn.rollback()

    @serialized
    def _sleep_memory_process(self, event: Event):
        self._ensure_connection()
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE episodic_memory
                    SET clarity = clarity * exp(
                            -%s / (1.0 + ln(1.0 + access_count))
                        )
                    WHERE clarity > 0.01
                    """,
                    (settings.MEMORY_DECENT_FACTOR,),
                )
                cur.execute("DELETE FROM episodic_memory WHERE clarity < 0.05")
                self.conn.commit()
                logger.info("Memory cleanup (deleted low clarity memories) completed.")
        except Exception as e:
            logger.error(f"Memory cleanup failed: {e}")
            if self.conn:
                self.conn.rollback()

    def _add_memory(self, event_data):
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO episodic_memory (content, embedding, insight, insight_embedding, importance, confidence, metadata, time, last_accessed, clarity, memory_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(memory_id) DO NOTHING
                    """,
                    (
                        event_data.get("content", ""),
                        event_data.get("embedding"),
                        event_data.get("insight", ""),
                        event_data.get("insight_embedding"),
                        event_data.get("importance", 1.0),
                        event_data.get("confidence", 1.0),
                        Json({key: event_data.get(key, []) for key in ('keywords', 'source_ids', 'evidence')}
                            | {'source_type': event_data.get('source_type', 'legacy_or_simulated'),
                               'time_basis': event_data.get('time_basis', 'legacy_unknown')}),
                        event_data.get("time"),
                        event_data.get("time"),
                        event_data.get("importance", 1.0),
                        event_data.get('memory_id'),
                    ),
                )
                self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to add memory to DB: {e}")
            if self.conn:
                self.conn.rollback()
            raise

    def _query_by_similarity(self, query_vector):
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, insight, importance, confidence, access_count, last_accessed, metadata,
                    (1 - (embedding <=> %s::vector)) as similarity1,
                    (1 - (insight_embedding <=> %s::vector)) as similarity2,
                    time
                    FROM episodic_memory 
                    ORDER BY GREATEST((1 - (embedding <=> %s::vector)), (1 - (insight_embedding <=> %s::vector))) DESC, clarity DESC
                    LIMIT %s
                    """,
                    (
                        query_vector,
                        query_vector,
                        query_vector,
                        query_vector,
                        settings.RECALL_TOP_K,
                    ),
                )
                results = cur.fetchall()
                memories = []
                for row in results:
                    memories.append(
                        {
                            "id": row[0],
                            "content": row[1],
                            "insight": row[2],
                            "importance": row[3],
                            "confidence": row[4],
                            'similarity': max((value for value in (row[8], row[9]) if value is not None), default=0),
                            "metadata": row[7],
                            "time": (
                                row[10].isoformat()
                                if hasattr(row[10], "isoformat")
                                else str(row[10]) if row[10] is not None else None
                            ),
                        }
                    )
                return memories
        except Exception as e:
            logger.error(f"Similarity query failed: {e}")
            if self.conn:
                self.conn.rollback()
            raise RuntimeError('长期记忆相似度查询失败') from e

    def _query_by_keywords(self, keywords: list):
        if not keywords:
            return []

        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    WITH keyword_matches AS (
                        SELECT 
                            id, content, insight, importance, confidence, access_count, last_accessed, metadata, clarity, time,
                            (
                                SELECT count(*) 
                                FROM jsonb_array_elements_text((metadata->'keywords')::jsonb) as k 
                                WHERE k = ANY(%s)
                            ) as match_count
                        FROM episodic_memory
                        WHERE (metadata->'keywords')::jsonb ?| %s
                    )
                    SELECT id, content, insight, importance, confidence, access_count, last_accessed, metadata, clarity, time
                    FROM keyword_matches
                    ORDER BY match_count DESC, clarity DESC
                    LIMIT %s
                    """,
                    (keywords, keywords, settings.RECALL_TOP_K),
                )
                rows = cur.fetchall()
                memories = []
                for row in rows:
                    memories.append(
                        {
                            "id": row[0],
                            "content": row[1],
                            "insight": row[2],
                            "importance": row[3],
                            "confidence": row[4],
                            "metadata": row[7],
                            "time": (
                                row[9].isoformat()
                                if hasattr(row[9], "isoformat")
                                else str(row[9]) if row[9] is not None else None
                            ),
                        }
                    )
                return memories
        except Exception as e:
            logger.error(f"Keyword query failed: {e}")
            if self.conn:
                self.conn.rollback()
            raise RuntimeError('长期记忆关键词查询失败') from e

    def stop(self):
        self.store_queue.put(None)
        self.worker_thread.join(timeout=10)
        if not self.worker_thread.is_alive() and self.conn:
            self.conn.close()
