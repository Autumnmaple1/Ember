from __future__ import annotations

from datetime import datetime
import json
import re
import threading
from typing import Any

from .config_store import MobileConfigStore
from .llm_client import MobileLLMClient
from .memory_db import MobileMemoryDatabase
from .prompt_store import (
    CORE_PERSONA,
    GRAPH_CONSOLIDATION_PROMPT,
    MEMORY_ENCODING_PROMPT,
    MEMORY_JUDGE_PROMPT,
    SYSTEM_PROMPT,
)


class MobileMemoryManager:
    def __init__(
        self,
        database: MobileMemoryDatabase,
        config_store: MobileConfigStore,
    ):
        self._database = database
        self._config_store = config_store
        self._schedule_lock = threading.Lock()
        self._encode_lock = threading.Lock()
        self._sleep_lock = threading.Lock()
        self._encode_scheduled = False

    def schedule_encode(self, force: bool = False) -> None:
        with self._schedule_lock:
            if self._encode_scheduled:
                return
            self._encode_scheduled = True
        threading.Thread(
            target=self._encode_worker,
            args=(force,),
            name="ember-mobile-memory-encode",
            daemon=True,
        ).start()

    def try_acquire_archive_lock(self) -> bool:
        if not self._sleep_lock.acquire(blocking=False):
            return False
        if not self._encode_lock.acquire(blocking=False):
            self._sleep_lock.release()
            return False
        return True

    def release_archive_lock(self) -> None:
        self._encode_lock.release()
        self._sleep_lock.release()

    def _encode_worker(self, force: bool) -> None:
        try:
            self.encode_pending(force=force)
        finally:
            with self._schedule_lock:
                self._encode_scheduled = False

    def encode_pending(self, force: bool = False) -> int:
        with self._encode_lock:
            return self._encode_pending_locked(force)

    def _encode_pending_locked(self, force: bool = False) -> int:
        rows = self._database.memory_buffer_snapshot()
        if not rows:
            return 0
        config = self._config_store.private_value()
        total_transcript = self._format_transcript(rows)
        threshold = int(config.get("memory_encode_threshold", 5000))
        if not force and len(total_transcript) < threshold:
            return 0

        # 分批编码：每批最多 30 条或 8000 字符，避免单次上下文过长被截断。
        max_rows = 30
        max_chars = 8000
        stored_total = 0
        index = 0
        while index < len(rows):
            batch_rows = []
            batch_chars = 0
            while index < len(rows) and len(batch_rows) < max_rows:
                line = self._format_transcript([rows[index]])
                if not line:
                    index += 1
                    continue
                if batch_rows and batch_chars + len(line) > max_chars:
                    break
                batch_rows.append(rows[index])
                batch_chars += len(line)
                index += 1
            if not batch_rows:
                break
            stored_total += self._encode_batch(batch_rows, config)
            # 整批确认，已编码的日志不再保留，避免重复编码。
            self._database.acknowledge_memory_buffer(
                batch_rows[-1]["id"], 0
            )
        return stored_total

    def _encode_batch(
        self,
        rows: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> int:
        transcript = self._format_transcript(rows)
        raw = MobileLLMClient.chat(
            config,
            [
                {"role": "system", "content": MEMORY_ENCODING_PROMPT},
                {"role": "user", "content": f"提供的日志如下：\n\n{transcript}"},
            ],
        )
        parsed = self._extract_json(raw)
        if not isinstance(parsed, list):
            raise ValueError("记忆编码模型没有返回 JSON 数组")

        stored = 0
        embedding_config = self._config_store.embedding_model_value()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            # 放宽硬截断：保留完整事实链，仅在超限时按句子边界截断。
            content = self._truncate_text(
                str(item.get("content", "")).strip(), 1000
            )
            if not content:
                continue
            insight = self._truncate_text(
                str(item.get("insight", "")).strip(), 300
            )
            keywords = item.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            clean_keywords = [
                str(value).strip()
                for value in keywords
                if str(value).strip()
            ][:50]
            embedding = None
            if embedding_config.get("api_key"):
                try:
                    embedding = MobileLLMClient.embedding(
                        embedding_config,
                        f"{content}\n{insight}",
                    )
                except Exception:
                    embedding = None
            self._database.add_episode(
                content=content,
                importance=self._number(item.get("importance"), 1.0, 0.0, 2.0),
                confidence=self._number(item.get("confidence"), 1.0, 0.0, 1.0),
                keywords=clean_keywords,
                insight=insight,
                occurred_at=str(item.get("time", "")).strip() or None,
                embedding=embedding,
            )
            stored += 1
        return stored

    def schedule_sleep_consolidation(self) -> None:
        if not self._sleep_lock.acquire(blocking=False):
            return
        threading.Thread(
            target=self._sleep_worker,
            name="ember-mobile-memory-sleep",
            daemon=True,
        ).start()

    def _sleep_worker(self) -> None:
        try:
            self.encode_pending(force=True)
            config = self._config_store.private_value()
            if config.get("graph_memory_enabled", True):
                self.consolidate_graph()
            self._database.decay_memories(
                float(config.get("memory_decay_factor", 0.2))
            )
        finally:
            self._sleep_lock.release()

    def consolidate_graph(self) -> dict[str, int]:
        totals = {"batches": 0, "nodes": 0, "edges": 0}
        while True:
            memories = self._database.unconsolidated_episodes(30)
            if not memories:
                return totals
            summaries = []
            for item in memories:
                time_text = str(item.get("occurred_at") or "").strip()
                if not time_text and item.get("created_at"):
                    try:
                        time_text = datetime.fromtimestamp(
                            float(item["created_at"])
                        ).strftime("%Y-%m-%d %H:%M:%S")
                    except (TypeError, ValueError, OSError):
                        time_text = ""
                summary = (
                    f"[{len(summaries) + 1}] "
                    f"时间: {time_text or '未知'}\n"
                    f"内容: {item.get('content', '')}"
                )
                insight = item.get("insight")
                if insight:
                    summary += f"\n感想: {insight}"
                keywords = json.loads(item.get("keywords_json") or "[]")
                if keywords:
                    summary += (
                        "\n关键词: "
                        + json.dumps({"keywords": keywords}, ensure_ascii=False)
                    )
                summaries.append(summary)
            raw = MobileLLMClient.chat(
                self._config_store.private_value(),
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": GRAPH_CONSOLIDATION_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "请处理以下摘要，并按上述格式输出：\n\n"
                            + "\n".join(summaries)
                        ),
                    },
                ],
            )
            operations = self._extract_json(raw)
            if not isinstance(operations, list):
                raise ValueError("图谱整理模型没有返回 JSON 数组")
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                kind = operation.get("operation")
                incremental = bool(operation.get("is_increment", True))
                if kind == "upsert_node":
                    name = str(operation.get("name", "")).strip()
                    if not name:
                        continue
                    properties = operation.get("properties", {})
                    if not isinstance(properties, dict):
                        properties = {}
                    properties = dict(properties)
                    aliases = properties.pop("aliases", [])
                    if not isinstance(aliases, list):
                        aliases = []
                    self._database.upsert_entity(
                        name,
                        str(operation.get("type", "Entity")),
                        [str(value) for value in aliases],
                        properties,
                        incremental,
                    )
                    totals["nodes"] += 1
                elif kind == "upsert_edge":
                    source = str(operation.get("source", "")).strip()
                    target = str(operation.get("target", "")).strip()
                    relation = str(operation.get("relation", "")).strip()
                    if not source or not target or not relation:
                        continue
                    properties = operation.get("properties", {})
                    self._database.upsert_relationship(
                        source,
                        target,
                        relation,
                        properties if isinstance(properties, dict) else {},
                        incremental,
                    )
                    totals["edges"] += 1
            self._database.mark_episodes_consolidated(
                [int(item["id"]) for item in memories]
            )
            totals["batches"] += 1

    def recall(self, user_text: str) -> str:
        stats = self._database.memory_stats()
        if stats["episodic_count"] == 0 and stats["entity_count"] == 0:
            return ""

        query = user_text.strip()
        keywords: list[str] = []
        entities: list[str] = []
        try:
            raw = MobileLLMClient.chat(
                self._config_store.state_model_value(),
                [
                    {
                        "role": "system",
                        "content": CORE_PERSONA + "\n" + MEMORY_JUDGE_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": f"提供的日志如下：\n\n{user_text}",
                    },
                ],
            )
            decision = self._extract_json(raw)
            if not isinstance(decision, dict) or not decision.get("need_memory", False):
                return ""
            query = str(decision.get("query", user_text)).strip() or user_text
            raw_keywords = decision.get("keywords", [])
            if isinstance(raw_keywords, list):
                keywords = [str(value).strip() for value in raw_keywords if str(value).strip()]
            raw_entities = decision.get("entities", [])
            if isinstance(raw_entities, list):
                entities = [str(value).strip() for value in raw_entities if str(value).strip()]
        except Exception:
            keywords = self._fallback_keywords(user_text)

        return self.query_memory(query, keywords, entities)

    def query_memory(
        self,
        query: str,
        keywords: list[str] | None = None,
        entities: list[str] | None = None,
    ) -> str:
        """直接检索本地记忆，供对话预检索和工具调用共同使用。"""
        stats = self._database.memory_stats()
        if stats["episodic_count"] == 0 and stats["entity_count"] == 0:
            return ""

        query = query.strip()
        if not query:
            return ""
        keywords = [value.strip() for value in (keywords or []) if value.strip()]
        entities = [value.strip() for value in (entities or []) if value.strip()]

        embedding = None
        embedding_config = self._config_store.embedding_model_value()
        if embedding_config.get("api_key"):
            try:
                embedding = MobileLLMClient.embedding(embedding_config, query)
            except Exception:
                embedding = None

        config = self._config_store.private_value()
        memories = self._database.search_episodes(
            embedding,
            keywords or self._fallback_keywords(query),
            int(config.get("recall_top_k", 5)),
        )
        graph = {"entities": [], "relations": []}
        if config.get("graph_memory_enabled", True):
            graph = self._database.graph_search(
                entities + keywords + self._fallback_keywords(query),
                int(config.get("recall_top_k", 5)),
            )
        if not memories and not graph["entities"]:
            return ""
        lines = []
        for memory in memories:
            date = str(memory.get("occurred_at") or "")[:10]
            prefix = f"[{date}] " if date else ""
            line = prefix + str(memory.get("content", "")).strip()
            insight = str(memory.get("insight", "")).strip()
            if insight:
                line += f"（理解：{insight}）"
            lines.append(line)
        for entity in graph["entities"]:
            properties = json.loads(entity.get("properties_json") or "{}")
            aliases = json.loads(entity.get("aliases_json") or "[]")
            description = json.dumps(properties, ensure_ascii=False)[:240]
            alias_text = f"（别名：{'、'.join(aliases)}）" if aliases else ""
            lines.append(f"[实体] {entity['name']}{alias_text}: {description}")
        for relation in graph["relations"]:
            details = json.loads(relation.get("properties_json") or "{}")
            detail_text = json.dumps(details, ensure_ascii=False)[:160]
            lines.append(
                f"[关系] {relation['source']} {relation['relation']} "
                f"{relation['target']}: {detail_text}"
            )
        return "\n".join(lines)

    def _format_transcript(self, rows: list[dict[str, Any]]) -> str:
        lines = []
        character_name = str(
            self._config_store.private_value().get("character_name", "依鸣")
        ).strip() or "依鸣"
        user_name = str(
            self._config_store.private_value().get("user_name", "用户")
        ).strip() or "用户"
        for row in rows:
            role = row.get("role")
            content = str(row.get("content", ""))
            content = re.sub(r"<thought>.*?</thought>", "", content, flags=re.I | re.S)
            content = re.sub(r"</?speech[^>]*>", "", content, flags=re.I)
            content = content.strip()
            if content and role in ("user", "assistant"):
                label = user_name if role == "user" else character_name
                lines.append(f"{label}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _extract_json(raw: str) -> Any:
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.S)
            if not match:
                raise
            return json.loads(match.group(1))

    @staticmethod
    def _fallback_keywords(text: str) -> list[str]:
        words = re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,8}", text)
        return list(dict.fromkeys(words))[:10]

    @staticmethod
    def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        """超过上限时优先在句子边界截断，尽量保留完整语义。"""
        if len(text) <= limit:
            return text
        head = text[:limit]
        for marker in ("。", "！", "？", "；", "…", ". ", "! ", "? "):
            index = head.rfind(marker)
            if index >= limit - 30:
                return head[: index + 1].strip()
        return head.strip()
