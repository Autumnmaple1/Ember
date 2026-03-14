import time
import threading
import logging
import json
import concurrent.futures
from config.settings import settings
from core.event_bus import EventBus, Event
from brain.llm_client import LLMClient
from memory.neo4j_memory import Neo4jGraphMemory

logger = logging.getLogger(__name__)


class Hippocampus:
    _file_lock = threading.Lock()

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.event_bus.subscribe("memory.preprocess", self._on_preprocess_request)
        self.llm_client = LLMClient()

        # 初始化 Neo4j 图谱连接
        self.graph_memory = None
        if settings.ENABLE_NEO4J:
            self.graph_memory = Neo4jGraphMemory(event_bus)

    def _load_experience(self):
        try:
            with self._file_lock:
                with open("./config/chat_history.log", "r", encoding="utf-8") as f:
                    content = f.read()
                with open("./config/chat_history.log", "w", encoding="utf-8") as f:
                    f.write("")
                return content

        except FileNotFoundError:
            logger.warning("chat_history.log not found")
            return ""

    def _on_preprocess_request(self, event: Event):
        res = self._load_experience()
        if not res:
            logger.info("No experience log found, skipping preprocessing.")
            return

        system_prompt = settings.MEMORY_ENCODING_PROMPT
        user_prompt = f"提供的日志如下：\n\n{res}"
        resp = self.llm_client.one_chat(
            settings.LARGE_LLM,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        if resp is None:
            logger.error(
                "LLM returned no response during memory preprocessing (resp is None)"
            )
            return

        try:
            memories = self.llm_client._extract_json(resp)
            if memories is None:
                raise json.JSONDecodeError("Failed to extract JSON", resp, 0)

            for mem in memories:
                logger.info(f"Preprocessed memory: {mem}")
                self.event_bus.publish(Event("memory.store", mem))
        except (TypeError, json.JSONDecodeError) as e:
            logger.error(
                f"Failed to decode LLM response during memory preprocessing: {e}. Raw response: {repr(resp)}"
            )

    def road_memory(self, content):
        system_prompt = settings.CORE_PERSONA + settings.MEMORY_JUDGE_PROMPT
        logger.info(f"Loading Memory\n")
        user_prompt = f"提供的日志如下：\n\n{content}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp = self.llm_client.one_chat(settings.SMALL_LLM, messages=messages)

        # 检查 LLM 响应是否为空
        if resp is None:
            logger.error("LLM returned no response during memory loading (resp is None)")
            return []

        key_words = []
        query = ""
        entities = []

        resp_json = self.llm_client._extract_json(resp)
        if resp_json is None:
            logger.error(f"Failed to extract JSON from LLM response: {repr(resp)}")
            return []

        if resp_json.get("need_memory", False) is False:
            logger.info("LLM judged that no memory retrieval is needed.")
            return []
        try:
            key_words = resp_json.get("keywords", [])
            query = resp_json.get("query", "")
            entities = resp_json.get("entities", [])

            # 并行检索：向量检索 + 图谱查询
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                # 任务1：PostgreSQL 向量检索
                future_episodic = executor.submit(
                    self._get_persistence_memory,
                    {"query": query, "key_words": key_words},
                )

                # 任务2：Neo4j 图谱查询
                future_graph = executor.submit(self._get_graph_memory, entities)

            # 获取向量检索结果
            raw_memories = future_episodic.result(timeout=5)
            simplified_memories = self._simplify_memories(raw_memories)

            # 获取图谱查询结果
            graph_context = self._simplify_graph(future_graph.result(timeout=5))

            # 整合结果
            result = {
                "episodic_memories": simplified_memories,
                "graph_context": graph_context,
            }
            memories = json.dumps(result, ensure_ascii=False)

            logger.info(
                f"Road Memory\nQuery: {query}\nKey Words: {key_words}\nEntities: {entities}\n"
            )
            logger.info(f"Retrieved memory: {len(simplified_memories)} items")
            if graph_context["entities"]:
                logger.info(
                    f"Graph entities: {[e.get('name') for e in graph_context['entities']]}"
                )

            return memories

        except concurrent.futures.TimeoutError:
            logger.error("检索超时，跳过查询以维持对话。")
            return []
        except (TypeError, json.JSONDecodeError) as e:
            logger.error(
                f"Failed to decode LLM response during memory loading: {e}. Raw response: {repr(resp)}"
            )
            return []

    def _get_graph_memory(self, entities: list) -> dict:
        """从 Neo4j 图谱中检索实体及关系"""
        if not self.graph_memory or not entities:
            return {"entities": [], "relations": []}

        try:
            result = self.graph_memory.query_entities_by_names_with_aliases(entities)
            logger.info(
                f"Graph query: found {len(result['entities'])} entities, {len(result['relations'])} relations"
            )
            return result
        except Exception as e:
            logger.error(f"Graph memory query failed: {e}")
            return {"entities": [], "relations": []}

    def _simplify_memories(self, memories):
        simplified = []
        for mem in memories:
            content = mem.get("content", "")
            insight = mem.get("insight", "")
            simplified.append(
                {
                    "content": content[:200] if len(content) > 200 else content,
                    "insight": insight[:100] if len(insight) > 100 else insight,
                    "time": mem.get("time", ""),
                }
            )
        return simplified

    def _simplify_graph(self, graph_context: dict) -> dict:
        """简化图谱实体字段，减少 token 占用

        - List<String> 描述字段：保留最新 3 条碎片
        - 旧格式 String 字段（兼容）：截断至 80 字
        """
        entities = []
        for e in graph_context.get("entities", []):
            entry = dict(e)
            for field in ("bio", "vibe", "utility", "significance"):
                val = entry.get(field)
                if isinstance(val, list):
                    entry[field] = val[-3:]  # 保留最新 3 条
                elif isinstance(val, str) and len(val) > 80:
                    entry[field] = val[:80]  # 旧格式兼容截断
            entities.append(entry)
        return {"entities": entities, "relations": graph_context.get("relations", [])}

    def _get_persistence_memory(self, query_data):
        future = concurrent.futures.Future()

        def on_memory_retrieved(memories):
            if not future.done():
                logger.info(f"Retrieved relevant memories: {len(memories)} items")
                future.set_result(memories)

        query_data["callback"] = on_memory_retrieved
        self.event_bus.publish(Event("memory.query", query_data))
        try:
            return future.result(timeout=5)
        except concurrent.futures.TimeoutError:
            logger.error("查询持久化记忆超时，跳过查询以维持对话。")
            return []
        except Exception as e:
            logger.error(f"查询持久化记忆时发生异常: {e}")
            return []
