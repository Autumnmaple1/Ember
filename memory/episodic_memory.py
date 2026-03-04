import psycopg2
from psycopg2.extras import Json, execute_values
import numpy as np
import time
import logging
import json
from config.settings import settings
from core.event_bus import EventBus, Event
from brain.llm_client import LLMClient
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor


class EpisodicMemory:
    def __init__(self, event_bus: EventBus):
        self.conn = psycopg2.connect(
            dbname=settings.PG_DB,
            user=settings.PG_USER,
            password=settings.PG_PASSWORD,
            host=settings.PG_HOST,
            port=settings.PG_PORT,
        )
        self._init_db()
        self.store_queue = Queue()

        self.llm_client = LLMClient()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

        self.event_bus.subscribe("memory.store", self._on_store_request)

        self.event_bus.subscribe("memory.query", self._on_query_request)

        self.event_bus.subscribe("memory.sleep", self._sleep_memory_process)

    def _init_db(self):
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

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
                    time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    
                )
                """
            )
            self.conn.commit()

    def _on_store_request(self, event: Event):
        self.store_queue.put(event.data)

    def _worker_loop(self):
        while True:
            event_data = self.store_queue.get()
            if event_data is None:
                break
            self._async_store_process(event_data)

            self.store_queue.task_done()

    def _async_store_process(self, event_data):
        content = event_data.get("content", "")
        insight = event_data.get("insight", "")

        try:
            embedding = self.llm_client.get_embedding(settings.EMBEDDING_MODEL, content)
            insight_embedding = self.llm_client.get_embedding(
                settings.INSIGHT_EMBEDDING_MODEL, insight
            )
            event_data["embedding"] = embedding
            event_data["insight_embedding"] = insight_embedding
            self._add_memory(event_data)
            logging.info(f"上传短期记忆：{content}")
        except Exception as e:
            logging.error(f"Failed to process memory store request: {e}")

    def _on_query_request(self, event: Event):
        query = event.data.get("query", "")
        if not query:
            return

        def get_embedding_task():
            return self.llm_client.get_embedding(settings.EMBEDDING_MODEL, query)

        def get_keywords_task():
            prompt = f"从以下查询中提取3-5个核心关键词，以逗号分隔：{query}"
            response = self.llm_client.generate(prompt)
            keywords = [k.strip() for k in response.split(",") if k.strip()]
            return keywords

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_embedding = executor.submit(get_embedding_task)
            future_keywords = executor.submit(get_keywords_task)
            try:
                embedding = future_embedding.result(timeout=10)
                keywords = future_keywords.result(timeout=10)
            except Exception as e:
                logging.warning(
                    f"Error occurred while retrieving embedding or keywords: {e}"
                )
                embedding = None
                keywords = []

        memories_sim = self._query_by_similarity(embedding) if embedding else []
        memories_key = self._query_by_keywords(keywords) if keywords else []

        seen_ids = set()
        combined_memories = []
        for m in memories_sim + memories_key:
            if m["id"] not in seen_ids:
                combined_memories.append(m)
                seen_ids.add(m["id"])

        if "callback" in event.data:
            event.data["callback"](combined_memories)

        for m_id in seen_ids:
            threading.Thread(
                target=self._update_access, args=(m_id,), daemon=True
            ).start()

    def _update_access(self, event_id):
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE episodic_memory 
                    SET access_count = access_count + 1,
                        last_accessed = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (event_id,),
                )
                self.conn.commit()
        except Exception as e:
            logging.error(f"Failed to update importance for memory {event_id}: {e}")

    def _sleep_memory_process(self, event: Event):
        pass

    def _add_memory(self, event_data):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO episodic_memory (content, embedding, insight, insight_embedding, importance, confidence, metadata, time,last_accessed, clarity)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_data.get("content", ""),
                    event_data.get("embedding"),
                    event_data.get("insight", ""),
                    event_data.get("insight_embedding"),
                    event_data.get("importance", 1.0),
                    event_data.get("confidence", 1.0),
                    Json({"keywords": event_data.get("keywords", [])}),
                    event_data.get("time"),
                    event_data.get("time"),
                    event_data.get("importance", 1.0),
                ),
            )
            logging.info(f"Stored memory: {event_data.get('content', '')}")
            self.conn.commit()

    def _query_by_similarity(self, query_vector):
        with self.conn.cursor() as cur:
            time_now = self.event_bus.formatted_logical_now
            cur.execute(
                """
                SELECT id, content, insight, importance, confidence, access_count, last_accessed, metadata,
                
                (1 - (embedding <=> %s::vector)) as similarity1,
                (1 - (insight_embedding <=> %s::vector)) as similarity2,
                (EXTRACT(EPOCH FROM (%s - last_accessed)) / 3600.0) as dt_hours,
                time
                FROM episodic_memory ORDER BY 
                GREATEST((1 - (embedding <=> %s::vector)), (1 - (insight_embedding <=> %s::vector))) 
                * importance 
                * exp(-%s * (EXTRACT(EPOCH FROM (%s::timestamp - last_accessed)) / 86400.0) / (1 + access_count)) DESC
                LIMIT %s
                """,
                (
                    query_vector,
                    query_vector,
                    time_now,
                    query_vector,
                    query_vector,
                    settings.MEMORY_DECENT_FACTOR,
                    time_now,
                    settings.RECALL_TOP_K,
                ),
            )
            results = cur.fetchall()
            rows = results
            memories = []

            k = settings.MEMORY_DECENT_FACTOR

            for row in rows:
                memories.append(
                    {
                        "id": row[0],
                        "content": row[1],
                        "insight": row[2],
                        "importance": row[3],
                        "confidence": row[4],
                        "metadata": row[7],
                        "time": row[11],
                    }
                )

            return memories

    def _query_by_keywords(self, keywords: list):
        if not keywords:
            return []

        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content, insight, importance, confidence, access_count, last_accessed, metadata, clarity, time
                FROM episodic_memory
                WHERE (metadata->'keywords')::jsonb ?| %s
                ORDER BY clarity DESC
                LIMIT %s
                """,
                (keywords, settings.RECALL_TOP_K),
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
                        "time": row[9],
                    }
                )
            return memories
