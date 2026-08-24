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
    EntityRecord,
    EntityResolution,
    Episode,
    stable_relation_id,
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


class RecordingIndependentDecisions(IndependentDecisions):
    def __init__(self) -> None:
        super().__init__()
        self.entity_requests: list[Any] = []

    async def resolve_entities(self, **kwargs: Any) -> dict[str, EntityResolution]:
        self.entity_requests = list(kwargs.get("requests") or [])
        return await super().resolve_entities(**kwargs)


class ContextSeparatingDecisions(RecordingIndependentDecisions):
    async def resolve_entities(self, **kwargs: Any) -> dict[str, EntityResolution]:
        resolutions = await super().resolve_entities(**kwargs)
        for request in self.entity_requests:
            if request.name == "Alice" and request.candidates:
                resolutions[request.key] = EntityResolution(
                    None,
                    "Alice",
                    1.0,
                    "incident relationship describes a different Alice",
                )
        return resolutions


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
    def test_relation_only_endpoints_persist_without_entity_atoms(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                sqlite = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await sqlite.initialize()
                engine = FakeEngine()
                decisions = RecordingIndependentDecisions()
                adapter = MagiKnowledgeAdapter(sqlite, decision_provider=decisions)
                reference = datetime(2026, 8, 5, tzinfo=timezone.utc)
                episode = Episode(
                    id="episode-relation-only",
                    content="Alice became a member of MAGI.",
                    reference_at=reference,
                )
                await sqlite.put_episode(episode)
                nodes = {
                    name: [
                        {
                            "entity_name": name,
                            "entity_type": entity_type,
                            "aliases": [],
                            "description": "",
                            "atom_payload": None,
                            "source_id": "chunk-relation-only",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                            "magi_endpoint_only": True,
                        }
                    ]
                    for name, entity_type in (
                        ("Alice", "person"),
                        ("MAGI", "organization"),
                    )
                }
                edges = {
                    ("Alice", "MAGI"): [
                        {
                            "src_id": "Alice",
                            "tgt_id": "MAGI",
                            "description": "Alice became a member of MAGI.",
                            "keywords": "membership",
                            "weight": 1.0,
                            "atom_payload": {
                                "predicate": "member_of",
                                "valid_at": reference.isoformat(),
                            },
                            "source_id": "chunk-relation-only",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ]
                }

                merge = AsyncMock()
                with patch(
                    "magi_core.memory.adapter.merge_nodes_and_edges",
                    new=merge,
                ):
                    await adapter.commit(
                        [(nodes, edges)],
                        KnowledgeCommitContext(
                            rag=engine,
                            doc_id=episode.id,
                            file_path="episode.txt",
                        ),
                    )

                alice = await sqlite.find_entity_exact("Alice")
                magi = await sqlite.find_entity_exact("MAGI")
                self.assertIsNotNone(alice)
                self.assertIsNotNone(magi)
                self.assertEqual(await sqlite.list_owner_atoms(alice.id), [])
                self.assertEqual(await sqlite.list_owner_atoms(magi.id), [])
                relation_id = stable_relation_id("workspace-a", alice.id, magi.id)
                relation_atoms = await sqlite.list_owner_atoms(relation_id)
                self.assertEqual(len(relation_atoms), 1)
                relation_atom = relation_atoms[0]
                self.assertEqual(relation_atom.subject_entity_id, alice.id)
                self.assertEqual(relation_atom.object_entity_id, magi.id)
                self.assertEqual(relation_atom.valid_at, reference)
                memory = await sqlite.get_atom_memory(relation_atom.id)
                self.assertEqual(memory.evidence[0].episode_id, episode.id)
                self.assertEqual(
                    memory.evidence[0].quote,
                    "Alice became a member of MAGI.",
                )

                contexts = {
                    request.name: request.atom_texts
                    for request in decisions.entity_requests
                }
                self.assertEqual(
                    contexts,
                    {
                        "Alice": ("Alice became a member of MAGI.",),
                        "MAGI": ("Alice became a member of MAGI.",),
                    },
                )
                projected_nodes = merge.await_args.kwargs["chunk_results"][0][0]
                self.assertEqual(projected_nodes["Alice"][0]["description"], "")
                self.assertTrue(
                    projected_nodes["Alice"][0]["magi_endpoint_only"]
                )
                self.assertEqual(
                    engine.chunk_entity_relation_graph.nodes["Alice"]["atom_ids"],
                    [],
                )
                self.assertNotIn(
                    "magi_endpoint_only",
                    engine.chunk_entity_relation_graph.nodes["Alice"],
                )

                deletion = await sqlite.backup_and_delete_episode(episode.id)
                self.assertIsNotNone(deletion)
                overview = await sqlite.memory_overview()
                self.assertEqual(overview["entities"], 0)
                self.assertEqual(overview["relations"], 0)
                self.assertEqual(overview["atoms"], 0)
                await sqlite.finalize()

        asyncio.run(scenario())

    def test_relation_only_endpoint_reuses_existing_entity_without_copying_atoms(
        self,
    ) -> None:
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
                existing = await sqlite.put_entity(
                    EntityRecord(
                        id="entity-alice",
                        workspace_id="workspace-a",
                        canonical_name="Alice",
                        entity_type="person",
                    )
                )
                history_episode = Episode(
                    id="episode-history", content="Alice is an architect."
                )
                await sqlite.put_episode(history_episode)
                existing_atom = AtomRecord(
                    id="atom-alice-history",
                    workspace_id="workspace-a",
                    owner_id=existing.id,
                    content="Alice is an architect.",
                    valid_at=None,
                )
                await sqlite.put_atom(
                    existing_atom,
                    AtomEvidence(
                        atom_id=existing_atom.id,
                        episode_id=history_episode.id,
                    ),
                )

                episode = Episode(id="episode-reuse", content="Alice joined MAGI.")
                await sqlite.put_episode(episode)

                def endpoint(name: str, entity_type: str) -> dict[str, Any]:
                    return {
                        "entity_name": name,
                        "entity_type": entity_type,
                        "aliases": [],
                        "description": "",
                        "atom_payload": None,
                        "source_id": "chunk-reuse",
                        "file_path": "episode.txt",
                        "timestamp": 1,
                        "magi_endpoint_only": True,
                    }
                nodes = {
                    "Alice": [endpoint("Alice", "person")],
                    "MAGI": [endpoint("MAGI", "organization")],
                }
                edges = {
                    ("Alice", "MAGI"): [
                        {
                            "src_id": "Alice",
                            "tgt_id": "MAGI",
                            "description": "Alice joined MAGI.",
                            "keywords": "membership",
                            "weight": 1.0,
                            "atom_payload": {"predicate": "member_of"},
                            "source_id": "chunk-reuse",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ]
                }

                with patch(
                    "magi_core.memory.adapter.merge_nodes_and_edges",
                    new=AsyncMock(),
                ):
                    await adapter.commit(
                        [(nodes, edges)],
                        KnowledgeCommitContext(
                            rag=engine,
                            doc_id=episode.id,
                            file_path="episode.txt",
                        ),
                    )

                resolved = await sqlite.find_entity_exact("Alice")
                self.assertEqual(resolved.id, existing.id)
                self.assertEqual(
                    [atom.id for atom in await sqlite.list_owner_atoms(existing.id)],
                    [existing_atom.id],
                )
                self.assertNotIn(
                    "magi_endpoint_only",
                    engine.chunk_entity_relation_graph.nodes["Alice"],
                )
                await sqlite.finalize()

        asyncio.run(scenario())

    def test_relation_only_same_name_can_stay_distinct_using_incident_context(
        self,
    ) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                sqlite = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await sqlite.initialize()
                existing = await sqlite.put_entity(
                    EntityRecord(
                        id="entity-existing-alice",
                        workspace_id="workspace-a",
                        canonical_name="Alice",
                        entity_type="software",
                    )
                )
                history = Episode(
                    id="episode-existing-alice",
                    content="Alice is a deployment tool.",
                )
                await sqlite.put_episode(history)
                old_atom = AtomRecord(
                    id="atom-existing-alice",
                    workspace_id="workspace-a",
                    owner_id=existing.id,
                    content="Alice is a deployment tool.",
                    valid_at=None,
                )
                await sqlite.put_atom(
                    old_atom,
                    AtomEvidence(atom_id=old_atom.id, episode_id=history.id),
                )

                decisions = ContextSeparatingDecisions()
                adapter = MagiKnowledgeAdapter(sqlite, decision_provider=decisions)
                engine = FakeEngine()
                episode = Episode(
                    id="episode-different-alice",
                    content="Alice joined MAGI as a researcher.",
                )
                await sqlite.put_episode(episode)

                def endpoint(name: str, entity_type: str) -> dict[str, Any]:
                    return {
                        "entity_name": name,
                        "entity_type": entity_type,
                        "aliases": [],
                        "description": "",
                        "atom_payload": None,
                        "source_id": "chunk-different-alice",
                        "file_path": "episode.txt",
                        "timestamp": 1,
                        "magi_endpoint_only": True,
                    }

                nodes = {
                    "Alice": [endpoint("Alice", "person")],
                    "MAGI": [endpoint("MAGI", "organization")],
                }
                edges = {
                    ("Alice", "MAGI"): [
                        {
                            "src_id": "Alice",
                            "tgt_id": "MAGI",
                            "description": "Alice joined MAGI as a researcher.",
                            "keywords": "employment",
                            "weight": 1.0,
                            "atom_payload": {"predicate": "joined"},
                            "source_id": "chunk-different-alice",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ]
                }
                with patch(
                    "magi_core.memory.adapter.merge_nodes_and_edges",
                    new=AsyncMock(),
                ):
                    await adapter.commit(
                        [(nodes, edges)],
                        KnowledgeCommitContext(
                            rag=engine,
                            doc_id=episode.id,
                            file_path="episode.txt",
                        ),
                    )

                alice_candidates = await sqlite.search_entity_names("Alice")
                alice_ids = {candidate.object_id for candidate in alice_candidates}
                self.assertIn(existing.id, alice_ids)
                self.assertEqual(len(alice_ids), 2)
                new_alice_id = next(item for item in alice_ids if item != existing.id)
                magi = await sqlite.find_entity_exact("MAGI")
                relation_id = stable_relation_id(
                    "workspace-a", new_alice_id, magi.id
                )
                relation_atoms = await sqlite.list_owner_atoms(relation_id)
                self.assertEqual(len(relation_atoms), 1)
                self.assertEqual(relation_atoms[0].subject_entity_id, new_alice_id)
                request = next(
                    item for item in decisions.entity_requests if item.name == "Alice"
                )
                self.assertEqual(
                    request.atom_texts,
                    ("Alice joined MAGI as a researcher.",),
                )
                await sqlite.finalize()

        asyncio.run(scenario())

    def test_resolved_owner_does_not_collapse_distinct_atom_ids(self) -> None:
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
                episode = Episode(
                    id="episode-merged-owners",
                    content="Alice and Alicia discussed Bob and Robert.",
                )
                await sqlite.put_episode(episode)
                adapter.register_episodes([episode])

                alice = await sqlite.put_entity(
                    EntityRecord(
                        id="entity-alice",
                        workspace_id="workspace-a",
                        canonical_name="Alice",
                        aliases=("Alicia",),
                    )
                )
                bob = await sqlite.put_entity(
                    EntityRecord(
                        id="entity-bob",
                        workspace_id="workspace-a",
                        canonical_name="Bob",
                        aliases=("Robert",),
                    )
                )
                resolved = {
                    "Alice": alice,
                    "Alicia": alice,
                    "Bob": bob,
                    "Robert": bob,
                }
                nodes = {
                    name: [
                        {
                            "entity_name": name,
                            "entity_type": "person",
                            "description": f"Fact about {name}.",
                            "atom_payload": {},
                            "source_id": "chunk-shared",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ]
                    for name in resolved
                }
                edges = {
                    ("Alice", "Bob"): [
                        {
                            "src_id": "Alice",
                            "tgt_id": "Bob",
                            "description": "Alice discussed Bob.",
                            "keywords": "discussed",
                            "weight": 1.0,
                            "atom_payload": {},
                            "source_id": "chunk-shared",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ],
                    ("Alicia", "Robert"): [
                        {
                            "src_id": "Alicia",
                            "tgt_id": "Robert",
                            "description": "Alicia interviewed Robert.",
                            "keywords": "interviewed",
                            "weight": 1.0,
                            "atom_payload": {},
                            "source_id": "chunk-shared",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ],
                }

                with (
                    patch.object(
                        adapter,
                        "_resolve_entities",
                        new=AsyncMock(return_value=(resolved, {})),
                    ),
                    patch(
                        "magi_core.memory.adapter.merge_nodes_and_edges",
                        new=AsyncMock(),
                    ),
                ):
                    await adapter.commit(
                        [(nodes, edges)],
                        KnowledgeCommitContext(
                            rag=engine,
                            doc_id=episode.id,
                            file_path="episode.txt",
                        ),
                    )

                alice_atoms = await sqlite.list_owner_atoms(alice.id)
                bob_atoms = await sqlite.list_owner_atoms(bob.id)
                relation_id = stable_relation_id("workspace-a", alice.id, bob.id)
                relation_atoms = await sqlite.list_owner_atoms(relation_id)
                self.assertEqual(len(alice_atoms), 2)
                self.assertEqual(len(bob_atoms), 2)
                self.assertEqual(len(relation_atoms), 2)
                self.assertEqual(len({atom.id for atom in relation_atoms}), 2)
                await sqlite.finalize()

        asyncio.run(scenario())

    def test_pending_relation_atom_is_kept_in_graph_projection(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                sqlite = SQLiteBackend(Path(temporary) / "memory.db", "workspace-a")
                await sqlite.initialize()
                engine = FakeEngine()
                adapter = MagiKnowledgeAdapter(
                    sqlite, decision_provider=IndependentDecisions()
                )
                episode = Episode(id="episode-future", content="Alice will meet Bob.")
                await sqlite.put_episode(episode)
                nodes = {
                    name: [
                        {
                            "entity_name": name,
                            "entity_type": "person",
                            "description": f"{name} is a person.",
                            "atom_payload": {},
                            "source_id": "chunk-future",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ]
                    for name in ("Alice", "Bob")
                }
                edges = {
                    ("Alice", "Bob"): [
                        {
                            "src_id": "Alice",
                            "tgt_id": "Bob",
                            "description": "Alice will meet Bob.",
                            "keywords": "meet",
                            "weight": 1.0,
                            "atom_payload": {"valid_at": "2099-01-01T00:00:00+00:00"},
                            "source_id": "chunk-future",
                            "file_path": "episode.txt",
                            "timestamp": 1,
                        }
                    ]
                }
                merge = AsyncMock()
                with patch(
                    "magi_core.memory.adapter.merge_nodes_and_edges",
                    new=merge,
                ):
                    await adapter.commit(
                        [(nodes, edges)],
                        KnowledgeCommitContext(
                            rag=engine,
                            doc_id=episode.id,
                            file_path="episode.txt",
                        ),
                    )

                projected_edges = merge.await_args.kwargs["chunk_results"][0][1]
                relation_rows = projected_edges[("Alice", "Bob")]
                self.assertEqual(len(relation_rows), 1)
                self.assertIn("Alice will meet Bob.", relation_rows[0]["description"])
                self.assertIn("status=pending", relation_rows[0]["description"])
                await sqlite.finalize()

        asyncio.run(scenario())

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
                entity = await sqlite.find_entity_exact("Alice")
                entity_id = entity.id
                self.assertEqual(node["magi_entity_id"], entity_id)
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
