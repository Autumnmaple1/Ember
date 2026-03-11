import logging
from neo4j import GraphDatabase
from core.event_bus import EventBus, Event
from config.settings import settings

logger = logging.getLogger(__name__)


class Neo4jGraphMemory:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.driver = None
        self.enabled = settings.ENABLE_NEO4J

        if self.enabled:
            self._connect()
            self._ensure_constraints()

    def _connect(self):
        try:
            self.driver = GraphDatabase.driver(
                settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
            )
            logger.info("connected to Neo4j")
        except Exception as e:
            logger.error(f"connection failed{e}")

    def _ensure_constraints(self):
        query = "CREATE CONSTRAINT entity_name_unique IF NOT EXISTS FOR (n:Entity) REQUIRE n.name IS UNIQUE"
        try:
            with self.driver.session(database=settings.NEO4J_DB) as session:
                session.run(query)
        except Exception as e:
            logger.error(f"failed to create constraints: {e}")

    def upsert_entity(self, entity_type: str, properties: dict):
        name = properties.get("name")
        if not name:
            return None

        def _upsert_tx(tx):
            query = f"""
            MERGE (e:Entity {{name: $name}})
            SET e:{entity_type},e += $props
            RETURN elementId(e) AS eid
            """
            result = tx.run(query, name=name, props=properties)
            return result.single()

        with self.driver.session(database=settings.NEO4J_DB) as session:
            record = session.execute_write(_upsert_tx)
            return record["eid"] if record else None

    def create_relationship(
        self, from_name: str, to_name: str, rel_type: str, props: dict = None
    ):
        def _rel_tx(tx):
            query = f"""
            MATCH (a:Entity {{name: $from_name}}),(b:Entity {{name: $to_name}})
            MERGE (a)-[r:{rel_type}]->(b)
            SET r += $props
            RETURN elementId(r) AS rid
            """
            result = tx.run(
                query, from_name=from_name, to_name=to_name, props=props or {}
            )
            return result.single()

        with self.driver.session(database=settings.NEO4J_DB) as session:
            record = session.execute_write(_rel_tx)
            return record["rid"] if record else None

    def delete_relationship(self, from_name: str, to_name: str, rel_type: str):
        def _del_rel_tx(tx):
            query = f"""
            MATCH (a:Entity {{name: $from_name}})-[r:{rel_type}]->(b:Entity {{name: $to_name}})
            DELETE r
            """
            tx.run(query, from_name=from_name, to_name=to_name)

        with self.driver.session(database=settings.NEO4J_DB) as session:
            session.execute_write(_del_rel_tx)

    def delete_entity(self, name: str):
        def _del_entity_tx(tx):
            query = "MATCH (e:Entity {name: $name}) DETACH DELETE e"
            tx.run(query, name=name)

        with self.driver.session(database=settings.NEO4J_DB) as session:
            session.execute_write(_del_entity_tx)

    def query_entities(self, entity_type: str = "Entity"):
        def _query_tx(tx):
            query = f"MATCH (e:{entity_type}) RETURN elementId(e) AS eid, e LIMIT 50"
            result = tx.run(query)
            return [record.data() for record in result]

        with self.driver.session(database=settings.NEO4J_DB) as session:
            return session.execute_read(_query_tx)

    def get_entity_relationships(self, name: str):
        def _rels_tx(tx):
            query = """
            MATCH (e:Entity {name: $name})-[r]->(related)
            RETURN type(r) AS rel_type, elementId(related) AS related_id, related
            """
            result = tx.run(query, name=name)
            return [record.data() for record in result]

        with self.driver.session(database=settings.NEO4J_DB) as session:
            return session.execute_read(_rels_tx)

    def get_relationships_between(self, from_name: str, to_name: str):
        def _rels_between_tx(tx):
            query = """
            MATCH (e:Entity {name: $name})-[r]-(related)
            RETURN type(r) AS rel_type,
                   CASE WHEN startNode(r) = e THEN 'out' ELSE 'in' END AS direction,
                   related.name AS other, related AS properties
            """
            result = tx.run(query, from_name=from_name, to_name=to_name)
            return [record.data() for record in result]

        with self.driver.session(database=settings.NEO4J_DB) as session:
            return session.execute_read(_rels_between_tx)

    def close(self):
        if self.driver:
            self.driver.close()
