import psycopg2
from psycopg2.extras import Json
from core.event_bus import EventBus, Event
from config.settings import settings
import logging
import json
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from brain.tag_utils import extract_thought_and_speech
from memory.base_memory import BasePostgresMemory

logger = logging.getLogger(__name__)


def separate_thought_and_speech(text: str) -> tuple[str, str]:
    """分离 thought 和 speech（使用增强的容错处理）"""
    thought, speech = extract_thought_and_speech(text)
    # 如果没有提取到 speech，返回原始文本
    if not speech:
        speech = text.strip()
    return thought, speech


class DBMemory(BasePostgresMemory):
    """数据库存储管理器"""

    _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="db_memory")

    def __init__(self, event_bus: EventBus):
        super().__init__()
        self.event_bus = event_bus
        self.store_queue = Queue()
        self._init_db()
        self.event_bus.subscribe("user.input", self._on_user_input)
        self.event_bus.subscribe("llm.finished", self._on_llm_finished)
        self.event_bus.subscribe("state.update", self._on_state_update)
        self._start_worker()

    def _init_db(self) -> None:
        """初始化数据库表结构"""
        if not self.conn:
            logger.error("Cannot initialize DB: connection failed")
            return

        schema_queries = [
            """
                CREATE TABLE IF NOT EXISTS message_list (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    sender TEXT,
                    text TEXT,
                    thinking TEXT
                );
            """,
            """
                CREATE TABLE IF NOT EXISTS state_list (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    text TEXT
                );
            """
        ]

        for query in schema_queries:
            if not self._execute_write(query):
                logger.error(f"Failed to create table with query: {query[:50]}...")

    def _on_user_input(self, event: Event) -> None:
        data = {
            "sender": "user",
            "text": event.data["text"],
            "thinking": "",
            "timestamp": self.event_bus.formatted_logical_now,
        }
        content = {"data": data, "database": "message_list"}
        self.store_queue.put(content)

    def _on_llm_finished(self, event: Event) -> None:
        thought, speech = separate_thought_and_speech(event.data["text"])
        data = {
            "sender": "assistant",
            "text": speech,
            "thinking": thought,
            "timestamp": self.event_bus.formatted_logical_now,
        }
        content = {"data": data, "database": "message_list"}
        self.store_queue.put(content)

    def _start_worker(self) -> None:
        """启动后台存储线程"""
        threading.Thread(target=self._store_loop, daemon=True).start()

    def get_history(self, limit: int = 20, before_timestamp: int | None = None) -> list:
        """获取历史消息

        Args:
            limit: 返回消息数量限制
            before_timestamp: 只返回此时间戳之前的消息

        Returns:
            消息列表
        """
        if not self._ensure_connection():
            return []

        try:
            with self.conn.cursor() as cur:
                if before_timestamp:
                    if isinstance(before_timestamp, (int, float)):
                        query = """SELECT id, timestamp, sender, text, thinking
                                   FROM message_list
                                   WHERE timestamp < to_timestamp(%s / 1000.0)
                                   ORDER BY timestamp DESC LIMIT %s"""
                    else:
                        query = """SELECT id, timestamp, sender, text, thinking
                                   FROM message_list
                                   WHERE timestamp < %s
                                   ORDER BY timestamp DESC LIMIT %s"""
                    cur.execute(query, (before_timestamp, limit))
                else:
                    cur.execute(
                        """SELECT id, timestamp, sender, text, thinking
                           FROM message_list
                           ORDER BY timestamp DESC LIMIT %s""",
                        (limit,),
                    )

                rows = cur.fetchall()
                return self._format_history_rows(rows)
        except psycopg2.Error as e:
            logger.error(f"Failed to fetch history: {e}")
            return []

    @staticmethod
    def _format_history_rows(rows: list) -> list[dict]:
        """格式化历史记录行"""
        messages = []
        for row in rows:
            raw_ts = row[1]
            ts_value = int(raw_ts.timestamp() * 1000) if hasattr(raw_ts, "timestamp") else 0

            messages.append({
                "id": row[0],
                "timestamp": ts_value,
                "role": "ai" if row[2] == "assistant" else "user",
                "content": row[3],
                "thinking": row[4],
            })
        return messages

    def _store_loop(self) -> None:
        """后台存储循环"""
        while True:
            content = self.store_queue.get()
            data = content["data"]
            database_name = content.get("database", "message_list")

            if not self._ensure_connection():
                logger.warning("DB connection unavailable, retrying later...")
                self.store_queue.put(content)
                threading.Event().wait(1)
                continue

            try:
                with self.conn.cursor() as cur:
                    if database_name == "state_list":
                        cur.execute(
                            "INSERT INTO state_list (text, timestamp) VALUES (%s, %s);",
                            (data["text"], data["timestamp"]),
                        )
                    else:
                        cur.execute(
                            """INSERT INTO message_list (sender, text, thinking, timestamp)
                               VALUES (%s, %s, %s, %s);""",
                            (data["sender"], data["text"], data["thinking"], data["timestamp"]),
                        )
                    self.conn.commit()
            except psycopg2.Error as e:
                logger.error(f"Failed to store message: {e}")
                if self.conn:
                    self.conn.rollback()

    def _on_state_update(self, event: Event) -> None:
        data = {
            "text": json.dumps(event.data["new_state"]),
            "timestamp": self.event_bus.formatted_logical_now,
        }
        content = {"data": data, "database": "state_list"}
        self.store_queue.put(content)
