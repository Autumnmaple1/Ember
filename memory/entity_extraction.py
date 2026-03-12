import json
import logging
import re
from core.event_bus import EventBus, Event
from config.settings import settings
from brain.llm_client import LLMClient
from memory.neo4j_memory import Neo4jGraphMemory

logger = logging.getLogger(__name__)


class EntityExtractionMemory:
    """实体提取与知识图谱构建器

    负责：
    1. 从感官摘要中提取实体和关系
    2. 实体归一化与别名管理
    3. 调用 Neo4j 进行知识图谱存储
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.llm_client = LLMClient()
        self.enabled = settings.ENABLE_NEO4J
        self.graph_memory = None

        if self.enabled:
            self.graph_memory = Neo4jGraphMemory(event_bus)
            self._subscribe_events()

    def _subscribe_events(self):
        """订阅相关事件"""
        self.event_bus.subscribe("sense_summary", self._on_sense_summary)

    async def _on_sense_summary(self, event: Event):
        """处理感官摘要事件"""
        summaries = event.data.get("summaries", "")
        if summaries:
            await self.extract_and_store(summaries)

    async def extract_and_store(self, summaries: str) -> dict:
        """从摘要中提取实体和关系并存储到 Neo4j

        Args:
            summaries: 感官摘要文本

        Returns:
            提取结果统计 {"nodes": int, "edges": int}
        """
        if not self.enabled or not self.graph_memory:
            return {"nodes": 0, "edges": 0}

        try:
            # 1. 调用 LLM 提取实体和关系
            extraction_result = await self._llm_extract(summaries)

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

            logger.info(f"实体提取完成: {node_count} 节点, {edge_count} 边")

            # 3. 发布完成事件
            self.event_bus.publish(
                Event(
                    type="graph_consolidated",
                    data={"nodes": node_count, "edges": edge_count},
                )
            )

            return {"nodes": node_count, "edges": edge_count}

        except Exception as e:
            logger.error(f"实体提取失败: {e}")
            return {"nodes": 0, "edges": 0}

    async def _llm_extract(self, summaries: str) -> list:
        """调用 LLM 提取实体和关系"""
        from config.prompts import prompts

        prompt = prompts.get("graph_consolidation_prompt", "")
        formatted_prompt = prompt.replace("{{summaries}}", summaries)

        try:
            response = await self.llm_client.chat(formatted_prompt)
            # 清理响应，移除可能的 markdown 标记
            cleaned = self._clean_json_response(response)
            result = json.loads(cleaned)

            if isinstance(result, list):
                return result
            return []

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
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
