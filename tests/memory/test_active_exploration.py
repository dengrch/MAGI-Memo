from __future__ import annotations

from dataclasses import dataclass

import pytest

from magi_core.memory.exploration import ExplorationReadError, MemoryExplorer


@dataclass
class _Entity:
    id: str
    canonical_name: str


@dataclass
class _Relation:
    id: str
    entity_a_name: str
    entity_b_name: str


class _Graph:
    def __init__(self) -> None:
        self.nodes = {
            "Alice": {
                "entity_id": "Alice",
                "magi_entity_id": "entity-alice",
                "entity_type": "PERSON",
                "description": "Alice description",
            },
            "Bob": {
                "entity_id": "Bob",
                "magi_entity_id": "entity-bob",
                "entity_type": "PERSON",
                "description": "Bob description",
            },
            "Carol": {
                "entity_id": "Carol",
                "magi_entity_id": "entity-carol",
                "entity_type": "PERSON",
                "description": "Carol description",
            },
        }
        self.edges = {
            ("Alice", "Bob"): {
                "magi_relation_id": "relation-alice-bob",
                "keywords": "works with",
                "description": "Alice works with Bob",
            },
            ("Alice", "Carol"): {
                "magi_relation_id": "relation-alice-carol",
                "keywords": "knows",
                "description": "Alice knows Carol",
            },
        }
        self.failed_frontier: str | None = None

    async def get_node(self, name: str):
        return self.nodes.get(name)

    async def get_edge(self, source: str, target: str):
        return self.edges.get(tuple(sorted((source, target))))

    async def get_node_edges(self, name: str):
        if name == self.failed_frontier:
            return None
        if name not in self.nodes:
            return None
        return [pair for pair in self.edges if name in pair]


class _MemoryDb:
    workspace_id = "test"

    def __init__(self) -> None:
        self.entities = {
            "entity-alice": _Entity("entity-alice", "Alice"),
            "entity-bob": _Entity("entity-bob", "Bob"),
            "entity-carol": _Entity("entity-carol", "Carol"),
        }
        self.relations = {
            "relation-alice-bob": _Relation(
                "relation-alice-bob", "Alice", "Bob"
            ),
            "relation-alice-carol": _Relation(
                "relation-alice-carol", "Alice", "Carol"
            ),
        }
        self.owner_atoms: dict[str, list[object]] = {}

    async def get_entity(self, entity_id: str):
        return self.entities.get(entity_id)

    async def get_relation(self, relation_id: str):
        return self.relations.get(relation_id)

    async def list_owner_atoms(self, owner_id: str):
        return self.owner_atoms.get(owner_id, [])

    async def get_atom_memory_view(self, atom_id: str):
        return None

    async def list_atom_evolutions(self, atom_id: str):
        return []


@pytest.mark.asyncio
async def test_expand_filters_only_when_entity_and_relation_are_both_visited():
    explorer = MemoryExplorer(_Graph(), _MemoryDb())

    result = await explorer.expand(
        frontier=["Alice"],
        visit_entities=["Alice", "Bob", "Carol"],
        visit_relations=[("Bob", "Alice")],
        max_candidates_per_frontier=50,
    )

    assert result["status"] == "complete"
    assert result["exhausted"] is False
    candidates = result["results"][0]["candidates"]
    assert [candidate["entity"]["name"] for candidate in candidates] == ["Carol"]
    assert candidates[0]["relation"]["endpoints"] == ["Alice", "Carol"]
    assert candidates[0] == {
        "entity": {"name": "Carol", "entity_type": "PERSON"},
        "relation": {"endpoints": ["Alice", "Carol"], "keywords": "knows"},
    }


@pytest.mark.asyncio
async def test_expand_reports_true_exhaustion_only_after_complete_deduplication():
    explorer = MemoryExplorer(_Graph(), _MemoryDb())

    result = await explorer.expand(
        frontier=["Alice"],
        visit_entities=["Alice", "Bob", "Carol"],
        visit_relations=[("Alice", "Bob"), ("Carol", "Alice")],
        max_candidates_per_frontier=50,
    )

    assert result["status"] == "complete"
    assert result["exhausted"] is True
    assert result["available_count"] == 0


@pytest.mark.asyncio
async def test_missing_or_truncated_frontier_is_never_exhaustion():
    explorer = MemoryExplorer(_Graph(), _MemoryDb())

    missing = await explorer.expand(
        frontier=["Missing"],
        visit_entities=[],
        visit_relations=[],
        max_candidates_per_frontier=50,
    )
    assert missing["status"] == "partial"
    assert missing["missing_frontier"] == ["Missing"]
    assert missing["exhausted"] is False

    truncated = await explorer.expand(
        frontier=["Alice"],
        visit_entities=[],
        visit_relations=[],
        max_candidates_per_frontier=1,
    )
    assert truncated["status"] == "truncated"
    assert truncated["truncated"] is True
    assert truncated["available_count"] == 2
    assert truncated["returned_count"] == 1
    assert truncated["exhausted"] is False


@pytest.mark.asyncio
async def test_graph_read_failure_is_not_returned_as_an_empty_expansion():
    graph = _Graph()
    graph.failed_frontier = "Alice"
    explorer = MemoryExplorer(graph, _MemoryDb())

    with pytest.raises(ExplorationReadError, match="one-hop expansion failed"):
        await explorer.expand(
            frontier=["Alice"],
            visit_entities=[],
            visit_relations=[],
            max_candidates_per_frontier=50,
        )


@pytest.mark.asyncio
async def test_evidence_resolves_entity_and_unordered_relation_by_name():
    explorer = MemoryExplorer(_Graph(), _MemoryDb())

    result = await explorer.evidence(
        [
            {"entity": "Alice"},
            {"relation": ("Bob", "Alice")},
            {"entity": "Missing"},
        ]
    )

    assert result["status"] == "partial"
    assert result["missing_owners"] == [{"entity": "Missing"}]
    assert result["owners"][0]["owner"] == {"type": "entity", "name": "Alice"}
    assert result["owners"][1]["owner"] == {
        "type": "relation",
        "name": ["Alice", "Bob"],
    }


@pytest.mark.asyncio
async def test_describe_progressively_loads_graph_descriptions_by_name():
    explorer = MemoryExplorer(_Graph(), _MemoryDb())

    result = await explorer.describe(
        [
            {"entity": "Alice"},
            {"relation": ("Bob", "Alice")},
            {"entity": "Missing"},
        ]
    )

    assert result == {
        "status": "partial",
        "missing_owners": [{"entity": "Missing"}],
        "owners": [
            {
                "owner": {"type": "entity", "name": "Alice"},
                "entity_type": "PERSON",
                "description": "Alice description",
            },
            {
                "owner": {
                    "type": "relation",
                    "name": ["Alice", "Bob"],
                },
                "keywords": "works with",
                "description": "Alice works with Bob",
            },
        ],
    }
