from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np

from magi_core.backend.sqlite import SQLiteBackend
from magi_core.ingestion import KnowledgeCommitContext
from magi_core.memory import (
    AtomClassification,
    AtomDecision,
    AtomEvidence,
    AtomRecord,
    EntityResolution,
    Episode,
)
from magi_core.memory.adapter import MagiKnowledgeAdapter


class IndependentDecisions:
    def __init__(self) -> None:
        self.resolve_calls = 0
        self.atom_resolve_calls = 0

    async def resolve_entities(self, **kwargs: Any) -> dict[str, EntityResolution]:
        self.resolve_calls += 1
        resolutions = {}
        for request in kwargs.get("requests") or []:
            if request.candidates:
                candidate = request.candidates[0]
                resolutions[request.key] = EntityResolution(
                    candidate.object_id,
                    candidate.text.splitlines()[0],
                    1.0,
                    "same entity",
                )
            else:
                resolutions[request.key] = EntityResolution(
                    None, None, 1.0, "new entity"
                )
        return resolutions

    async def resolve_atoms(self, **kwargs: Any) -> dict[str, AtomClassification]:
        self.atom_resolve_calls += 1
        return {
            request.atom.id: AtomClassification(
                decision=AtomDecision.INDEPENDENT,
                confidence=1.0,
                reason="test",
            )
            for request in kwargs.get("requests") or []
        }


class FakeEmbedding:
    embedding_dim = 3
    model_name = "fake-3d"

    async def __call__(self, texts: list[str], **kwargs: Any) -> np.ndarray:
        rows = []
        for text in texts:
            rows.append(
                [
                    float(len(text)),
                    float(sum(ord(char) for char in text) % 101),
                    1.0,
                ]
            )
        return np.asarray(rows, dtype=np.float32)


class Neo4JStorage:
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


class FakeEngine:
    def __init__(self) -> None:
        self.embedding_func = FakeEmbedding()
        self.chunk_entity_relation_graph = Neo4JStorage()
        self.entities_vdb = object()
        self.relationships_vdb = object()
        self.llm_response_cache = object()
        self.entity_chunks = object()
        self.relation_chunks = object()

    def _build_global_config(self) -> dict[str, Any]:
        return {"workspace": "test"}


class MagiKnowledgeAdapterTests(unittest.TestCase):
    def test_episode_entities_use_one_resolution_call_and_alias_vectors(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                sqlite = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await sqlite.initialize()
                engine = FakeEngine()
                decisions = IndependentDecisions()
                adapter = MagiKnowledgeAdapter(sqlite, decision_provider=decisions)
                episode = Episode(id="episode-alias", content="Alice met Bob.")
                await sqlite.put_episode(episode)
                records = {
                    "Alice": [
                        {
                            "entity_name": "Alice",
                            "entity_type": "person",
                            "aliases": ["A. Example"],
                            "description": "Alice is a person.",
                            "atom_payload": {},
                            "source_id": "chunk-alias",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ],
                    "Bob": [
                        {
                            "entity_name": "Bob",
                            "entity_type": "person",
                            "aliases": [],
                            "description": "Bob is a person.",
                            "atom_payload": {},
                            "source_id": "chunk-alias",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ],
                }
                with patch(
                    "magi_core.memory.adapter.merge_nodes_and_edges",
                    new=AsyncMock(),
                ):
                    await adapter.commit(
                        [(records, {})],
                        KnowledgeCommitContext(
                            rag=engine,
                            doc_id=episode.id,
                            file_path="episode.txt",
                        ),
                    )
                self.assertEqual(decisions.resolve_calls, 1)
                alias_vector = (await engine.embedding_func(["A. Example"]))[0]
                matches = await sqlite.search_embeddings(
                    object_kind="entity_name",
                    query_vector=alias_vector,
                    model_name="fake-3d",
                )
                entity = await sqlite.find_entity_exact("A. Example")
                self.assertEqual(matches[0].object_id, entity.id)
                self.assertIn("A. Example", entity.aliases)
                await sqlite.finalize()

        asyncio.run(scenario())

    def test_concurrent_commits_share_one_disambiguation_boundary(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                sqlite = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await sqlite.initialize()
                engine = FakeEngine()
                adapter = MagiKnowledgeAdapter(
                    sqlite, decision_provider=IndependentDecisions()
                )
                reference = datetime(2026, 8, 5, tzinfo=timezone.utc)

                async def commit(episode_id: str) -> None:
                    episode = Episode(
                        id=episode_id,
                        content="Alice joined MAGI.",
                        reference_at=reference,
                    )
                    await sqlite.put_episode(episode)
                    adapter.register_episodes([episode])
                    chunks = [
                        (
                            {
                                "Alice": [
                                    {
                                        "entity_name": "Alice",
                                        "entity_type": "person",
                                        "description": "Alice joined MAGI.",
                                        "atom_payload": {
                                            "valid_at": reference.isoformat(),
                                            "invalid_at": None,
                                        },
                                        "source_id": f"chunk-{episode_id}",
                                        "file_path": "episode.txt",
                                        "timestamp": 1,
                                    }
                                ]
                            },
                            {},
                        )
                    ]
                    await adapter.commit(
                        chunks,
                        KnowledgeCommitContext(
                            rag=engine,
                            doc_id=episode_id,
                            file_path="episode.txt",
                        ),
                    )

                with patch(
                    "magi_core.memory.adapter.merge_nodes_and_edges",
                    new=AsyncMock(),
                ):
                    await asyncio.gather(commit("episode-a"), commit("episode-b"))

                candidates = await sqlite.search_entity_names("Alice")
                self.assertEqual(len(candidates), 1)
                atoms = await sqlite.list_owner_atoms(candidates[0].object_id)
                self.assertEqual(len(atoms), 1)
                self.assertEqual(atoms[0].support_count, 2)
                await sqlite.finalize()

        asyncio.run(scenario())

    def test_strict_commit_reuses_owner_scoped_exact_atom(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                sqlite = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await sqlite.initialize()
                engine = FakeEngine()
                adapter = MagiKnowledgeAdapter(
                    sqlite, decision_provider=IndependentDecisions()
                )
                reference = datetime(2026, 8, 5, tzinfo=timezone.utc)

                async def commit_episode(episode_id: str) -> None:
                    episode = Episode(
                        id=episode_id,
                        content="Alice joined MAGI.",
                        reference_at=reference,
                    )
                    await sqlite.put_episode(episode)
                    adapter.register_episodes([episode])
                    atom_payload = {
                        "valid_at": reference.isoformat(),
                        "invalid_at": None,
                        "confidence": 0.9,
                        "importance": 0.8,
                    }
                    chunks = [
                        (
                            {
                                "Alice": [
                                    {
                                        "entity_name": "Alice",
                                        "entity_type": "person",
                                        "description": "Alice joined MAGI.",
                                        "atom_payload": atom_payload,
                                        "source_id": "chunk-a",
                                        "file_path": "episode.txt",
                                        "timestamp": 1,
                                    }
                                ]
                            },
                            {},
                        )
                    ]
                    with patch(
                        "magi_core.memory.adapter.merge_nodes_and_edges",
                        new=AsyncMock(),
                    ):
                        await adapter.commit(
                            chunks,
                            KnowledgeCommitContext(
                                rag=engine,
                                doc_id=episode_id,
                                file_path="episode.txt",
                            ),
                        )

                await commit_episode("episode-a")
                await commit_episode("episode-b")
                node = engine.chunk_entity_relation_graph.nodes["Alice"]
                self.assertEqual(node["entity_id"], "Alice")
                entity_id = node["magi_entity_id"]
                atoms = await sqlite.list_owner_atoms(entity_id)
                self.assertEqual(len(atoms), 1)
                self.assertEqual(atoms[0].support_count, 2)
                self.assertEqual(
                    engine.chunk_entity_relation_graph.nodes["Alice"]["atom_ids"],
                    [atom.id for atom in atoms],
                )
                self.assertEqual(await sqlite.get_workspace_mode(), "magi_strict")
                await sqlite.finalize()

        asyncio.run(scenario())

    def test_atom_write_does_not_call_deduplication_role(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                sqlite = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await sqlite.initialize()
                first_episode = Episode(
                    id="episode-first", content="Alice joined MAGI."
                )
                second_episode = Episode(
                    id="episode-second", content="Alice joined MAGI again."
                )
                await sqlite.put_episode(first_episode)
                await sqlite.put_episode(second_episode)
                reference = datetime(2026, 8, 5, tzinfo=timezone.utc)
                existing = AtomRecord(
                    id="atom-existing",
                    workspace_id="workspace-a",
                    owner_id="entity-alice",
                    content="Alice joined MAGI.",
                    valid_at=reference,
                )
                await sqlite.put_atom(
                    existing,
                    AtomEvidence(
                        atom_id=existing.id,
                        episode_id=first_episode.id,
                    ),
                )

                decisions = IndependentDecisions()
                adapter = MagiKnowledgeAdapter(sqlite, decision_provider=decisions)
                new_atom = AtomRecord(
                    id="atom-new",
                    workspace_id="workspace-a",
                    owner_id="entity-alice",
                    content="Alice joined MAGI again.",
                    valid_at=reference,
                )
                await adapter._persist_atom(
                    engine=FakeEngine(),
                    atom=new_atom,
                    vector=np.asarray([0.0, 0.0, 1.0], dtype=np.float32),
                    evidence=AtomEvidence(
                        atom_id=new_atom.id,
                        episode_id=second_episode.id,
                    ),
                )

                atoms = await sqlite.list_owner_atoms("entity-alice")
                self.assertEqual(
                    [atom.id for atom in atoms], [existing.id, new_atom.id]
                )
                await sqlite.finalize()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
