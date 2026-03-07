import psycopg2
from psycopg2.extras import Json, execute_values
from core.event_bus import EventBus,Event
from config.settings import settings
import logging
import json
import threading
import re
from queue import Queue
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

def separate_thought_and_speech(text):
    thought_match = re.search(r'<thought>([\s\S]*?)</thought>', text)
    thought = thought_match.group(1).strip() if thought_match else ""
    
    if '</thought>' in text:
        speech_match = re.search(r'[\s\S]*</thought>\s*([\s\S]*)', text)
        speech = speech_match.group(1).strip() if speech_match else ""
    else:
        speech = re.sub(r'<thought>[\s\S]*', '', text).strip()
        if not speech: speech = text.strip()
    
    return thought, speech

class DBMemory:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.store_queue = Queue()
        self.conn = None
        self._ensure_connection()
        self._init_db()
        self.event_bus.subscribe("user.input",self._on_user_input)
        self.event_bus.subscribe("llm.finished",self._on_llm_finished)
        self.start()
        
    def _ensure_connection(self):
        try:
            if self.conn is None or self.conn.closed:
                self.conn = psycopg2.connect(
                    dbname=settings.PG_DB,
                    user=settings.PG_USER,
                    password=settings.PG_PASSWORD,
                    host=settings.PG_HOST,
                    port=settings.PG_PORT,
                    connect_timeout=5
                )
                logger.debug("Successfully connected to PostgreSQL.")
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            self.conn = None
            
    def _init_db(self):
        self._ensure_connection()
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS message_list (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    sender TEXT,
                    text TEXT,
                    thinking TEXT
                );
            """)
            self.conn.commit()
            
    def _on_user_input(self, event: Event):
        data = {"sender": "user", "text": event.data["text"],"thinking":"","timestamp":self.event_bus.formatted_logical_now}
        self.store_queue.put(data)
    
    def _on_llm_finished(self, event: Event):
        thought, speech = separate_thought_and_speech(event.data["text"])
        data = {"sender": "assistant", "text": speech,"thinking":thought,"timestamp":self.event_bus.formatted_logical_now}
        self.store_queue.put(data)
        
    def start(self):
        threading.Thread(target=self._store_loop, daemon=True).start()
    
    def get_history(self, limit=20, before_timestamp=None):
        self._ensure_connection()
        if not self.conn:
            return []
            
        try:
            with self.conn.cursor() as cur:
                if before_timestamp:
                    # 如果 before_timestamp 是数字（毫秒时间戳），使用 to_timestamp
                    # 如果是字符串（ISO），直接比较
                    if isinstance(before_timestamp, (int, float)):
                        query = "SELECT id, timestamp, sender, text, thinking FROM message_list WHERE timestamp < to_timestamp(%s / 1000.0) ORDER BY timestamp DESC LIMIT %s"
                    else:
                        query = "SELECT id, timestamp, sender, text, thinking FROM message_list WHERE timestamp < %s ORDER BY timestamp DESC LIMIT %s"
                    cur.execute(query, (before_timestamp, limit))
                else:
                    cur.execute("""
                        SELECT id, timestamp, sender, text, thinking 
                        FROM message_list 
                        ORDER BY timestamp DESC 
                        LIMIT %s
                    """, (limit,))
                
                rows = cur.fetchall()
                messages = []
                for row in rows:
                    raw_ts = row[1]
                    ts_value = int(raw_ts.timestamp() * 1000) if hasattr(raw_ts, 'timestamp') else 0
                    
                    messages.append({
                        "id": row[0],
                        "timestamp": ts_value,
                        "role": "ai" if row[2] == "assistant" else "user",
                        "content": row[3],
                        "thinking": row[4]
                    })
                return messages
        except Exception as e:
            logger.error(f"Failed to fetch history: {e}")
            return []

    def _store_loop(self):
        while True:
            data = self.store_queue.get()
            try:
                self._ensure_connection()
                if not self.conn:
                    logger.warning("DB connection unavailable, retrying later...")
                    self.store_queue.put(data)
                    threading.Event().wait(1)
                    continue
                
                with self.conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO message_list (sender, text, thinking, timestamp) 
                        VALUES (%s, %s, %s, %s);
                    """, (data["sender"], data["text"], data["thinking"], data["timestamp"]))
                    self.conn.commit()
            except Exception as e:
                logger.error(f"Failed to store message: {e}")
                if self.conn: self.conn.rollback()
        
        