import logging
import re
from neo4j import GraphDatabase
from core.event_bus import EventBus, Event
from config.settings import settings

logger = logging.getLogger(__name__)

# 描述型字段以 List<String> 存储，每条格式为 "类别|时间|内容"
LIST_DESCRIPTION_FIELDS: dict[str, list[str]] = {
    "Person": ["bio"],
    "Location": ["vibe"],
    "Thing": ["utility"],
    "Organization": ["significance"],
}


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

    def _sanitize_relation(self, relation: str) -> str:
        """净化关系类型，只允许中文、英文、数字和下划线"""
        if not relation or not isinstance(relation, str):
            return ""
        # 保留中文、英文、数字、下划线和空格（空格转为下划线）
        sanitized = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9_\s]', '', relation)
        sanitized = sanitized.strip().replace(' ', '_')
        return sanitized if sanitized else ""

    def upsert_entity_with_mode(
        self, entity_type: str, properties: dict, is_increment: bool = True
    ):
        """支持增量/覆盖模式的实体更新，自动处理别名匹配

        Args:
            entity_type: 实体类型 (Person, Location, Thing, Concept, Event, Organization)
            properties: 实体属性，必须包含 name 字段
            is_increment: True=增量更新(列表合并去重), False=覆盖更新
        """
        if not self._is_ready() or "name" not in properties:
            return None

        safe_type = self._safe_label(entity_type)
        name = properties["name"]
        new_aliases = properties.get("aliases", [])
        if isinstance(new_aliases, str):
            new_aliases = [new_aliases]

        # 查找匹配的已存在实体
        matched_name = self._find_entity_by_name_or_alias(name, new_aliases)

        # 如果找到匹配实体，使用其名称
        final_name = matched_name if matched_name else name
        properties["name"] = final_name

        # 合并别名
        if matched_name:
            existing_aliases = self._get_entity_aliases(matched_name)
            merged_aliases = list(set(existing_aliases + new_aliases + [name]))
            properties["aliases"] = merged_aliases

        # 处理 List<String> 描述型字段
        list_fields = LIST_DESCRIPTION_FIELDS.get(entity_type, [])
        for field in list_fields:
            if field not in properties:
                continue
            new_value = properties[field]
            if is_increment:
                if isinstance(new_value, str) and new_value:
                    existing = self._get_entity_field_values(final_name, field)
                    if not self._is_duplicate_fragment(existing, new_value):
                        properties[field] = existing + [new_value]
                    else:
                        del properties[field]  # 与已有碎片重复，跳过写入
                elif isinstance(new_value, list):
                    existing = self._get_entity_field_values(final_name, field)
                    merged = list(existing)
                    for frag in new_value:
                        if isinstance(frag, str) and frag and not self._is_duplicate_fragment(merged, frag):
                            merged.append(frag)
                    properties[field] = merged
            else:
                # 覆盖模式：统一规范化为列表
                if isinstance(new_value, str):
                    properties[field] = [new_value]
                # isinstance list：保持原样

        def _upsert_tx(tx):
            if is_increment:
                # 增量更新：列表属性合并去重，其他属性覆盖
                query = f"""
                MERGE (e:Entity {{name: $name}})
                SET e:{safe_type}
                WITH e
                SET e += $props
                RETURN elementId(e) AS eid
                """
                result = tx.run(query, name=final_name, props=properties)
            else:
                # 覆盖更新：完全替换
                query = f"""
                MERGE (e:Entity {{name: $name}})
                SET e:{safe_type}, e = $props
                RETURN elementId(e) AS eid
                """
                result = tx.run(query, name=final_name, props=properties)

            record = result.single()
            return record["eid"] if record else None

        with self.driver.session(database=self.db_name) as session:
            return session.execute_write(_upsert_tx)

    def _find_entity_by_name_or_alias(self, name: str, aliases: list = None) -> str:
        """通过名称或别名查找已存在的实体

        检查：
        1. name 是否等于某个已存在实体的 name
        2. name 是否在某个已存在实体的 aliases 中
        3. aliases 中的值是否等于某个已存在实体的 name
        4. aliases 中的值是否在某个已存在实体的 aliases 中

        Returns:
            匹配到的实体名称，无匹配返回 None
        """
        aliases = aliases or []

        def _find_tx(tx):
            # 构建所有待检查的值
            all_values = [name] + aliases

            # 查询：name 匹配 或 aliases 包含任一值
            query = """
            MATCH (e:Entity)
            WHERE e.name IN $values 
               OR (e.aliases IS NOT NULL AND ANY(alias IN e.aliases WHERE alias IN $values))
            RETURN e.name AS matched_name
            LIMIT 1
            """
            result = tx.run(query, values=all_values)
            record = result.single()
            return record["matched_name"] if record else None

        with self.driver.session(database=self.db_name) as session:
            return session.execute_read(_find_tx)

    def _get_entity_aliases(self, name: str) -> list:
        """获取实体的别名列表"""

        def _get_tx(tx):
            query = """
            MATCH (e:Entity {name: $name})
            RETURN e.aliases AS aliases
            """
            result = tx.run(query, name=name)
            record = result.single()
            if record and record["aliases"]:
                return list(record["aliases"])
            return []

        with self.driver.session(database=self.db_name) as session:
            return session.execute_read(_get_tx)

    def _get_entity_field_values(self, name: str, field: str) -> list:
        """读取实体某列表型描述字段的当前值；若字段为旧格式 String 则自动迁移为列表"""

        def _tx(tx):
            result = tx.run(
                f"MATCH (e:Entity {{name: $name}}) RETURN e.{field} AS val",
                name=name,
            )
            record = result.single()
            if record and record["val"] is not None:
                val = record["val"]
                if isinstance(val, (list, tuple)):
                    return list(val)
                return [str(val)]  # 旧 String 值 → 迁移为单元素列表
            return []

        with self.driver.session(database=self.db_name) as session:
            return session.execute_read(_tx)

    def _is_duplicate_fragment(self, existing: list, new_frag: str) -> bool:
        """判断新碎片是否与已有碎片语义重复（内容部分的字符串包含检测）

        碎片格式：类别|时间|内容（取第3段作为内容；格式不符时整体作为内容）
        """

        def extract_content(frag: str) -> str:
            parts = frag.split("|", 2)
            return parts[2].strip() if len(parts) == 3 else frag.strip()

        new_content = extract_content(new_frag)
        if not new_content:
            return True  # 空碎片视为重复

        for frag in existing:
            ec = extract_content(frag)
            if new_content in ec or ec in new_content:
                return True
        return False

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
            relation: 关系类型（支持中文）
            properties: 关系属性 (如 strength)
            is_increment: True=增量更新, False=覆盖更新
        """
        if not self._is_ready():
            return None

        if not relation:
            logger.warning("关系类型为空")
            return None

        props = properties or {}

        def _rel_tx(tx):
            # 净化关系类型，防止注入
            safe_relation = self._sanitize_relation(relation)
            if not safe_relation:
                logger.warning(f"关系类型无效: {relation}")
                return None

            # 使用反引号包裹关系类型，支持中文
            query = f"""
            MATCH (a:Entity {{name: $source}}), (b:Entity {{name: $target}})
            MERGE (a)-[r:`{safe_relation}`]->(b)
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
        if not rel_type:
            return

        def _del_rel_tx(tx):
            # 使用反引号包裹关系类型，支持中文
            query = f"MATCH (a:Entity {{name: $from_name}})-[r:`{rel_type}`]->(b:Entity {{name: $to_name}}) DELETE r"
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

    def query_entities_by_names_with_aliases(self, names: list, hops: int = 1) -> dict:
        """通过名称列表查询实体及其关系，支持别名匹配

        Args:
            names: 实体名称列表（可能是标准名或别名）
            hops: 关系查询深度，默认1跳

        Returns:
            {
                "entities": [{"name": "...", "bio": "...", ...}],
                "relations": [{"source": "...", "target": "...", "relation": "...", ...}]
            }
        """
        if not self._is_ready() or not names:
            return {"entities": [], "relations": []}

        def _query_tx(tx):
            # 第一步：通过名称或别名匹配找到所有相关实体
            match_query = """
            MATCH (e:Entity)
            WHERE e.name IN $names 
               OR (e.aliases IS NOT NULL AND ANY(alias IN e.aliases WHERE alias IN $names))
            RETURN e
            """
            entities_result = tx.run(match_query, names=names)
            entities = []
            entity_names = []

            for record in entities_result:
                e = dict(record["e"])
                entities.append(e)
                entity_names.append(e.get("name"))

            if not entity_names:
                return {"entities": [], "relations": []}

            # 第二步：查询这些实体之间的关系
            rel_query = f"""
            MATCH (a:Entity)-[r]-(b:Entity)
            WHERE a.name IN $entity_names AND b.name IN $entity_names
            RETURN a.name AS source, type(r) AS relation, b.name AS target, properties(r) AS props
            """
            rels_result = tx.run(rel_query, entity_names=entity_names)
            relations = []
            for record in rels_result:
                relations.append(
                    {
                        "source": record["source"],
                        "relation": record["relation"],
                        "target": record["target"],
                        "properties": record["props"] or {},
                    }
                )

            neighbor_query = """
            UNWIND $entity_names AS center_name
            CALL {
              WITH center_name
              MATCH (center:Entity {name: center_name})-[r]-(neighbor:Entity)
              WHERE NOT neighbor.name IN $entity_names
              WITH type(r) AS relation, neighbor, properties(r) AS rel_props
              RETURN relation, neighbor, rel_props
              ORDER BY rand()
              LIMIT 5
            }
            RETURN center_name, relation, neighbor.name AS neighbor_name, 
                   properties(neighbor) AS neighbor_props, rel_props
            """
            neighbor_result = tx.run(neighbor_query, entity_names=entity_names)

            neighbor_entities = {}
            for record in neighbor_result:
                n_name = record["neighbor_name"]
                if n_name not in neighbor_entities:
                    neighbor_entities[n_name] = record["neighbor_props"] or {}
                    neighbor_entities[n_name]["name"] = n_name

                relations.append(
                    {
                        "source": record["center_name"],
                        "relation": record["relation"],
                        "target": n_name,
                        "properties": record["rel_props"] or {},
                    }
                )

            # 合并实体列表
            all_entities = entities + list(neighbor_entities.values())

            return {"entities": all_entities, "relations": relations}

        with self.driver.session(database=self.db_name) as session:
            return session.execute_read(_query_tx)

    def close(self):
        if self.driver:
            self.driver.close()
            logger.info("Neo4j connection closed")
