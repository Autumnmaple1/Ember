import time
import threading
import logging
import json
import concurrent.futures
from config.settings import settings
from core.event_bus import EventBus, Event
from brain.llm_client import LLMClient
from brain.tag_utils import remove_speech_tags
from memory.postgres_graph_memory import PostgresGraphMemory

logger = logging.getLogger(__name__)


class Hippocampus:
    _file_lock = threading.Lock()

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.event_bus.subscribe("memory.preprocess", self._on_preprocess_request)
        self.llm_client = LLMClient()

        # 初始化 PostgreSQL 图谱存储
        self.graph_memory = None
        if settings.ENABLE_GRAPH_MEMORY:
            self.graph_memory = PostgresGraphMemory(event_bus)

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
            call_type="memory_encode",
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
                # 预先清理可能包含在 LLM 生成的摘要中的标签
                if "content" in mem:
                    mem["content"] = remove_speech_tags(mem["content"])
                if "insight" in mem:
                    mem["insight"] = remove_speech_tags(mem["insight"])
                
                logger.info(f"Preprocessed memory: {mem}")
                self.event_bus.publish(Event("memory.store", mem))
        except (TypeError, json.JSONDecodeError) as e:
            logger.error(
                f"Failed to decode LLM response during memory preprocessing: {e}. Raw response: {repr(resp)}"
            )

    def query_memory(
        self,
        query: str,
        keywords: list = None,
        entities: list = None,
        timeout: float = 10.0,
    ) -> dict:
        """
        核心记忆检索方法 - 供工具和内部调用统一入口

        Args:
            query: 检索查询语句（陈述句描述目标记忆）
            keywords: 关键词列表（用于向量检索和图谱查询）
            entities: 实体列表（用于图谱查询，可选）
            timeout: 超时时间（秒）

        Returns:
            dict: {
                "found": bool,
                "episodic_memories": ["[日期] 内容", ...],
                "graph_entities": {"名字": "描述", ...},
                "graph_relations": ["A 关系 B", ...],
                "query": str,
                "keywords": list
            }
        """
        keywords = keywords or []
        entities = entities or []

        # 从 keywords 中筛选可能的实体名（长度>=2的专有名词）
        potential_entities = entities or [kw for kw in keywords if len(kw) >= 2]

        logger.info(f"[query_memory] query='{query[:50]}...', keywords={keywords}")

        try:
            # 并行检索：PostgreSQL 向量检索 + 关系图查询
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                # 任务1：PostgreSQL 向量检索
                future_episodic = executor.submit(
                    self._get_persistence_memory,
                    {"query": query, "key_words": keywords},
                )

                # 任务2：PostgreSQL 实体关系查询
                future_graph = executor.submit(
                    self._get_graph_memory, potential_entities
                )

            # 获取向量检索结果
            raw_memories = future_episodic.result(timeout=timeout)
            simplified_memories = self._simplify_memories(raw_memories)

            # 获取图谱查询结果
            graph_context = self._simplify_graph(
                future_graph.result(timeout=timeout), query=query, key_words=keywords
            )

            # 统计结果
            episodic_count = len(simplified_memories)
            graph_entity_count = len(graph_context.get("entities", {}))
            found = episodic_count > 0 or graph_entity_count > 0

            logger.info(
                f"[query_memory] 检索完成: 情景记忆 {episodic_count} 条 | "
                f"图谱实体 {graph_entity_count} 个"
            )

            return {
                "found": found,
                "episodic_memories": simplified_memories,
                "graph_entities": graph_context.get("entities", {}),
                "graph_relations": graph_context.get("relations", []),
                "query": query,
                "keywords": keywords,
            }

        except concurrent.futures.TimeoutError:
            logger.error("[query_memory] 检索超时")
            return {
                "found": False,
                "episodic_memories": [],
                "graph_entities": {},
                "graph_relations": [],
                "query": query,
                "keywords": keywords,
                "error": "检索超时",
            }
        except Exception as e:
            logger.exception(f"[query_memory] 检索失败: {e}")
            return {
                "found": False,
                "episodic_memories": [],
                "graph_entities": {},
                "graph_relations": [],
                "query": query,
                "keywords": keywords,
                "error": str(e),
            }

    def load_memory(self, content):
        """
        旧接口 - 通过 LLM 判断是否需要检索记忆
        保留兼容性，内部调用 query_memory
        """
        system_prompt = settings.CORE_PERSONA + "\n" + settings.MEMORY_JUDGE_PROMPT
        logger.info(f"Loading Memory\n")
        user_prompt = f"提供的日志如下：\n\n{content}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp = self.llm_client.one_chat(
            settings.SMALL_LLM, messages=messages, call_type="memory_query"
        )

        # 检查 LLM 响应是否为空
        if resp is None:
            logger.error(
                "LLM returned no response during memory loading (resp is None)"
            )
            return []

        resp_json = self.llm_client._extract_json(resp)
        if resp_json is None:
            logger.error(f"Failed to extract JSON from LLM response: {repr(resp)}")
            return []

        # LLM 判断不需要检索
        if resp_json.get("need_memory", False) is False:
            logger.info("LLM judged that no memory retrieval is needed.")
            return []

        try:
            keywords = resp_json.get("keywords", [])
            query = resp_json.get("query", "")
            entities = resp_json.get("entities", [])

            # 调用统一的检索方法
            result = self.query_memory(query, keywords, entities)

            if result.get("found"):
                logger.info(
                    f"Road Memory\nQuery: {query}\nKey Words: {keywords}\nEntities: {entities}\n"
                )
                # 返回旧格式（JSON字符串）保持兼容
                return json.dumps(result, ensure_ascii=False)
            return []

        except Exception as e:
            logger.error(f"load_memory 失败: {e}")
            return []

    def _get_graph_memory(self, entities: list) -> dict:
        """从 PostgreSQL 实体关系表中检索实体及关系"""
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
        """压缩记忆格式：每条记忆一行，减少 JSON 语法开销
        格式：[时间] 内容
        """
        lines = []
        for mem in memories:
            content = mem.get("content", "")
            time = mem.get("time", "")[:10]  # 只取日期
            lines.append(f"[{time}] {content}")
        return lines

    def _simplify_graph(
        self, graph_context: dict, query: str = "", key_words: list = None
    ) -> dict:
        """语境探照灯：为每个实体的碎片字段智能选取最相关片段

        返回压缩格式：
        - entities: {"名字": "bio内容", ...}  # 只保留有描述字段的
        - relations: ["A 关系 B", ...]  # 紧凑关系字符串
        """
        entities = {}
        kws = key_words or []
        for e in graph_context.get("entities", []):
            name = e.get("name", "")
            if not name:
                continue
            # 提取描述字段
            desc_parts = []
            for field in ("bio", "vibe", "utility", "significance"):
                val = e.get(field)
                if isinstance(val, list):
                    selected = self._select_relevant_fragments(val, query, kws)
                    if selected:
                        desc_parts.append("; ".join(selected)[:100])
                elif isinstance(val, str) and val:
                    desc_parts.append(val[:80])
            if desc_parts:
                entities[name] = " | ".join(desc_parts)

        # 压缩关系格式："A 关系 B"
        relations = []
        for r in graph_context.get("relations", []):
            src = r.get("source", "")
            tgt = r.get("target", "")
            rel = r.get("relation", "")
            if src and tgt and rel:
                relations.append(f"{src} {rel} {tgt}")

        return {"entities": entities, "relations": relations}

    def _select_relevant_fragments(
        self, fragments: list, query: str, key_words: list
    ) -> list:
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
