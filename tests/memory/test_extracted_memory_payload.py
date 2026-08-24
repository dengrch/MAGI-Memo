from __future__ import annotations

from datetime import datetime, timezone

import pytest

from magi_core.memory import (
    Episode,
    ExtractedAtom,
    ExtractedEntity,
    ExtractedMemory,
    ExtractedRelation,
)
from magi_core.memory.extracted import extracted_payload_to_chunk_results


def test_payload_rejoins_strict_commit_shape_with_real_chunk_source() -> None:
    reference = datetime(2026, 8, 10, tzinfo=timezone.utc)
    memory = ExtractedMemory.create(
        episode=Episode(id="episode-1", content="Alice met Bob.", reference_at=reference),
        entities=(
            ExtractedEntity(
                "Alice",
                (ExtractedAtom("Alice is a person.", valid_at=reference),),
                aliases=("A.",),
            ),
            ExtractedEntity("Bob", (ExtractedAtom("Bob is a person."),)),
        ),
        relations=(
            ExtractedRelation(
                "Alice",
                "Bob",
                (ExtractedAtom("Alice met Bob.", valid_at=reference),),
                keywords=("met",),
            ),
        ),
    )
    nodes, edges = extracted_payload_to_chunk_results(
        memory.to_payload(),
        source_id="chunk-real",
        file_path="episode-1.txt",
    )[0]
    assert nodes["Alice"][0]["source_id"] == "chunk-real"
    assert nodes["Alice"][0]["aliases"] == ["A."]
    assert nodes["Alice"][0]["atom_payload"]["valid_at"] == reference.isoformat()
    assert edges[("Alice", "Bob")][0]["description"] == "Alice met Bob."
    assert edges[("Alice", "Bob")][0]["keywords"] == "met"


def test_relation_only_entities_rejoin_as_endpoint_candidates() -> None:
    memory = ExtractedMemory.create(
        episode=Episode(id="episode-relation-only", content="Alice joined MAGI."),
        entities=(
            ExtractedEntity("Alice", ()),
            ExtractedEntity("MAGI", (), entity_type="organization"),
        ),
        relations=(
            ExtractedRelation(
                "Alice",
                "MAGI",
                (ExtractedAtom("Alice joined MAGI.", predicate="member_of"),),
            ),
        ),
    )

    nodes, edges = extracted_payload_to_chunk_results(
        memory.to_payload(),
        source_id="chunk-relation-only",
        file_path="episode-relation-only.txt",
    )[0]

    assert nodes["Alice"] == [
        {
            "entity_name": "Alice",
            "entity_type": "UNKNOWN",
            "aliases": [],
            "description": "",
            "atom_payload": None,
            "source_id": "chunk-relation-only",
            "file_path": "episode-relation-only.txt",
            "timestamp": nodes["Alice"][0]["timestamp"],
            "magi_endpoint_only": True,
        }
    ]
    assert edges[("Alice", "MAGI")][0]["description"] == "Alice joined MAGI."


def test_missing_relation_endpoint_entities_are_synthesized() -> None:
    memory = ExtractedMemory.create(
        episode=Episode(id="episode-synthesized-endpoints", content="Alice joined MAGI."),
        relations=(
            ExtractedRelation(
                "Alice",
                "MAGI",
                (ExtractedAtom("Alice joined MAGI.", predicate="member_of"),),
            ),
        ),
    )

    assert [(entity.name, entity.atoms) for entity in memory.entities] == [
        ("Alice", ()),
        ("MAGI", ()),
    ]
    nodes, edges = extracted_payload_to_chunk_results(
        memory.to_payload(),
        source_id="chunk-synthesized-endpoints",
        file_path="episode-synthesized-endpoints.txt",
    )[0]
    assert nodes["Alice"][0]["magi_endpoint_only"] is True
    assert nodes["MAGI"][0]["magi_endpoint_only"] is True
    assert edges[("Alice", "MAGI")][0]["description"] == "Alice joined MAGI."


def test_relation_only_entity_without_support_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be endpoints"):
        ExtractedMemory.create(
            episode=Episode(id="episode-orphan", content="Alice was mentioned."),
            entities=(ExtractedEntity("Alice", ()),),
        )


def test_empty_extracted_memory_is_rejected() -> None:
    with pytest.raises(ValueError, match="entity or a relationship"):
        ExtractedMemory.create(
            episode=Episode(id="episode-empty", content="No durable fact."),
        )
