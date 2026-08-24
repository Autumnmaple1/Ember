"""PostgreSQL-backed entity graph storage.

The graph is represented as an adjacency list: entities are rows in
``knowledge_entities`` and directed edges are rows in ``knowledge_relations``.
This keeps Ember's graph operations in the same PostgreSQL database as the
episodic and dialogue memories.
"""

import json
import logging
import re

from core.event_bus import EventBus
from memory.db_pool import get_connection

logger = logging.getLogger(__name__)


# Description fragments are appended instead of overwritten in incremental mode.
LIST_DESCRIPTION_FIELDS: dict[str, list[str]] = {
    "Person": ["bio"],
    "Location": ["vibe"],
    "Thing": ["utility"],
    "Organization": ["significance"],
}


class PostgresGraphMemory:
    """Store and query Ember's small knowledge graph in PostgreSQL."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.enabled = True
        try:
            self._init_db()
        except Exception as exc:
            self.enabled = False
            logger.error("PostgreSQL graph memory initialization failed: %s", exc)

    def _init_db(self):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_entities (
                        id BIGSERIAL PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        entity_type TEXT NOT NULL DEFAULT 'Entity',
                        aliases TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                        properties JSONB NOT NULL DEFAULT '{}'::JSONB,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_relations (
                        id BIGSERIAL PRIMARY KEY,
                        source_id BIGINT NOT NULL
                            REFERENCES knowledge_entities(id) ON DELETE CASCADE,
                        target_id BIGINT NOT NULL
                            REFERENCES knowledge_entities(id) ON DELETE CASCADE,
                        relation_type TEXT NOT NULL,
                        properties JSONB NOT NULL DEFAULT '{}'::JSONB,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (source_id, target_id, relation_type)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_knowledge_entities_aliases
                    ON knowledge_entities USING GIN (aliases)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source
                    ON knowledge_relations (source_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target
                    ON knowledge_relations (target_id)
                    """
                )
            conn.commit()

    def _is_ready(self) -> bool:
        return self.enabled

    @staticmethod
    def _sanitize_relation(relation: str) -> str:
        """Keep relation labels compact and compatible with existing prompts."""
        if not relation or not isinstance(relation, str):
            return ""
        sanitized = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9_\s]", "", relation)
        return sanitized.strip().replace(" ", "_")

    @staticmethod
    def _is_duplicate_fragment(existing: list, new_frag: str) -> bool:
        def extract_content(fragment: str) -> str:
            parts = fragment.split("|", 2)
            return parts[2].strip() if len(parts) == 3 else fragment.strip()

        new_content = extract_content(new_frag)
        if not new_content:
            return True
        for fragment in existing:
            old_content = extract_content(str(fragment))
            if new_content in old_content or old_content in new_content:
                return True
        return False

    @staticmethod
    def _entity_from_row(row) -> dict:
        entity_id, name, entity_type, aliases, properties = row
        entity = dict(properties or {})
        entity.update(
            {
                "id": entity_id,
                "name": name,
                "entity_type": entity_type,
                "aliases": list(aliases or []),
            }
        )
        return entity

    def _find_matching_entity(self, cur, name: str, aliases: list[str]):
        values = list(dict.fromkeys([name, *aliases]))
        cur.execute(
            """
            SELECT id, name, entity_type, aliases, properties
            FROM knowledge_entities
            WHERE name = ANY(%s)
               OR aliases && %s::TEXT[]
            ORDER BY CASE WHEN name = %s THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (values, values, name),
        )
        return cur.fetchone()

    def upsert_entity_with_mode(
        self, entity_type: str, properties: dict, is_increment: bool = True
    ):
        if not self._is_ready() or not isinstance(properties, dict):
            return None

        incoming = dict(properties)
        name = str(incoming.pop("name", "")).strip()
        if not name:
            return None

        raw_aliases = incoming.pop("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        aliases = [str(alias).strip() for alias in raw_aliases if str(alias).strip()]
        entity_type = str(entity_type or "Entity")

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    matched = self._find_matching_entity(cur, name, aliases)
                    if matched:
                        entity_id, final_name, old_type, old_aliases, old_props = matched
                        merged_aliases = list(
                            dict.fromkeys([*(old_aliases or []), *aliases, name])
                        )
                        merged_aliases = [a for a in merged_aliases if a != final_name]
                        final_props = dict(old_props or {}) if is_increment else {}
                    else:
                        entity_id = None
                        final_name = name
                        old_type = "Entity"
                        merged_aliases = [a for a in dict.fromkeys(aliases) if a != name]
                        final_props = {}

                    list_fields = LIST_DESCRIPTION_FIELDS.get(entity_type, [])
                    for field in list_fields:
                        if field not in incoming:
                            continue
                        value = incoming[field]
                        fragments = value if isinstance(value, list) else [value]
                        fragments = [str(v) for v in fragments if str(v).strip()]
                        if is_increment:
                            existing = final_props.get(field, [])
                            if not isinstance(existing, list):
                                existing = [str(existing)] if existing else []
                            for fragment in fragments:
                                if not self._is_duplicate_fragment(existing, fragment):
                                    existing.append(fragment)
                            incoming[field] = existing
                        else:
                            incoming[field] = fragments

                    final_props.update(incoming)
                    stored_type = entity_type or old_type
                    if entity_id is None:
                        cur.execute(
                            """
                            INSERT INTO knowledge_entities
                                (name, entity_type, aliases, properties)
                            VALUES (%s, %s, %s, %s::JSONB)
                            ON CONFLICT (name) DO UPDATE SET
                                entity_type = EXCLUDED.entity_type,
                                aliases = EXCLUDED.aliases,
                                properties = EXCLUDED.properties,
                                updated_at = NOW()
                            RETURNING id
                            """,
                            (
                                final_name,
                                stored_type,
                                merged_aliases,
                                json.dumps(final_props, ensure_ascii=False),
                            ),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE knowledge_entities
                            SET entity_type = %s,
                                aliases = %s,
                                properties = %s::JSONB,
                                updated_at = NOW()
                            WHERE id = %s
                            RETURNING id
                            """,
                            (
                                stored_type,
                                merged_aliases,
                                json.dumps(final_props, ensure_ascii=False),
                                entity_id,
                            ),
                        )
                    result = cur.fetchone()
                conn.commit()
            return result[0] if result else None
        except Exception as exc:
            logger.error("Failed to upsert graph entity %s: %s", name, exc)
            return None

    def upsert_edge(
        self,
        source: str,
        target: str,
        relation: str,
        properties: dict = None,
        is_increment: bool = True,
    ):
        if not self._is_ready():
            return None
        relation_type = self._sanitize_relation(relation)
        if not relation_type:
            return None

        props = dict(properties or {})
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, name, aliases
                        FROM knowledge_entities
                        WHERE name = ANY(%s) OR aliases && %s::TEXT[]
                        """,
                        ([source, target], [source, target]),
                    )
                    source_id = None
                    target_id = None
                    for entity_id, canonical_name, aliases in cur.fetchall():
                        known_names = {canonical_name, *(aliases or [])}
                        if source in known_names:
                            source_id = entity_id
                        if target in known_names:
                            target_id = entity_id
                    if source_id is None or target_id is None:
                        logger.warning(
                            "Cannot create relation %s: missing source or target", relation_type
                        )
                        return None

                    if is_increment:
                        cur.execute(
                            """
                            INSERT INTO knowledge_relations
                                (source_id, target_id, relation_type, properties)
                            VALUES (%s, %s, %s, %s::JSONB)
                            ON CONFLICT (source_id, target_id, relation_type)
                            DO UPDATE SET
                                properties = knowledge_relations.properties
                                    || EXCLUDED.properties,
                                updated_at = NOW()
                            RETURNING id
                            """,
                            (
                                source_id,
                                target_id,
                                relation_type,
                                json.dumps(props, ensure_ascii=False),
                            ),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO knowledge_relations
                                (source_id, target_id, relation_type, properties)
                            VALUES (%s, %s, %s, %s::JSONB)
                            ON CONFLICT (source_id, target_id, relation_type)
                            DO UPDATE SET
                                properties = EXCLUDED.properties,
                                updated_at = NOW()
                            RETURNING id
                            """,
                            (
                                source_id,
                                target_id,
                                relation_type,
                                json.dumps(props, ensure_ascii=False),
                            ),
                        )
                    result = cur.fetchone()
                conn.commit()
            return result[0] if result else None
        except Exception as exc:
            logger.error("Failed to upsert graph relation: %s", exc)
            return None

    def delete_relationship(self, from_name: str, to_name: str, rel_type: str):
        if not self._is_ready():
            return
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM knowledge_relations AS relation
                    USING knowledge_entities AS source, knowledge_entities AS target
                    WHERE relation.source_id = source.id
                      AND relation.target_id = target.id
                      AND source.name = %s
                      AND target.name = %s
                      AND relation.relation_type = %s
                    """,
                    (from_name, to_name, rel_type),
                )
            conn.commit()

    def delete_entity(self, name: str):
        if not self._is_ready():
            return
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM knowledge_entities WHERE name = %s", (name,))
            conn.commit()

    def query_entities(self, entity_type: str = "Entity", limit: int = 50):
        if not self._is_ready():
            return []
        with get_connection() as conn:
            with conn.cursor() as cur:
                if entity_type == "Entity":
                    cur.execute(
                        """
                        SELECT id, name, entity_type, aliases, properties
                        FROM knowledge_entities ORDER BY id LIMIT %s
                        """,
                        (limit,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, name, entity_type, aliases, properties
                        FROM knowledge_entities
                        WHERE entity_type = %s ORDER BY id LIMIT %s
                        """,
                        (entity_type, limit),
                    )
                return [
                    {"eid": row[0], "props": self._entity_from_row(row)}
                    for row in cur.fetchall()
                ]

    def get_entity_relationships(self, name: str, direction: str = "out"):
        if not self._is_ready():
            return []
        direction = direction if direction in {"out", "in", "both"} else "both"
        clauses = {
            "out": "source.name = %s",
            "in": "target.name = %s",
            "both": "(source.name = %s OR target.name = %s)",
        }
        params = (name, name) if direction == "both" else (name,)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT source.name, target.name, relation.relation_type,
                           relation.properties
                    FROM knowledge_relations AS relation
                    JOIN knowledge_entities AS source ON source.id = relation.source_id
                    JOIN knowledge_entities AS target ON target.id = relation.target_id
                    WHERE {clauses[direction]}
                    ORDER BY relation.id
                    """,
                    params,
                )
                result = []
                for source, target, rel_type, props in cur.fetchall():
                    item_direction = "out" if source == name else "in"
                    related = target if item_direction == "out" else source
                    result.append(
                        {
                            "rel_type": rel_type,
                            "direction": item_direction,
                            "other": related,
                            "properties": props or {},
                        }
                    )
                return result

    def find_path(self, from_name: str, to_name: str, max_depth: int = 3):
        if not self._is_ready():
            return {"nodes": [], "relationships": []}
        max_depth = max(1, min(int(max_depth), 10))
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH RECURSIVE graph_path AS (
                        SELECT e.id AS current_id,
                               ARRAY[e.id] AS visited,
                               ARRAY[e.name] AS names,
                               ARRAY[]::TEXT[] AS relations,
                               0 AS depth
                        FROM knowledge_entities AS e
                        WHERE e.name = %s
                        UNION ALL
                        SELECT CASE WHEN r.source_id = p.current_id
                                    THEN r.target_id ELSE r.source_id END,
                               p.visited || CASE WHEN r.source_id = p.current_id
                                                THEN r.target_id ELSE r.source_id END,
                               p.names || neighbor.name,
                               p.relations || r.relation_type,
                               p.depth + 1
                        FROM graph_path AS p
                        JOIN knowledge_relations AS r
                          ON r.source_id = p.current_id OR r.target_id = p.current_id
                        JOIN knowledge_entities AS neighbor
                          ON neighbor.id = CASE WHEN r.source_id = p.current_id
                                               THEN r.target_id ELSE r.source_id END
                        WHERE p.depth < %s
                          AND NOT neighbor.id = ANY(p.visited)
                    )
                    SELECT p.names, p.relations
                    FROM graph_path AS p
                    JOIN knowledge_entities AS destination
                      ON destination.id = p.current_id
                    WHERE destination.name = %s
                    ORDER BY p.depth
                    LIMIT 1
                    """,
                    (from_name, max_depth, to_name),
                )
                row = cur.fetchone()
                if not row:
                    return {"nodes": [], "relationships": []}
                return {"nodes": row[0], "relationships": row[1]}

    def get_context_for_entity(self, name: str, hops: int = 2):
        if not self._is_ready():
            return []
        hops = max(1, min(int(hops), 5))
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH RECURSIVE neighborhood AS (
                        SELECT id, ARRAY[id] AS visited, 0 AS depth
                        FROM knowledge_entities WHERE name = %s
                        UNION ALL
                        SELECT neighbor.id, n.visited || neighbor.id, n.depth + 1
                        FROM neighborhood AS n
                        JOIN knowledge_relations AS r
                          ON r.source_id = n.id OR r.target_id = n.id
                        JOIN knowledge_entities AS neighbor
                          ON neighbor.id = CASE WHEN r.source_id = n.id
                                               THEN r.target_id ELSE r.source_id END
                        WHERE n.depth < %s
                          AND NOT neighbor.id = ANY(n.visited)
                    )
                    SELECT DISTINCT entity.name, entity.entity_type
                    FROM neighborhood AS n
                    JOIN knowledge_entities AS entity ON entity.id = n.id
                    WHERE n.depth > 0
                    LIMIT 30
                    """,
                    (name, hops),
                )
                return [
                    {"name": row[0], "labels": ["Entity", row[1]]}
                    for row in cur.fetchall()
                ]

    def query_entities_by_names_with_aliases(
        self, names: list, hops: int = 1
    ) -> dict:
        if not self._is_ready() or not names:
            return {"entities": [], "relations": []}

        lookup_names = [str(name) for name in names if str(name).strip()]
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, entity_type, aliases, properties
                    FROM knowledge_entities
                    WHERE name = ANY(%s) OR aliases && %s::TEXT[]
                    ORDER BY id
                    """,
                    (lookup_names, lookup_names),
                )
                matched_rows = cur.fetchall()
                if not matched_rows:
                    return {"entities": [], "relations": []}

                matched = {row[0]: self._entity_from_row(row) for row in matched_rows}
                matched_ids = list(matched)
                cur.execute(
                    """
                    SELECT relation.id,
                           source.id, source.name, source.entity_type,
                           source.aliases, source.properties,
                           target.id, target.name, target.entity_type,
                           target.aliases, target.properties,
                           relation.relation_type, relation.properties
                    FROM knowledge_relations AS relation
                    JOIN knowledge_entities AS source ON source.id = relation.source_id
                    JOIN knowledge_entities AS target ON target.id = relation.target_id
                    WHERE relation.source_id = ANY(%s)
                       OR relation.target_id = ANY(%s)
                    ORDER BY relation.id
                    """,
                    (matched_ids, matched_ids),
                )
                relation_rows = cur.fetchall()

        entities = dict(matched)
        relations = []
        external_counts = {entity_id: 0 for entity_id in matched_ids}
        seen_relations = set()
        for row in relation_rows:
            (
                relation_id,
                source_id,
                source_name,
                source_type,
                source_aliases,
                source_props,
                target_id,
                target_name,
                target_type,
                target_aliases,
                target_props,
                relation_type,
                relation_props,
            ) = row

            is_internal = source_id in matched and target_id in matched
            center_id = source_id if source_id in matched else target_id
            neighbor_id = target_id if center_id == source_id else source_id
            if not is_internal:
                if external_counts[center_id] >= 5:
                    continue
                external_counts[center_id] += 1
                if neighbor_id not in entities:
                    if neighbor_id == source_id:
                        neighbor_row = (
                            source_id,
                            source_name,
                            source_type,
                            source_aliases,
                            source_props,
                        )
                    else:
                        neighbor_row = (
                            target_id,
                            target_name,
                            target_type,
                            target_aliases,
                            target_props,
                        )
                    entities[neighbor_id] = self._entity_from_row(neighbor_row)

            if relation_id not in seen_relations:
                seen_relations.add(relation_id)
                relations.append(
                    {
                        "source": source_name,
                        "relation": relation_type,
                        "target": target_name,
                        "properties": relation_props or {},
                    }
                )

        return {"entities": list(entities.values()), "relations": relations}

    def get_stats(self) -> dict:
        if not self._is_ready():
            return {"entities": 0, "relations": 0}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM knowledge_entities")
                entity_count = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM knowledge_relations")
                relation_count = cur.fetchone()[0]
        return {"entities": entity_count, "relations": relation_count}

    def close(self):
        """Connections are owned by the shared PostgreSQL pool."""
