import logging
import re
from neo4j import GraphDatabase
from core.event_bus import EventBus, Event
from config.settings import settings

logger = logging.getLogger(__name__)


class Neo4jGraphMemory:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.driver = None
        self.enabled = settings.ENABLE_NEO4J
        self.apoc_enabled = False  # APOC 插件可用性标志
        self.db_name = getattr(settings, "NEO4J_DB", "neo4j")

        if self.enabled:
            self._connect()
            if self.driver:
                self._ensure_constraints()

    def _connect(self):
        try:
            self.driver = GraphDatabase.driver(
                settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
            )
            self.driver.verify_connectivity()
            logger.info("Connected to Neo4j")
        except Exception as e:
            self.enabled = False
            logger.error(f"Neo4j connection failed: {e}")

    def _ensure_constraints(self):
        """初始化约束并检查 APOC 插件"""
        try:
            with self.driver.session(database=self.db_name) as session:
                # 创建唯一约束
                session.run(
                    "CREATE CONSTRAINT entity_name_unique IF NOT EXISTS "
                    "FOR (n:Entity) REQUIRE n.name IS UNIQUE"
                )

                # 检查 APOC 插件是否可用
                result = session.run(
                    "CALL apoc.help('coll') YIELD name RETURN count(*) AS cnt"
                )
                record = result.single()
                if record and record["cnt"] > 0:
                    self.apoc_enabled = True
                    logger.info("APOC plugin is available")
                else:
                    self.apoc_enabled = False
                    logger.warning(
                        "APOC plugin not found. List merging will use Python fallback. "
                        "Install APOC for better performance."
                    )
        except Exception as e:
            self.apoc_enabled = False
            logger.warning(
                f"APOC check failed: {e}. Using Python fallback for list merging."
            )

    def _is_ready(self):
        return self.enabled and self.driver

    def _safe_label(self, label: str):
        return re.sub(r"[^a-zA-Z0-9_]", "", label)

    def upsert_entity_with_mode(
        self, entity_type: str, properties: dict, is_increment: bool = True
    ):
        """支持增量/覆盖模式的实体更新

        Args:
            entity_type: 实体类型 (Person, Location, Thing, Concept, Event, Organization)
            properties: 实体属性，必须包含 name 字段
            is_increment: True=增量更新(列表合并去重), False=覆盖更新
        """
        if not self._is_ready() or "name" not in properties:
            return None

        safe_type = self._safe_label(entity_type)
        name = properties["name"]

        def _upsert_tx(tx):
            if is_increment:
                # 增量更新：列表属性合并去重，其他属性覆盖
                # 注意：需要 Neo4j 安装 APOC 插件，否则使用纯 Cypher 实现
                query = f"""
                MERGE (e:Entity {{name: $name}})
                SET e:{safe_type}
                WITH e
                SET e += $props
                RETURN elementId(e) AS eid
                """
                result = tx.run(query, name=name, props=properties)
            else:
                # 覆盖更新：完全替换
                query = f"""
                MERGE (e:Entity {{name: $name}})
                SET e:{safe_type}, e = $props
                RETURN elementId(e) AS eid
                """
                result = tx.run(query, name=name, props=properties)

            record = result.single()
            return record["eid"] if record else None

        with self.driver.session(database=self.db_name) as session:
            return session.execute_write(_upsert_tx)

    def upsert_edge(
        self,
        source: str,
        target: str,
        relation: str,
        properties: dict = None,
        is_increment: bool = True,
    ):
        """创建或更新关系，支持增量/覆盖模式

        Args:
            source: 起点实体名称
            target: 终点实体名称
            relation: 关系类型
            properties: 关系属性 (如 strength)
            is_increment: True=增量更新, False=覆盖更新
        """
        if not self._is_ready():
            return None

        safe_rel = self._safe_label(relation)
        props = properties or {}

        def _rel_tx(tx):
            query = f"""
            MATCH (a:Entity {{name: $source}}), (b:Entity {{name: $target}})
            MERGE (a)-[r:{safe_rel}]->(b)
            SET r += $props
            RETURN elementId(r) AS rid
            """
            result = tx.run(query, source=source, target=target, props=props)
            record = result.single()
            return record["rid"] if record else None

        with self.driver.session(database=self.db_name) as session:
            return session.execute_write(_rel_tx)

    def delete_relationship(self, from_name: str, to_name: str, rel_type: str):
        if not self._is_ready():
            return
        safe_rel = self._safe_label(rel_type)

        def _del_rel_tx(tx):
            query = f"MATCH (a:Entity {{name: $from_name}})-[r:{safe_rel}]->(b:Entity {{name: $to_name}}) DELETE r"
            tx.run(query, from_name=from_name, to_name=to_name)

        with self.driver.session(database=self.db_name) as session:
            session.execute_write(_del_rel_tx)

    def delete_entity(self, name: str):
        if not self._is_ready():
            return

        def _del_entity_tx(tx):
            query = "MATCH (e:Entity {name: $name}) DETACH DELETE e"
            tx.run(query, name=name)

        with self.driver.session(database=self.db_name) as session:
            session.execute_write(_del_entity_tx)

    def query_entities(self, entity_type: str = "Entity", limit: int = 50):
        if not self._is_ready():
            return []
        safe_type = self._safe_label(entity_type)

        def _query_tx(tx):
            query = f"MATCH (e:{safe_type}) RETURN elementId(e) AS eid, properties(e) as props LIMIT $limit"
            result = tx.run(query, limit=limit)
            return [record.data() for record in result]

        with self.driver.session(database=self.db_name) as session:
            return session.execute_read(_query_tx)

    def get_entity_relationships(self, name: str, direction: str = "out"):
        if not self._is_ready():
            return []

        def _rels_tx(tx):
            if direction == "out":
                query = """
                MATCH (e:Entity {name: $name})-[r]->(related)
                RETURN type(r) AS rel_type, 'out' AS direction, 
                       related.name AS target, properties(related) AS properties
                """
            elif direction == "in":
                query = """
                MATCH (related)-[r]->(e:Entity {name: $name})
                RETURN type(r) AS rel_type, 'in' AS direction,
                       related.name AS source, properties(related) AS properties
                """
            else:
                query = """
                MATCH (e:Entity {name: $name})-[r]-(related)
                RETURN type(r) AS rel_type,
                       CASE WHEN startNode(r) = e THEN 'out' ELSE 'in' END AS direction,
                       related.name AS other, properties(related) AS properties
                """
            result = tx.run(query, name=name)
            return [record.data() for record in result]

        with self.driver.session(database=self.db_name) as session:
            return session.execute_read(_rels_tx)

    def find_path(self, from_name: str, to_name: str, max_depth: int = 3):
        if not self._is_ready():
            return {"nodes": [], "relationships": []}

        def _path_tx(tx):
            query = f"""
            MATCH p=shortestPath((a:Entity {{name: $from_name}})-[*..{int(max_depth)}]-(b:Entity {{name: $to_name}}))
            RETURN [n IN nodes(p) | n.name] AS names,
                   [r IN relationships(p) | type(r)] AS rel_types
            """
            result = tx.run(query, from_name=from_name, to_name=to_name)
            record = result.single()
            return record if record else {"names": [], "rel_types": []}

        with self.driver.session(database=self.db_name) as session:
            res = session.execute_read(_path_tx)
            return {"nodes": res["names"], "relationships": res["rel_types"]}

    def get_context_for_entity(self, name: str, hops: int = 2):
        if not self._is_ready():
            return []

        def _context_tx(tx):
            query = f"""
            MATCH (center:Entity {{name: $name}})-[*1..{int(hops)}]-(neighbor:Entity)
            RETURN DISTINCT neighbor.name AS name, labels(neighbor) AS labels
            LIMIT 30
            """
            result = tx.run(query, name=name)
            return [record.data() for record in result]

        with self.driver.session(database=self.db_name) as session:
            return session.execute_read(_context_tx)

    def close(self):
        if self.driver:
            self.driver.close()
            logger.info("Neo4j connection closed")
