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
            graph_context = self._simplify_graph(
                future_graph.result(timeout=5), query=query, key_words=key_words
            )

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

    def _simplify_graph(self, graph_context: dict, query: str = "", key_words: list = None) -> dict:
        """语境探照灯：为每个实体的碎片字段智能选取最相关片段

        - List<String> 碎片字段：锚点保留 + 语境匹配 + 最新兜底，合成为字符串
        - 旧格式 String 字段（兼容）：截断至 80 字
        """
        entities = []
        kws = key_words or []
        for e in graph_context.get("entities", []):
            entry = dict(e)
            for field in ("bio", "vibe", "utility", "significance"):
                val = entry.get(field)
                if isinstance(val, list):
                    selected = self._select_relevant_fragments(val, query, kws)
                    entry[field] = "; ".join(selected)
                elif isinstance(val, str) and len(val) > 80:
                    entry[field] = val[:80]
            entities.append(entry)
        return {"entities": entities, "relations": graph_context.get("relations", [])}

    def _select_relevant_fragments(self, fragments: list, query: str, key_words: list) -> list:
        """从碎片列表选出最相关的片段

        策略：
          1. 锚点保留：始终保留第 0 条（核心身份定义）
          2. 语境扫描：对剩余碎片按关键词命中评分
             - 内容段（类别|时间|内容 的第3段）命中 +2
             - 类别段（第1段）命中 +1
          3. 取得分 > 0 的最高 2 条；得分均为 0 则取最新 2 条（末尾）
        碎片格式：类别|时间|内容
        """
        if not fragments:
            return []

        anchor = fragments[0]
        rest = fragments[1:]

        if not rest:
            return [anchor]

        # 构建关键词集合（key_words 直接用；query 按空白分词补充；≥2 字）
        kws = set(w.lower() for w in key_words if len(w) >= 2)
        kws.update(w.lower() for w in query.split() if len(w) >= 2)

        def score_frag(frag: str) -> int:
            parts = frag.split("|", 2)
            category = parts[0].lower() if len(parts) >= 1 else ""
            content = parts[2].lower() if len(parts) == 3 else frag.lower()
            s = 0
            for kw in kws:
                if kw in content:
                    s += 2
                if kw in category:
                    s += 1
            return s

        scored = [(score_frag(f), f) for f in rest]
        scored.sort(key=lambda x: x[0], reverse=True)

        matched = [f for sc, f in scored if sc > 0]
        if matched:
            tail = matched[:2]
        else:
            tail = rest[-2:]  # 兜底：最新 2 条

        return [anchor] + tail

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
