"""Connections and schema initialization shared by persistence workers."""
from contextlib import contextmanager
from psycopg2 import connect as pg_connect, sql
from pgvector.psycopg2 import register_vector
from config.settings import settings


def connect():
    return pg_connect(host=settings.PG_HOST, port=settings.PG_PORT,
                      dbname=settings.PG_DB, user=settings.PG_USER,
                      password=settings.PG_PASSWORD, connect_timeout=5,
                      options="-c timezone=Asia/Shanghai -c search_path=public")


@contextmanager
def connection():
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def sync_sequence(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
        sequence = cur.fetchone()[0]
        if sequence:
            cur.execute(sql.SQL("SELECT max(id) FROM {}").format(sql.Identifier(table)))
            maximum = cur.fetchone()[0]
            cur.execute("SELECT setval(%s::regclass, %s, %s)",
                        (sequence, maximum if maximum is not None else 1, maximum is not None))


def initialize_history(conn):
    with conn.cursor() as cur:
        cur.execute('SET search_path TO public')
        cur.execute("""CREATE TABLE IF NOT EXISTS message_list (
            id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT NOW(), sender TEXT,
            text TEXT, thinking TEXT, message_id TEXT UNIQUE)""")
        cur.execute("ALTER TABLE message_list ADD COLUMN IF NOT EXISTS message_id TEXT")
        cur.execute("ALTER TABLE message_list ADD COLUMN IF NOT EXISTS completion_status TEXT NOT NULL DEFAULT 'completed'")
        cur.execute("ALTER TABLE message_list ADD COLUMN IF NOT EXISTS turn_id TEXT")
        cur.execute("UPDATE message_list SET message_id = 'legacy-' || id WHERE message_id IS NULL")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS message_list_message_id_idx ON message_list(message_id)")
        cur.execute("""CREATE TABLE IF NOT EXISTS state_list (
            id SERIAL PRIMARY KEY, timestamp TIMESTAMP DEFAULT NOW(), text TEXT, message_id TEXT UNIQUE)""")
        cur.execute("ALTER TABLE state_list ADD COLUMN IF NOT EXISTS message_id TEXT")
        cur.execute("UPDATE state_list SET message_id = 'legacy-state-' || id WHERE message_id IS NULL")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS state_list_message_id_idx ON state_list(message_id)")
    for table in ('message_list', 'state_list'):
        sync_sequence(conn, table)


def initialize_episodic(conn):
    with conn.cursor() as cur:
        cur.execute('SET search_path TO public')
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    conn.commit()
    register_vector(conn)
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS episodic_memory (
            id SERIAL PRIMARY KEY, content TEXT NOT NULL, embedding vector(1536),
            insight TEXT, insight_embedding vector(1536), importance FLOAT DEFAULT 1,
            confidence FLOAT DEFAULT 1, clarity FLOAT DEFAULT 1, access_count INTEGER DEFAULT 0,
            last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP, metadata JSONB,
            time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_consolidated INTEGER DEFAULT 0,
            memory_id TEXT UNIQUE)""")
        cur.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS is_consolidated INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS memory_id TEXT")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS episodic_memory_memory_id_idx ON episodic_memory(memory_id)")
