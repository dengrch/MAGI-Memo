from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from magi_core.backend.sqlite import SQLiteBackend
from magi_core.ingestion import KnowledgeCommitContext
from magi_core.memory import (
    AtomClassification,
    AtomDecision,
    AtomEvidence,
    AtomRecord,
    EntityRecord,
    EntityResolution,
    Episode,
)
from magi_core.memory.adapter import MagiKnowledgeAdapter


class _Embedding:
    embedding_dim = 3
    model_name = "in-batch-test-3d"

    async def __call__(self, texts: list[str], **kwargs: Any) -> np.ndarray:
        return np.asarray(
            [
                [
                    float(len(text)),
                    float(sum(ord(char) for char in text) % 101),
                    1.0,
                ]
                for text in texts
            ],
            dtype=np.float32,
        )


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str], dict[str, Any]] = {}

    async def get_all_labels(self) -> list[str]:
        return list(self.nodes)

    async def get_node(self, name: str) -> dict[str, Any] | None:
        return self.nodes.get(name)

    async def get_edge(self, source: str, target: str) -> dict[str, Any] | None:
        return self.edges.get(tuple(sorted((source, target))))

    async def upsert_node(self, name: str, node_data: dict[str, Any]) -> None:
        self.nodes[name] = dict(node_data)

    async def upsert_edge(
        self, source: str, target: str, edge_data: dict[str, Any]
    ) -> None:
        self.edges[tuple(sorted((source, target)))] = dict(edge_data)


class _Engine:
    def __init__(self) -> None:
        self.embedding_func = _Embedding()
        self.chunk_entity_relation_graph = _Graph()
        self.entities_vdb = object()
        self.relationships_vdb = object()
        self.llm_response_cache = object()
        self.entity_chunks = object()
        self.relation_chunks = object()

    def _build_global_config(self) -> dict[str, Any]:
        return {"workspace": "test"}


class _ScriptedDecisions:
    def __init__(
        self,
        *,
        in_batch_decision: AtomDecision = AtomDecision.INDEPENDENT,
        raise_in_batch: bool = False,
        invalid_history_target: str | None = None,
    ) -> None:
        self.in_batch_decision = in_batch_decision
        self.raise_in_batch = raise_in_batch
        self.invalid_history_target = invalid_history_target
        self.atom_calls: list[list[Any]] = []

    async def resolve_entities(self, **kwargs: Any) -> dict[str, EntityResolution]:
        resolutions = {}
        for request in kwargs.get("requests") or []:
            if request.candidates:
                candidate = request.candidates[0]
                resolutions[request.key] = EntityResolution(
                    candidate.object_id,
                    candidate.text.splitlines()[0],
                    1.0,
                    "reuse test entity",
                )
            else:
                resolutions[request.key] = EntityResolution(
                    None,
                    request.name,
                    1.0,
                    "new test entity",
                )
        return resolutions

    async def resolve_atoms(self, **kwargs: Any) -> dict[str, AtomClassification]:
        requests = list(kwargs.get("requests") or [])
        self.atom_calls.append(requests)
        is_in_batch = any(
            candidate.match_kind.startswith("in_batch_")
            for request in requests
            for candidate in request.candidates
        )
        if is_in_batch and self.raise_in_batch:
            raise RuntimeError("simulated in-batch model failure")

        resolutions = {}
        for request in requests:
            in_batch_candidates = [
                candidate
                for candidate in request.candidates
                if candidate.match_kind.startswith("in_batch_")
            ]
            if in_batch_candidates:
                resolutions[request.atom.id] = AtomClassification(
                    decision=self.in_batch_decision,
                    matched_atom_id=(
                        in_batch_candidates[0].object_id
                        if self.in_batch_decision is not AtomDecision.INDEPENDENT
                        else None
                    ),
                    confidence=1.0,
                    reason="scripted in-batch decision",
                )
            elif self.invalid_history_target:
                resolutions[request.atom.id] = AtomClassification(
                    decision=AtomDecision.REFINEMENT,
                    matched_atom_id=self.invalid_history_target,
                    confidence=1.0,
                    reason="simulated illegal history target",
                )
            else:
                resolutions[request.atom.id] = AtomClassification(
                    decision=AtomDecision.INDEPENDENT,
                    confidence=1.0,
                    reason="scripted independent history decision",
                )
        return resolutions


def _record(
    name: str,
    content: str,
    chunk_id: str,
    *,
    valid_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "entity_name": name,
        "entity_type": "person",
        "aliases": [],
        "description": content,
        "atom_payload": {
            "valid_at": valid_at.isoformat() if valid_at else None,
        },
        "source_id": chunk_id,
        "file_path": "episode.txt",
        "timestamp": 1,
    }


async def _commit_chunks(
    *,
    backend: SQLiteBackend,
    adapter: MagiKnowledgeAdapter,
    engine: _Engine,
    episode: Episode,
    chunks: list[tuple[dict[str, list[dict[str, Any]]], dict]],
) -> None:
    await backend.put_episode(episode)
    with patch(
        "magi_core.memory.adapter.merge_nodes_and_edges",
        new=AsyncMock(),
    ):
        await adapter.commit(
            chunks,
            KnowledgeCommitContext(
                rag=engine,
                doc_id=episode.id,
                file_path="episode.txt",
            ),
        )


@pytest.mark.asyncio
async def test_cross_chunk_duplicates_keep_one_atom_and_every_chunk_evidence(
    tmp_path: Path,
) -> None:
    backend = SQLiteBackend(tmp_path / "memory.sqlite3", "workspace-a")
    await backend.initialize()
    decisions = _ScriptedDecisions(in_batch_decision=AtomDecision.DUPLICATE)
    adapter = MagiKnowledgeAdapter(backend, decision_provider=decisions)
    engine = _Engine()
    reference = datetime(2026, 8, 5, tzinfo=timezone.utc)

    await _commit_chunks(
        backend=backend,
        adapter=adapter,
        engine=engine,
        episode=Episode(
            id="episode-batch-duplicate",
            content="Alice likes green tea. Alice enjoys green tea.",
            reference_at=reference,
        ),
        chunks=[
            ({"Alice": [_record("Alice", "Alice likes green tea.", "chunk-a")]}, {}),
            (
                {
                    "Alice": [
                        _record("Alice", "  Alice likes   green tea. ", "chunk-b")
                    ]
                },
                {},
            ),
            (
                {"Alice": [_record("Alice", "Alice enjoys green tea.", "chunk-c")]},
                {},
            ),
        ],
    )

    alice = await backend.find_entity_exact("Alice")
    atoms = await backend.list_owner_atoms(alice.id)
    assert len(atoms) == 1
    assert atoms[0].normalized_content == "Alice likes green tea."
    memory = await backend.get_atom_memory(atoms[0].id)
    assert memory is not None
    assert len(memory.evidence) == 3
    assert {item.extraction_revision for item in memory.evidence} == {
        "chunk-a",
        "chunk-b",
        "chunk-c",
    }
    in_batch_requests = [
        request
        for call in decisions.atom_calls
        for request in call
        if any(
            candidate.match_kind.startswith("in_batch_")
            for candidate in request.candidates
        )
    ]
    assert len(in_batch_requests) == 1
    assert in_batch_requests[0].atom.content == "Alice enjoys green tea."
    await backend.finalize()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_relation", "expected_expired"),
    [
        (AtomDecision.REFINEMENT, "REFINEMENT", True),
        (AtomDecision.TEMPORAL_SUCCESSOR, "TEMPORAL_SUCCESSOR", True),
        (AtomDecision.CONTRADICTION, "CONTRADICTION", False),
        (AtomDecision.INDEPENDENT, None, False),
    ],
)
async def test_cross_chunk_semantic_decisions_apply_existing_five_class_semantics(
    tmp_path: Path,
    decision: AtomDecision,
    expected_relation: str | None,
    expected_expired: bool,
) -> None:
    backend = SQLiteBackend(tmp_path / decision.value / "memory.sqlite3", "workspace-a")
    await backend.initialize()
    decisions = _ScriptedDecisions(in_batch_decision=decision)
    adapter = MagiKnowledgeAdapter(backend, decision_provider=decisions)
    engine = _Engine()
    reference = datetime(2026, 8, 5, tzinfo=timezone.utc)
    await _commit_chunks(
        backend=backend,
        adapter=adapter,
        engine=engine,
        episode=Episode(
            id=f"episode-batch-{decision.value}",
            content="Alice works in research and studies temporal graphs.",
            reference_at=reference,
        ),
        chunks=[
            (
                {
                    "Alice": [
                        _record(
                            "Alice",
                            "Alice works in research.",
                            "chunk-general",
                            valid_at=reference,
                        )
                    ]
                },
                {},
            ),
            (
                {
                    "Alice": [
                        _record(
                            "Alice",
                            "Alice researches temporal knowledge graphs.",
                            "chunk-specific",
                            valid_at=reference,
                        )
                    ]
                },
                {},
            ),
        ],
    )

    alice = await backend.find_entity_exact("Alice")
    atoms = await backend.list_owner_atoms(alice.id)
    assert len(atoms) == 2
    general = next(atom for atom in atoms if atom.content == "Alice works in research.")
    specific = next(
        atom
        for atom in atoms
        if atom.content == "Alice researches temporal knowledge graphs."
    )
    assert (general.expired_at is not None) is expected_expired
    evolutions = await backend.list_atom_evolutions(specific.id)
    if expected_relation is None:
        assert evolutions == []
    else:
        assert [(row["relation_type"], row["target_atom_id"]) for row in evolutions] == [
            (expected_relation, general.id)
        ]
    await backend.finalize()


@pytest.mark.asyncio
async def test_in_batch_candidates_never_cross_owner(tmp_path: Path) -> None:
    backend = SQLiteBackend(tmp_path / "memory.sqlite3", "workspace-a")
    await backend.initialize()
    decisions = _ScriptedDecisions()
    adapter = MagiKnowledgeAdapter(backend, decision_provider=decisions)
    engine = _Engine()
    episode = Episode(
        id="episode-owner-isolation",
        content="Alice and Bob each have two unrelated facts.",
    )
    await _commit_chunks(
        backend=backend,
        adapter=adapter,
        engine=engine,
        episode=episode,
        chunks=[
            (
                {
                    "Alice": [_record("Alice", "Alice likes tea.", "chunk-a")],
                    "Bob": [_record("Bob", "Bob likes coffee.", "chunk-a")],
                },
                {},
            ),
            (
                {
                    "Alice": [_record("Alice", "Alice reads novels.", "chunk-b")],
                    "Bob": [_record("Bob", "Bob runs marathons.", "chunk-b")],
                },
                {},
            ),
        ],
    )

    alice = await backend.find_entity_exact("Alice")
    bob = await backend.find_entity_exact("Bob")
    atoms = [
        *(await backend.list_owner_atoms(alice.id)),
        *(await backend.list_owner_atoms(bob.id)),
    ]
    owner_by_atom = {atom.id: atom.owner_id for atom in atoms}
    in_batch_requests = [
        request
        for call in decisions.atom_calls
        for request in call
        if any(
            candidate.match_kind.startswith("in_batch_")
            for candidate in request.candidates
        )
    ]
    assert len(in_batch_requests) == 2
    for request in in_batch_requests:
        assert {
            owner_by_atom[candidate.object_id]
            for candidate in request.candidates
        } == {request.atom.owner_id}
    await backend.finalize()


@pytest.mark.asyncio
async def test_batch_failure_and_illegal_history_target_degrade_without_overwrite(
    tmp_path: Path,
) -> None:
    backend = SQLiteBackend(tmp_path / "memory.sqlite3", "workspace-a")
    await backend.initialize()
    alice = await backend.put_entity(
        EntityRecord(
            id="entity-alice",
            workspace_id="workspace-a",
            canonical_name="Alice",
        )
    )
    bob = await backend.put_entity(
        EntityRecord(
            id="entity-bob",
            workspace_id="workspace-a",
            canonical_name="Bob",
        )
    )
    history = Episode(id="episode-history", content="Historical facts.")
    await backend.put_episode(history)
    alice_history = AtomRecord(
        id="atom-alice-history",
        workspace_id="workspace-a",
        owner_id=alice.id,
        content="Alice likes tea.",
        valid_at=None,
    )
    bob_history = AtomRecord(
        id="atom-bob-history",
        workspace_id="workspace-a",
        owner_id=bob.id,
        content="Bob likes coffee.",
        valid_at=None,
    )
    for atom in (alice_history, bob_history):
        await backend.put_atom(
            atom,
            AtomEvidence(atom_id=atom.id, episode_id=history.id),
        )

    decisions = _ScriptedDecisions(
        raise_in_batch=True,
        invalid_history_target=bob_history.id,
    )
    adapter = MagiKnowledgeAdapter(backend, decision_provider=decisions)
    engine = _Engine()
    await _commit_chunks(
        backend=backend,
        adapter=adapter,
        engine=engine,
        episode=Episode(
            id="episode-safe-degrade",
            content="Alice learned graph theory and database design.",
        ),
        chunks=[
            (
                {
                    "Alice": [
                        _record("Alice", "Alice learned graph theory.", "chunk-a")
                    ]
                },
                {},
            ),
            (
                {
                    "Alice": [
                        _record("Alice", "Alice learned database design.", "chunk-b")
                    ]
                },
                {},
            ),
        ],
    )

    stored_alice_history = await backend.get_atom(alice_history.id)
    stored_bob_history = await backend.get_atom(bob_history.id)
    assert stored_alice_history.expired_at is None
    assert stored_bob_history.expired_at is None
    alice_atoms = await backend.list_owner_atoms(alice.id)
    assert len(alice_atoms) == 3
    for atom in alice_atoms:
        assert await backend.list_atom_evolutions(atom.id) == []
    await backend.finalize()
