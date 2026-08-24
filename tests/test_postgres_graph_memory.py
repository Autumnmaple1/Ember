"""Unit tests for PostgreSQL graph-memory helpers that do not need a database."""

from memory.postgres_graph_memory import PostgresGraphMemory


def test_relation_label_is_sanitized():
    assert PostgresGraphMemory._sanitize_relation(" 就读 于! ") == "就读_于"
    assert PostgresGraphMemory._sanitize_relation("") == ""


def test_description_fragment_deduplication_uses_content():
    existing = ["经历|2026-01-01|第一次见到用户"]

    assert PostgresGraphMemory._is_duplicate_fragment(
        existing, "回忆|2026-02-01|第一次见到用户"
    )
    assert not PostgresGraphMemory._is_duplicate_fragment(
        existing, "回忆|2026-02-01|一起去了图书馆"
    )


def test_entity_row_is_flattened_for_prompt_consumers():
    row = (
        7,
        "南京大学",
        "Location",
        ["南大"],
        {"vibe": ["地点|2026-01-01|校园"]},
    )

    entity = PostgresGraphMemory._entity_from_row(row)

    assert entity["id"] == 7
    assert entity["name"] == "南京大学"
    assert entity["entity_type"] == "Location"
    assert entity["aliases"] == ["南大"]
    assert entity["vibe"] == ["地点|2026-01-01|校园"]
