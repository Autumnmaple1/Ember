import json
import logging
import re
import psycopg2
from pgvector.psycopg2 import register_vector
from core.event_bus import EventBus, Event
from config.settings import settings
from brain.llm_client import LLMClient
from memory.neo4j_memory import Neo4jGraphMemory

logger = logging.getLogger(__name__)

BATCH_SIZE = 30  # 每批处理的记忆数量


class EntityExtractionMemory:
    """实体提取与知识图谱构建器

    负责：
    1. 监听 memory.sleep 事件触发知识图谱整理
    2. 从 episodic_memory 数据库读取记忆
    3. 批量调用 LLM 提取实体和关系
    4. 存储到 Neo4j 知识图谱
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.llm_client = LLMClient()
        self.enabled = settings.ENABLE_NEO4J
        self.graph_memory = None
        self.conn = None

        if self.enabled:
            self.graph_memory = Neo4jGraphMemory(event_bus)
            self._ensure_connection()
            self._subscribe_events()

    def _ensure_connection(self):
        """确保 PostgreSQL 连接"""
        try:
            if self.conn is None or self.conn.closed:
                self.conn = psycopg2.connect(
                    dbname=settings.PG_DB,
                    user=settings.PG_USER,
                    password=settings.PG_PASSWORD,
                    host=settings.PG_HOST,
                    port=settings.PG_PORT,
                    connect_timeout=5,
                )
                register_vector(self.conn)
                logger.debug("EntityExtractionMemory connected to PostgreSQL.")
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            self.conn = None

    def _subscribe_events(self):
        """订阅相关事件"""
        self.event_bus.subscribe("memory.sleep", self._on_memory_sleep)

    def _on_memory_sleep(self, event: Event):
        """处理记忆休眠事件 - 触发知识图谱整理"""
        logger.info("收到 memory.sleep 事件，开始知识图谱整理...")
        # 在后台线程中执行，避免阻塞事件总线
        import threading

        threading.Thread(target=self.consolidate_all_memories, daemon=True).start()

    def consolidate_all_memories(self) -> dict:
        """整理全部记忆 - 从 episodic_memory 读取并批量处理

        Returns:
            整理结果统计 {"batches": int, "nodes": int, "edges": int}
        """
        if not self.enabled or not self.graph_memory:
            logger.warning("Neo4j 未启用，跳过知识图谱整理")
            return {"batches": 0, "nodes": 0, "edges": 0}

        self._ensure_connection()
        if not self.conn:
            logger.error("无法连接 PostgreSQL，跳过整理")
            return {"batches": 0, "nodes": 0, "edges": 0}

        try:
            # 1. 从 episodic_memory 读取所有记忆
            memories = self._fetch_all_memories()
            if not memories:
                logger.info("没有记忆需要整理")
                return {"batches": 0, "nodes": 0, "edges": 0}

            logger.info(f"共读取 {len(memories)} 条记忆，开始批量整理...")

            # 2. 分批处理
            total_nodes = 0
            total_edges = 0
            batch_count = 0

            for i in range(0, len(memories), BATCH_SIZE):
                batch = memories[i : i + BATCH_SIZE]
                batch_count += 1
                batch_ids = [m["id"] for m in batch]  # 收集本批次的记忆 ID

                # 构建摘要文本
                summaries = self._build_summaries(batch)

                # 调用 LLM 提取（同步）
                result = self._extract_and_store(summaries)
                total_nodes += result["nodes"]
                total_edges += result["edges"]

                # 标记本批次记忆为已整理
                self._mark_memories_consolidated(batch_ids)

                logger.info(
                    f"批次 {batch_count} 完成: {result['nodes']} 节点, {result['edges']} 边"
                )

            logger.info(
                f"知识图谱整理完成: {batch_count} 批次, {total_nodes} 节点, {total_edges} 边"
            )

            # 3. 发布完成事件
            self.event_bus.publish(
                Event(
                    name="graph_consolidated",
                    data={
                        "batches": batch_count,
                        "nodes": total_nodes,
                        "edges": total_edges,
                    },
                )
            )

            return {"batches": batch_count, "nodes": total_nodes, "edges": total_edges}

        except Exception as e:
            logger.error(f"知识图谱整理失败: {e}")
            return {"batches": 0, "nodes": 0, "edges": 0}

    def _fetch_all_memories(self) -> list:
        """从 episodic_memory 读取未整理的记忆（is_consolidated=0，按时间从早到晚排序）"""
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, insight, importance, time, metadata
                    FROM episodic_memory
                    WHERE is_consolidated = 0
                    ORDER BY time ASC
                    """
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
                            "time": (
                                row[4].isoformat()
                                if hasattr(row[4], "isoformat")
                                else str(row[4])
                            ),
                            "metadata": row[5],
                        }
                    )
                return memories
        except Exception as e:
            logger.error(f"读取记忆失败: {e}")
            if self.conn:
                self.conn.rollback()
            return []

    def _build_summaries(self, memories: list) -> str:
        """将记忆列表构建为摘要文本"""
        summaries = []
        for i, m in enumerate(memories, 1):
            summary = f"[{i}] 时间: {m['time']}\n内容: {m['content']}"
            if m.get("insight"):
                summary += f"\n感想: {m['insight']}"
            if m.get("metadata").get("keywords"):
                summary += f"\n关键词: {json.dumps(m['metadata'], ensure_ascii=False)}"
            summaries.append(summary)
        return "\n\n".join(summaries)

    def _extract_and_store(self, summaries: str) -> dict:
        """从摘要中提取实体和关系并存储到 Neo4j

        Args:
            summaries: 记忆摘要文本

        Returns:
            提取结果统计 {"nodes": int, "edges": int}
        """
        try:
            # 1. 调用 LLM 提取实体和关系
            extraction_result = self._llm_extract(summaries)

            if not extraction_result:
                return {"nodes": 0, "edges": 0}

            # 2. 处理提取结果
            node_count = 0
            edge_count = 0

            for item in extraction_result:
                operation = item.get("operation")

                if operation == "upsert_node":
                    success = self._process_node(item)
                    if success:
                        node_count += 1

                elif operation == "upsert_edge":
                    success = self._process_edge(item)
                    if success:
                        edge_count += 1

            return {"nodes": node_count, "edges": edge_count}

        except Exception as e:
            logger.error(f"实体提取失败: {e}")
            return {"nodes": 0, "edges": 0}

    def _llm_extract(self, summaries: str) -> list:
        """调用 LLM 提取实体和关系

        使用 system_prompt + graph_consolidation_prompt 作为提示词
        """
        system_prompt = settings.SYSTEM_PROMPT
        graph_prompt = settings.GRAPH_CONSOLIDATION_PROMPT

        # 替换变量
        formatted_graph_prompt = graph_prompt.replace("{{summaries}}", summaries)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": formatted_graph_prompt},
        ]

        try:
            response = self.llm_client.one_chat(settings.LARGE_LLM, messages)
            if response is None:
                logger.error("LLM 返回空响应")
                return []

            # 清理响应，移除可能的 markdown 标记
            cleaned = self._clean_json_response(response)
            result = json.loads(cleaned)

            if isinstance(result, list):
                return result
            return []

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            logger.error(f"原始响应前500字符: {response[:500] if response else 'None'}")
            logger.error(
                f"错误位置附近内容: {response[max(0, e.pos-100):e.pos+100] if response and e.pos else 'N/A'}"
            )
            return []
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return []

    def _clean_json_response(self, response: str) -> str:
        """清理 LLM 响应中的 markdown 标记"""
        # 移除 markdown 代码块标记
        cleaned = re.sub(r"^```(?:json)?\s*", "", response.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()

    def _process_node(self, item: dict) -> bool:
        """处理节点操作"""
        try:
            entity_type = item.get("type", "Entity")
            name = item.get("name")
            properties = item.get("properties", {})
            is_increment = item.get("is_increment", True)

            if not name:
                return False

            # 确保 name 在 properties 中
            properties["name"] = name

            eid = self.graph_memory.upsert_entity_with_mode(
                entity_type=entity_type,
                properties=properties,
                is_increment=is_increment,
            )
            return eid is not None

        except Exception as e:
            logger.error(f"节点处理失败: {e}")
            return False

    def _mark_memories_consolidated(self, memory_ids: list):
        """标记记忆为已整理（is_consolidated=1）"""
        if not memory_ids:
            return

        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE episodic_memory
                    SET is_consolidated = 1
                    WHERE id = ANY(%s)
                    """,
                    (memory_ids,),
                )
                self.conn.commit()
                logger.debug(f"已标记 {len(memory_ids)} 条记忆为已整理")
        except Exception as e:
            logger.error(f"标记记忆状态失败: {e}")
            if self.conn:
                self.conn.rollback()

    def _process_edge(self, item: dict) -> bool:
        """处理关系操作"""
        try:
            source = item.get("source")
            target = item.get("target")
            relation = item.get("relation")
            properties = item.get("properties", {})
            is_increment = item.get("is_increment", True)

            if not all([source, target, relation]):
                return False

            rid = self.graph_memory.upsert_edge(
                source=source,
                target=target,
                relation=relation,
                properties=properties,
                is_increment=is_increment,
            )
            return rid is not None

        except Exception as e:
            logger.error(f"关系处理失败: {e}")
            return False
