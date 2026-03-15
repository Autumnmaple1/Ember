"""
记忆处理模块 - 海马体

负责记忆的编码、存储和检索，整合向量记忆和图谱记忆
"""
import concurrent.futures
import json
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from brain.llm_client import LLMClient
from config.settings import settings
from core.event_bus import Event, EventBus

if TYPE_CHECKING:
    from memory.neo4j_memory import Neo4jGraphMemory

logger = logging.getLogger(__name__)

# 检索超时配置（秒）
RETRIEVAL_TIMEOUT = 5

# 片段评分权重
CONTENT_MATCH_SCORE = 2
CATEGORY_MATCH_SCORE = 1
MIN_KEYWORD_LENGTH = 2

# 描述字段列表
DESCRIPTION_FIELDS = ("bio", "vibe", "utility", "significance")


@dataclass
class MemoryQueryResult:
    """记忆查询结果"""
    keywords: list[str]
    query: str
    entities: list[str]


@dataclass
class RetrievalResult:
    """检索结果容器"""
    episodic_memories: list[dict]
    graph_context: dict


class Hippocampus:
    """
    海马体 - 记忆处理中心

    职责：
    1. 记忆编码：将对话历史编码为结构化记忆
    2. 记忆检索：基于查询检索相关记忆（向量 + 图谱）
    3. 记忆整合：合并多种来源的记忆结果
    """

    _file_lock = threading.Lock()

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self.llm_client = LLMClient()
        self.graph_memory: Neo4jGraphMemory | None = None

        self._subscribe_events()
        self._init_graph_memory()

    def _subscribe_events(self) -> None:
        """订阅事件总线事件"""
        self.event_bus.subscribe("memory.preprocess", self._on_preprocess_request)

    def _init_graph_memory(self) -> None:
        """初始化图谱记忆（如果启用）"""
        if not settings.ENABLE_NEO4J:
            return

        try:
            from memory.neo4j_memory import Neo4jGraphMemory
            self.graph_memory = Neo4jGraphMemory(self.event_bus)
        except Exception as e:
            logger.error(f"初始化图谱记忆失败: {e}")

    def _load_experience(self) -> str:
        """
        加载并清空对话历史日志

        Returns:
            日志内容，文件不存在返回空字符串
        """
        log_path = "./config/chat_history.log"

        try:
            with self._file_lock:
                try:
                    with open(log_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except FileNotFoundError:
                    return ""

                # 清空文件
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("")

                return content

        except (IOError, OSError) as e:
            logger.warning(f"读取日志文件失败: {e}")
            return ""

    def _on_preprocess_request(self, event: Event) -> None:
        """处理记忆预处理请求"""
        experience = self._load_experience()
        if not experience:
            logger.info("无经验日志，跳过预处理")
            return

        self._encode_and_store_memories(experience)

    def _encode_and_store_memories(self, experience: str) -> None:
        """调用 LLM 编码记忆并存储"""
        messages = [
            {"role": "system", "content": settings.MEMORY_ENCODING_PROMPT},
            {"role": "user", "content": f"提供的日志如下：\n\n{experience}"},
        ]

        response = self.llm_client.one_chat(settings.LARGE_LLM, messages)

        if response is None:
            logger.error("记忆编码 LLM 返回空响应")
            return

        memories = self._safe_parse_json(response)
        if not memories:
            return

        for memory in memories:
            logger.info(f"编码记忆: {memory}")
            self.event_bus.publish(Event("memory.store", memory))

    def road_memory(self, content: list[str]) -> str | None:
        """
        检索相关记忆（主入口）

        流程：
        1. 调用 LLM 判断是否需要记忆
        2. 如需记忆，并行查询向量记忆和图谱记忆
        3. 合并并返回结果

        Args:
            content: 查询内容列表

        Returns:
            JSON 格式的记忆字符串，无记忆返回 None
        """
        logger.info("开始检索记忆...")

        # 分析查询需求
        query_result = self._analyze_query_need(content)
        if query_result is None:
            return None

        # 并行检索
        retrieval = self._parallel_retrieval(
            query=query_result.query,
            keywords=query_result.keywords,
            entities=query_result.entities
        )

        # 构建结果
        result = {
            "episodic_memories": retrieval.episodic_memories,
            "graph_context": retrieval.graph_context,
        }

        self._log_retrieval_result(query_result, retrieval)
        return json.dumps(result, ensure_ascii=False)

    def _analyze_query_need(self, content: list[str]) -> MemoryQueryResult | None:
        """
        分析查询需求，调用 LLM 判断是否需要记忆

        Returns:
            查询参数，不需要记忆返回 None
        """
        system_prompt = settings.CORE_PERSONA + settings.MEMORY_JUDGE_PROMPT
        user_prompt = f"提供的日志如下：\n\n{content}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self.llm_client.one_chat(settings.SMALL_LLM, messages=messages)

        if response is None:
            logger.error("记忆判断 LLM 返回空响应")
            return None

        parsed = self._safe_parse_json(response)
        if parsed is None:
            return None

        # 判断是否需要记忆
        if not parsed.get("need_memory", False):
            logger.info("LLM 判断无需检索记忆")
            return None

        return MemoryQueryResult(
            keywords=parsed.get("keywords", []),
            query=parsed.get("query", ""),
            entities=parsed.get("entities", [])
        )

    def _parallel_retrieval(
        self,
        query: str,
        keywords: list[str],
        entities: list[str]
    ) -> RetrievalResult:
        """
        并行检索：向量记忆 + 图谱记忆

        Returns:
            合并的检索结果
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            # 提交两个检索任务
            future_episodic = executor.submit(
                self._retrieve_episodic_memory,
                query=query,
                keywords=keywords
            )
            future_graph = executor.submit(
                self._retrieve_graph_memory,
                entities=entities
            )

            # 获取结果（带超时）
            try:
                episodic = future_episodic.result(timeout=RETRIEVAL_TIMEOUT)
            except concurrent.futures.TimeoutError:
                logger.error("向量记忆检索超时")
                episodic = []

            try:
                graph = future_graph.result(timeout=RETRIEVAL_TIMEOUT)
            except concurrent.futures.TimeoutError:
                logger.error("图谱记忆检索超时")
                graph = {"entities": [], "relations": []}

        return RetrievalResult(
            episodic_memories=self._simplify_episodic_memories(episodic),
            graph_context=graph
        )

    def _retrieve_episodic_memory(
        self,
        query: str,
        keywords: list[str]
    ) -> list[dict]:
        """检索向量记忆（情节记忆）"""
        query_data = {"query": query, "key_words": keywords}
        future = concurrent.futures.Future()

        def on_retrieved(memories: list) -> None:
            if not future.done():
                future.set_result(memories)

        query_data["callback"] = on_retrieved
        self.event_bus.publish(Event("memory.query", query_data))

        try:
            return future.result(timeout=RETRIEVAL_TIMEOUT)
        except concurrent.futures.TimeoutError:
            logger.error("情节记忆检索超时")
            return []

    def _retrieve_graph_memory(self, entities: list[str]) -> dict:
        """检索图谱记忆"""
        if not self.graph_memory or not entities:
            return {"entities": [], "relations": []}

        try:
            result = self.graph_memory.query_entities_by_names_with_aliases(entities)
            return self._simplify_graph_result(result)
        except Exception as e:
            logger.error(f"图谱记忆检索失败: {e}")
            return {"entities": [], "relations": []}

    def _simplify_episodic_memories(self, memories: list[dict]) -> list[dict]:
        """简化情节记忆结果，截断长内容"""
        MAX_CONTENT_LEN = 200
        MAX_INSIGHT_LEN = 100

        simplified = []
        for mem in memories:
            content = mem.get("content", "")
            insight = mem.get("insight", "")

            simplified.append({
                "content": content[:MAX_CONTENT_LEN] if len(content) > MAX_CONTENT_LEN else content,
                "insight": insight[:MAX_INSIGHT_LEN] if len(insight) > MAX_INSIGHT_LEN else insight,
                "time": mem.get("time", ""),
            })
        return simplified

    def _simplify_graph_result(self, graph_context: dict) -> dict:
        """
        简化图谱结果

        - 列表字段：保留锚点 + 选取最相关片段
        - 字符串字段：截断至 80 字符
        """
        MAX_STRING_LEN = 80

        entities = []
        for entity in graph_context.get("entities", []):
            entry = dict(entity)

            for field in DESCRIPTION_FIELDS:
                value = entry.get(field)

                if isinstance(value, list):
                    # 列表字段：智能选取相关片段
                    selected = self._select_relevant_fragments(value)
                    entry[field] = "; ".join(selected)
                elif isinstance(value, str) and len(value) > MAX_STRING_LEN:
                    # 字符串字段：简单截断
                    entry[field] = value[:MAX_STRING_LEN]

            entities.append(entry)

        return {
            "entities": entities,
            "relations": graph_context.get("relations", [])
        }

    def _select_relevant_fragments(self, fragments: list[str]) -> list[str]:
        """
        从碎片列表选取最相关的片段

        策略：
        1. 锚点保留：始终保留第 0 条（核心身份定义）
        2. 语境扫描：按关键词命中评分
        3. 取得分 > 0 的最高 2 条；得分均为 0 则取最新 2 条

        碎片格式：类别|时间|内容
        """
        if not fragments:
            return []

        anchor = fragments[0]
        rest = fragments[1:]

        if not rest:
            return [anchor]

        # 计算每个片段的得分
        scored = [(self._calculate_fragment_score(f), f) for f in rest]
        scored.sort(key=lambda x: x[0], reverse=True)

        # 选取得分 > 0 的，否则取最新的
        matched = [f for score, f in scored if score > 0]
        tail = matched[:2] if matched else rest[-2:]

        return [anchor] + tail

    def _calculate_fragment_score(self, fragment: str) -> int:
        """计算片段相关性得分（简化版，不带关键词时使用）"""
        # 这里可以扩展为基于查询关键词的评分
        # 目前简单返回 0，表示使用兜底策略
        return 0

    def _safe_parse_json(self, content: str) -> list | dict | None:
        """
        安全解析 JSON

        Args:
            content: JSON 字符串

        Returns:
            解析结果，失败返回 None
        """
        try:
            return self.llm_client._extract_json(content)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"JSON 解析失败: {e}")
            logger.debug(f"原始内容: {content[:200]}...")
            return None

    def _log_retrieval_result(
        self,
        query: MemoryQueryResult,
        retrieval: RetrievalResult
    ) -> None:
        """记录检索结果日志"""
        logger.info(
            f"记忆检索完成 | "
            f"Query: {query.query} | "
            f"Keywords: {query.keywords} | "
            f"Entities: {query.entities} | "
            f"Episodic: {len(retrieval.episodic_memories)} | "
            f"Graph: {len(retrieval.graph_context.get('entities', []))}"
        )
