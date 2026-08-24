from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any


class MobileMemoryDatabase:
    """SQLite storage mirroring Ember's PostgreSQL memory domains."""

    SCHEMA_VERSION = 3

    def __init__(self, data_dir: Path):
        self.path = data_dir / "ember" / "memory.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=15.0,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._create_schema()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                logical_timestamp TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_created_at
                ON chat_messages(created_at DESC);

            CREATE TABLE IF NOT EXISTS memory_buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                logical_timestamp TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodic_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 1.0,
                confidence REAL NOT NULL DEFAULT 1.0,
                keywords_json TEXT NOT NULL DEFAULT '[]',
                insight TEXT NOT NULL DEFAULT '',
                occurred_at TEXT,
                embedding_json TEXT,
                access_count INTEGER NOT NULL DEFAULT 0,
                clarity REAL NOT NULL DEFAULT 1.0,
                is_consolidated INTEGER NOT NULL DEFAULT 0,
                last_accessed REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_episode_created_at
                ON episodic_memories(created_at DESC);

            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                entity_type TEXT NOT NULL DEFAULT 'Entity',
                aliases_json TEXT NOT NULL DEFAULT '[]',
                properties_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                target_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                properties_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(source_entity_id, target_entity_id, relation)
            );
            CREATE INDEX IF NOT EXISTS idx_relationship_source
                ON relationships(source_entity_id);
            CREATE INDEX IF NOT EXISTS idx_relationship_target
                ON relationships(target_entity_id);
            """
        )
        episode_columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(episodic_memories)"
            ).fetchall()
        }
        migrations = {
            "clarity": "REAL NOT NULL DEFAULT 1.0",
            "is_consolidated": "INTEGER NOT NULL DEFAULT 0",
            "last_accessed": "REAL",
        }
        for column, definition in migrations.items():
            if column not in episode_columns:
                self._connection.execute(
                    f"ALTER TABLE episodic_memories ADD COLUMN {column} {definition}"
                )
        self._connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(self.SCHEMA_VERSION),),
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.commit()
                self._connection.close()
            except sqlite3.ProgrammingError:
                # close() is intentionally idempotent during runtime teardown.
                pass

    def _ensure_connection_locked(self) -> None:
        try:
            self._connection.execute("SELECT 1")
        except sqlite3.ProgrammingError as error:
            if "closed" not in str(error).lower():
                raise
            self._connection = self._open_connection()
            self._create_schema()

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._ensure_connection_locked()
            target = sqlite3.connect(str(destination))
            try:
                self._connection.backup(target)
                target.commit()
            finally:
                target.close()

    def restore_from(self, source: Path) -> None:
        if not source.is_file():
            raise ValueError("存档缺少记忆数据库")
        source_connection = sqlite3.connect(str(source))
        try:
            integrity = source_connection.execute("PRAGMA quick_check").fetchone()[0]
            tables = {
                row[0]
                for row in source_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if integrity != "ok" or "episodic_memories" not in tables:
                raise ValueError("存档记忆数据库校验失败")

            rollback_connection = sqlite3.connect(":memory:")
            try:
                with self._lock:
                    self._ensure_connection_locked()
                    self._connection.commit()
                    self._connection.backup(rollback_connection)
                    try:
                        source_connection.backup(self._connection)
                        self._connection.commit()
                        self._create_schema()
                        restored_integrity = self._connection.execute(
                            "PRAGMA quick_check"
                        ).fetchone()[0]
                        if restored_integrity != "ok":
                            raise ValueError("恢复后的记忆数据库校验失败")
                    except Exception:
                        rollback_connection.backup(self._connection)
                        self._connection.commit()
                        self._create_schema()
                        raise
            finally:
                try:
                    rollback_connection.close()
                except sqlite3.Error:
                    pass
        finally:
            source_connection.close()

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=15.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def add_chat_message(self, role: str, content: str, timestamp: str) -> int:
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"unsupported chat role: {role}")
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO chat_messages(role, content, logical_timestamp, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (role, content, timestamp, time.time()),
            )
            self._connection.execute(
                """
                INSERT INTO memory_buffer(role, content, logical_timestamp, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (role, content, timestamp, time.time()),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def get_chat_messages(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, role, content, logical_timestamp
                FROM chat_messages
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["logical_timestamp"],
            }
            for row in reversed(rows)
        ]

    def chat_message_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM chat_messages"
            ).fetchone()
            return int(row["count"])

    def trim_chat_messages(self, keep: int) -> None:
        with self._lock:
            self._connection.execute(
                """
                DELETE FROM chat_messages
                WHERE id NOT IN (
                    SELECT id FROM chat_messages ORDER BY id DESC LIMIT ?
                )
                """,
                (max(1, int(keep)),),
            )
            self._connection.commit()

    def clear_chat_messages(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM chat_messages")
            self._connection.execute("DELETE FROM memory_buffer")
            self._connection.commit()

    def memory_buffer_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, role, content, logical_timestamp
                FROM memory_buffer ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_memory_buffer(self, through_id: int, keep_last: int = 0) -> None:
        with self._lock:
            keep_rows = self._connection.execute(
                """
                SELECT id FROM memory_buffer
                WHERE id <= ? ORDER BY id DESC LIMIT ?
                """,
                (int(through_id), max(0, int(keep_last))),
            ).fetchall()
            keep_ids = [int(row["id"]) for row in keep_rows]
            if keep_ids:
                placeholders = ",".join("?" for _ in keep_ids)
                self._connection.execute(
                    f"DELETE FROM memory_buffer WHERE id <= ? AND id NOT IN ({placeholders})",
                    [int(through_id), *keep_ids],
                )
            else:
                self._connection.execute(
                    "DELETE FROM memory_buffer WHERE id <= ?", (int(through_id),)
                )
            self._connection.commit()

    def episode_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM episodic_memories"
            ).fetchone()
            return int(row["count"])

    def memory_stats(self) -> dict[str, int]:
        with self._lock:
            episode_row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM episodic_memories"
            ).fetchone()
            buffer_row = self._connection.execute(
                """
                SELECT COUNT(*) AS count,
                       COALESCE(SUM(LENGTH(content)), 0) AS characters
                FROM memory_buffer
                """
            ).fetchone()
            entity_row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM entities"
            ).fetchone()
            relation_row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM relationships"
            ).fetchone()
        return {
            "episodic_count": int(episode_row["count"]),
            "pending_messages": int(buffer_row["count"]),
            "pending_characters": int(buffer_row["characters"]),
            "entity_count": int(entity_row["count"]),
            "relationship_count": int(relation_row["count"]),
        }

    def graph_overview(
        self,
        entity_limit: int = 200,
        relation_limit: int = 500,
        episode_limit: int = 60,
    ) -> dict[str, Any]:
        """图数据库 + 结构化记忆总览（供 UI 的“记忆图谱”页面展示）。"""
        with self._lock:
            entities = [
                dict(row)
                for row in self._connection.execute(
                    """
                    SELECT id, name, entity_type, aliases_json, properties_json
                    FROM entities
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (max(1, int(entity_limit)),),
                ).fetchall()
            ]
            relationships = [
                dict(row)
                for row in self._connection.execute(
                    """
                    SELECT r.id, source.name AS source,
                           r.relation, target.name AS target,
                           r.properties_json
                    FROM relationships r
                    JOIN entities source ON source.id = r.source_entity_id
                    JOIN entities target ON target.id = r.target_entity_id
                    ORDER BY r.updated_at DESC
                    LIMIT ?
                    """,
                    (max(1, int(relation_limit)),),
                ).fetchall()
            ]
            episodes = [
                dict(row)
                for row in self._connection.execute(
                    """
                    SELECT id, content, insight, importance, confidence,
                           keywords_json, occurred_at, access_count, clarity,
                           created_at
                    FROM episodic_memories
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(1, int(episode_limit)),),
                ).fetchall()
            ]
        return {
            "stats": self.memory_stats(),
            "entities": entities,
            "relationships": relationships,
            "episodes": episodes,
        }

    def add_episode(
        self,
        content: str,
        importance: float = 1.0,
        confidence: float = 1.0,
        keywords: list[str] | None = None,
        insight: str = "",
        occurred_at: str | None = None,
        embedding: list[float] | None = None,
    ) -> int:
        now = time.time()
        with self._lock:
            existing = self._connection.execute(
                "SELECT id FROM episodic_memories WHERE content = ? LIMIT 1",
                (content,),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            cursor = self._connection.execute(
                """
                INSERT INTO episodic_memories(
                    content, importance, confidence, keywords_json, insight,
                    occurred_at, embedding_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content,
                    float(importance),
                    float(confidence),
                    json.dumps(keywords or [], ensure_ascii=False),
                    insight,
                    occurred_at,
                    json.dumps(embedding) if embedding is not None else None,
                    now,
                    now,
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def keyword_episode_search(self, keywords: list[str], limit: int = 10):
        clean = [value.strip() for value in keywords if value.strip()]
        if not clean:
            return []
        clauses = " OR ".join("content LIKE ? OR keywords_json LIKE ?" for _ in clean)
        values: list[Any] = []
        for keyword in clean:
            pattern = f"%{keyword}%"
            values.extend((pattern, pattern))
        values.append(max(1, int(limit)))
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM episodic_memories
                WHERE {clauses}
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def search_episodes(
        self,
        query_embedding: list[float] | None,
        keywords: list[str],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM episodic_memories ORDER BY created_at DESC"
            ).fetchall()

        clean_keywords = [value.lower() for value in keywords if value.strip()]
        ranked: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            haystack = (
                str(item.get("content", ""))
                + " "
                + str(item.get("keywords_json", ""))
                + " "
                + str(item.get("insight", ""))
            ).lower()
            keyword_score = sum(1.0 for word in clean_keywords if word in haystack)
            similarity = 0.0
            raw_embedding = item.get("embedding_json")
            if query_embedding and raw_embedding:
                try:
                    similarity = self._cosine_similarity(
                        query_embedding, json.loads(raw_embedding)
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    similarity = 0.0
            if similarity <= 0.0 and keyword_score <= 0.0:
                continue
            score = similarity * 3.0 + keyword_score
            score += float(item.get("importance", 1.0)) * 0.05
            score += float(item.get("clarity", 1.0)) * 0.05
            ranked.append((score, item))

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        result = [item for _, item in ranked[: max(1, int(limit))]]
        if result:
            with self._lock:
                self._connection.executemany(
                    """
                    UPDATE episodic_memories
                    SET access_count = access_count + 1,
                        clarity = MIN(5.0, clarity + importance * 0.1),
                        last_accessed = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    [
                        (time.time(), time.time(), int(item["id"]))
                        for item in result
                    ],
                )
                self._connection.commit()
        return result

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        dot = sum(float(a) * float(b) for a, b in zip(left, right))
        left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
        right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / (left_norm * right_norm)

    def unconsolidated_episodes(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, content, insight, importance, keywords_json,
                       occurred_at, created_at
                FROM episodic_memories
                WHERE is_consolidated = 0
                ORDER BY id LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_episodes_consolidated(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            self._connection.execute(
                f"UPDATE episodic_memories SET is_consolidated = 1 WHERE id IN ({placeholders})",
                [int(value) for value in ids],
            )
            self._connection.commit()

    def decay_memories(self, factor: float) -> dict[str, int]:
        decay = max(0.0, min(1.0, float(factor)))
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, clarity, access_count FROM episodic_memories"
            ).fetchall()
            for row in rows:
                resistance = 1.0 + math.log1p(int(row["access_count"]))
                clarity = float(row["clarity"]) * math.exp(-decay / resistance)
                self._connection.execute(
                    "UPDATE episodic_memories SET clarity = ?, updated_at = ? WHERE id = ?",
                    (clarity, time.time(), int(row["id"])),
                )
            deleted = self._connection.execute(
                "DELETE FROM episodic_memories WHERE clarity < 0.05"
            ).rowcount
            self._connection.commit()
        return {"updated": len(rows), "deleted": max(0, int(deleted))}

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        aliases: list[str] | None,
        properties: dict[str, Any] | None,
        incremental: bool = True,
    ) -> int:
        now = time.time()
        with self._lock:
            existing = self._connection.execute(
                "SELECT aliases_json, properties_json FROM entities WHERE name = ?",
                (name,),
            ).fetchone()
            clean_aliases = list(aliases or [])
            clean_properties = dict(properties or {})
            if existing is not None and incremental:
                clean_aliases = self._merge_lists(
                    json.loads(existing["aliases_json"]), clean_aliases
                )
                old_properties = json.loads(existing["properties_json"])
                clean_properties = self._merge_properties(
                    old_properties, clean_properties
                )
            self._connection.execute(
                """
                INSERT INTO entities(
                    name, entity_type, aliases_json, properties_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    entity_type=excluded.entity_type,
                    aliases_json=excluded.aliases_json,
                    properties_json=excluded.properties_json,
                    updated_at=excluded.updated_at
                """,
                (
                    name,
                    entity_type,
                    json.dumps(clean_aliases, ensure_ascii=False),
                    json.dumps(clean_properties, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = self._connection.execute(
                "SELECT id FROM entities WHERE name = ?", (name,)
            ).fetchone()
            self._connection.commit()
            return int(row["id"])

    def upsert_relationship(
        self,
        source: str,
        target: str,
        relation: str,
        properties: dict[str, Any] | None = None,
        incremental: bool = True,
    ) -> int:
        source_id = self._ensure_entity(source)
        target_id = self._ensure_entity(target)
        now = time.time()
        with self._lock:
            clean_properties = dict(properties or {})
            existing = self._connection.execute(
                """
                SELECT properties_json FROM relationships
                WHERE source_entity_id=? AND target_entity_id=? AND relation=?
                """,
                (source_id, target_id, relation),
            ).fetchone()
            if existing is not None and incremental:
                clean_properties = self._merge_properties(
                    json.loads(existing["properties_json"]), clean_properties
                )
            self._connection.execute(
                """
                INSERT INTO relationships(
                    source_entity_id, target_entity_id, relation,
                    properties_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_entity_id, target_entity_id, relation) DO UPDATE SET
                    properties_json=excluded.properties_json,
                    updated_at=excluded.updated_at
                """,
                (
                    source_id,
                    target_id,
                    relation,
                    json.dumps(clean_properties, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = self._connection.execute(
                """
                SELECT id FROM relationships
                WHERE source_entity_id=? AND target_entity_id=? AND relation=?
                """,
                (source_id, target_id, relation),
            ).fetchone()
            self._connection.commit()
            return int(row["id"])

    def graph_search(self, terms: list[str], limit: int = 8) -> dict[str, Any]:
        clean_terms = [value.lower().strip() for value in terms if value.strip()]
        if not clean_terms:
            return {"entities": [], "relations": []}
        with self._lock:
            rows = self._connection.execute("SELECT * FROM entities").fetchall()
            matches = []
            for row in rows:
                item = dict(row)
                haystack = (
                    item["name"] + " " + item["aliases_json"] + " " + item["properties_json"]
                ).lower()
                score = sum(1 for term in clean_terms if term in haystack)
                if score:
                    matches.append((score, item))
            matches.sort(key=lambda pair: pair[0], reverse=True)
            entities = [item for _, item in matches[: max(1, int(limit))]]
            ids = [int(item["id"]) for item in entities]
            relations = []
            if ids:
                placeholders = ",".join("?" for _ in ids)
                relations = [
                    dict(row)
                    for row in self._connection.execute(
                        f"""
                        SELECT r.*, source.name AS source, target.name AS target
                        FROM relationships r
                        JOIN entities source ON source.id = r.source_entity_id
                        JOIN entities target ON target.id = r.target_entity_id
                        WHERE r.source_entity_id IN ({placeholders})
                           OR r.target_entity_id IN ({placeholders})
                        LIMIT ?
                        """,
                        [*ids, *ids, max(1, int(limit))],
                    ).fetchall()
                ]
        return {"entities": entities, "relations": relations}

    @staticmethod
    def _merge_lists(old: list[Any], new: list[Any]) -> list[Any]:
        result = list(old)
        for value in new:
            if value not in result:
                result.append(value)
        return result

    @classmethod
    def _merge_properties(
        cls, old: dict[str, Any], new: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(old)
        for key, value in new.items():
            if isinstance(value, list) and isinstance(result.get(key), list):
                result[key] = cls._merge_lists(result[key], value)
            else:
                result[key] = value
        return result

    def _ensure_entity(self, name: str) -> int:
        now = time.time()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO entities(
                    name, entity_type, aliases_json, properties_json, created_at, updated_at
                ) VALUES (?, 'Entity', '[]', '{}', ?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (name, now, now),
            )
            row = self._connection.execute(
                "SELECT id FROM entities WHERE name = ?", (name,)
            ).fetchone()
            self._connection.commit()
            return int(row["id"])
