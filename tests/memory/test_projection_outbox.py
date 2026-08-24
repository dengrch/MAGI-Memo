from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import (
    AtomEvidence,
    AtomRecord,
    EntityRecord,
    Episode,
    RelationRecord,
    stable_entity_id,
    stable_relation_id,
)
from magi_core.memory.adapter import MagiKnowledgeAdapter


class FakeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str], dict[str, Any]] = {}
        self.flushes = 0
        self.fail_flush = False

    async def get_node(self, name: str) -> dict[str, Any] | None:
        return self.nodes.get(name)

    async def upsert_node(self, name: str, node_data: dict[str, Any]) -> None:
        self.nodes[name] = dict(node_data)

    async def delete_node(self, name: str) -> None:
        self.nodes.pop(name, None)
        for pair in list(self.edges):
            if name in pair:
                self.edges.pop(pair, None)

    async def get_edge(
        self, source: str, target: str
    ) -> dict[str, Any] | None:
        return self.edges.get(tuple(sorted((source, target))))

    async def upsert_edge(
        self, source: str, target: str, edge_data: dict[str, Any]
    ) -> None:
        self.edges[tuple(sorted((source, target)))] = dict(edge_data)

    async def remove_edges(self, edges: list[tuple[str, str]]) -> None:
        for source, target in edges:
            self.edges.pop(tuple(sorted((source, target))), None)

    async def index_done_callback(self) -> None:
        if self.fail_flush:
            raise RuntimeError("injected graph flush failure")
        self.flushes += 1


class FakeVectorStorage:
    def __init__(self, *, fail_upsert: bool = False) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.fail_upsert = fail_upsert
        self.flushes = 0
        self.deleted_entities: list[str] = []

    async def upsert(self, data: dict[str, dict[str, Any]]) -> None:
        if self.fail_upsert:
            raise RuntimeError("injected vector projection failure")
        self.records.update({key: dict(value) for key, value in data.items()})

    async def delete(self, ids: list[str]) -> None:
        for object_id in ids:
            self.records.pop(object_id, None)

    async def delete_entity(self, entity_name: str) -> None:
        self.deleted_entities.append(entity_name)
        for object_id, value in list(self.records.items()):
            if value.get("entity_name") == entity_name:
                self.records.pop(object_id, None)

    async def index_done_callback(self) -> None:
        self.flushes += 1


class FakeRag:
    def __init__(self, *, fail_entity_upsert: bool = False) -> None:
        self.chunk_entity_relation_graph = FakeGraph()
        self.entities_vdb = FakeVectorStorage(fail_upsert=fail_entity_upsert)
        self.relationships_vdb = FakeVectorStorage()
        self.llm_response_cache = None

    def _build_global_config(self) -> dict[str, Any]:
        # One-Atom materializations do not invoke the summary LLM.  The
        # truncation helper also intentionally accepts this minimal config.
        return {"magi_memory_enabled": True}


async def seed_entity(
    backend: SQLiteBackend,
    *,
    episode_id: str = "episode-outbox",
) -> tuple[Episode, EntityRecord, AtomRecord]:
    episode = Episode(id=episode_id, content="Alice remembers MAGI.")
    await backend.put_episode(episode)
    entity = EntityRecord(
        id=stable_entity_id(backend.workspace_id, "Alice"),
        workspace_id=backend.workspace_id,
        canonical_name="Alice",
        entity_type="person",
    )
    await backend.put_entity(entity)
    atom = AtomRecord(
        id=f"atom-{episode_id}",
        workspace_id=backend.workspace_id,
        owner_id=entity.id,
        content="Alice remembers MAGI.",
        valid_at=episode.effective_reference_at,
    )
    await backend.put_atom(
        atom,
        AtomEvidence(
            atom_id=atom.id,
            episode_id=episode.id,
            extraction_revision="chunk-outbox",
        ),
    )
    return episode, entity, atom


class ProjectionOutboxTests(unittest.TestCase):
    def test_atom_mutations_coalesce_one_owner_and_use_revision_fencing(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "magi-memory.db", "workspace-a"
                )
                await backend.initialize()
                _, entity, atom = await seed_entity(backend)

                tasks = await backend.list_projection_tasks(ready_only=False)
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["owner_id"], entity.id)
                self.assertEqual(tasks[0]["target_revision"], 1)
                self.assertEqual(
                    tasks[0]["owner_snapshot"]["canonical_name"], "Alice"
                )

                second = Episode(id="episode-second", content="Same evidence.")
                await backend.put_episode(second)
                await backend.add_atom_evidence(
                    atom.id,
                    AtomEvidence(atom_id=atom.id, episode_id=second.id),
                )
                tasks = await backend.list_projection_tasks(ready_only=False)
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["target_revision"], 2)

                # A stale worker must not acknowledge a newer owner state.
                self.assertFalse(
                    await backend.mark_projection_applied(entity.id, 1)
                )
                self.assertTrue(
                    await backend.mark_projection_applied(entity.id, 2)
                )

                await backend.expire_atom(atom.id, expired_at=atom.created_at)
                tasks = await backend.list_projection_tasks(ready_only=False)
                self.assertEqual(tasks[0]["target_revision"], 3)
                await backend.finalize()

        asyncio.run(scenario())

    def test_failed_projection_is_retried_from_latest_sqlite_state(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "magi-memory.db", "workspace-a"
                )
                await backend.initialize()
                _, entity, atom = await seed_entity(backend)
                rag = FakeRag(fail_entity_upsert=True)
                adapter = MagiKnowledgeAdapter(backend)

                first = await adapter.reconcile_projections(
                    rag, ready_only=False
                )
                self.assertEqual(first, {"processed": 1, "applied": 0, "failed": 1})
                failed = await backend.list_projection_tasks(ready_only=False)
                self.assertEqual(failed[0]["status"], "failed")
                self.assertEqual(failed[0]["attempts"], 1)
                # Neo4j may have succeeded before the VDB failure. Replaying is
                # therefore required to be an idempotent full replacement.
                self.assertEqual(
                    rag.chunk_entity_relation_graph.nodes["Alice"]["atom_ids"],
                    [atom.id],
                )

                rag.entities_vdb.fail_upsert = False
                rag.chunk_entity_relation_graph.fail_flush = True
                second = await adapter.reconcile_projections(
                    rag, ready_only=False
                )
                self.assertEqual(
                    second, {"processed": 1, "applied": 0, "failed": 1}
                )
                still_failed = await backend.list_projection_tasks(
                    ready_only=False
                )
                self.assertEqual(still_failed[0]["attempts"], 2)

                rag.chunk_entity_relation_graph.fail_flush = False
                third = await adapter.reconcile_projections(
                    rag, ready_only=False
                )
                self.assertEqual(
                    third, {"processed": 1, "applied": 1, "failed": 0}
                )
                self.assertEqual(
                    await backend.list_projection_tasks(ready_only=False), []
                )
                overview = await backend.memory_overview()
                self.assertEqual(overview["projection_outbox"], {"applied": 1})
                self.assertIn(
                    "Alice remembers MAGI.",
                    rag.chunk_entity_relation_graph.nodes["Alice"]["description"],
                )
                self.assertEqual(len(rag.entities_vdb.records), 1)
                await backend.finalize()

        asyncio.run(scenario())

    def test_startup_reconcile_and_delete_are_idempotent(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "magi-memory.db", "workspace-a"
                )
                await backend.initialize()
                episode, entity, _ = await seed_entity(backend)
                rag = FakeRag()
                adapter = MagiKnowledgeAdapter(backend)

                # start_projection_worker performs the startup reconcile before
                # accepting background retries.
                await adapter.start_projection_worker(rag)
                self.assertIn("Alice", rag.chunk_entity_relation_graph.nodes)
                await adapter.stop_projection_worker()

                deletion = await backend.backup_and_delete_episode(episode.id)
                self.assertIn(entity.id, deletion["affected_owner_ids"])
                tasks = await backend.list_projection_tasks(ready_only=False)
                self.assertEqual(tasks[0]["owner_snapshot"]["canonical_name"], "Alice")

                await adapter.reconcile_projections(rag, ready_only=False)
                self.assertNotIn("Alice", rag.chunk_entity_relation_graph.nodes)
                self.assertIn("Alice", rag.entities_vdb.deleted_entities)

                # Requeueing the already-deleted owner remains harmless and
                # converges to the same absent state.
                await backend.queue_owner_projection(entity.id)
                requeued = await backend.list_projection_tasks(ready_only=False)
                self.assertEqual(
                    requeued[0]["owner_snapshot"]["canonical_name"], "Alice"
                )
                await adapter.reconcile_projections(rag, ready_only=False)
                self.assertNotIn("Alice", rag.chunk_entity_relation_graph.nodes)
                await backend.finalize()

        asyncio.run(scenario())

    def test_relation_projection_and_episode_delete_converge_all_sinks(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "magi-memory.db", "workspace-a"
                )
                await backend.initialize()
                episode = Episode(id="episode-relation", content="Alice knows Bob.")
                await backend.put_episode(episode)
                alice = EntityRecord(
                    id=stable_entity_id("workspace-a", "Alice"),
                    workspace_id="workspace-a",
                    canonical_name="Alice",
                )
                bob = EntityRecord(
                    id=stable_entity_id("workspace-a", "Bob"),
                    workspace_id="workspace-a",
                    canonical_name="Bob",
                )
                for entity in (alice, bob):
                    await backend.put_entity(entity)
                    atom = AtomRecord(
                        id=f"atom-{entity.canonical_name.lower()}",
                        workspace_id="workspace-a",
                        owner_id=entity.id,
                        content=f"{entity.canonical_name} is a person.",
                        valid_at=episode.effective_reference_at,
                    )
                    await backend.put_atom(
                        atom,
                        AtomEvidence(atom_id=atom.id, episode_id=episode.id),
                    )

                relation = RelationRecord(
                    id=stable_relation_id("workspace-a", alice.id, bob.id),
                    workspace_id="workspace-a",
                    entity_a_id=alice.id,
                    entity_b_id=bob.id,
                    entity_a_name="Alice",
                    entity_b_name="Bob",
                    keywords=("knows",),
                )
                await backend.put_relation(relation)
                relation_atom = AtomRecord(
                    id="atom-relation",
                    workspace_id="workspace-a",
                    owner_id=relation.id,
                    content="Alice knows Bob.",
                    valid_at=episode.effective_reference_at,
                    subject_entity_id=alice.id,
                    predicate="knows",
                    object_entity_id=bob.id,
                    relation_keywords=("knows",),
                )
                await backend.put_atom(
                    relation_atom,
                    AtomEvidence(
                        atom_id=relation_atom.id,
                        episode_id=episode.id,
                    ),
                )

                rag = FakeRag()
                adapter = MagiKnowledgeAdapter(backend)
                reconciled = await adapter.reconcile_projections(
                    rag, ready_only=False
                )
                self.assertEqual(
                    reconciled, {"processed": 3, "applied": 3, "failed": 0}
                )
                edge = rag.chunk_entity_relation_graph.edges[("Alice", "Bob")]
                self.assertEqual(edge["magi_relation_id"], relation.id)
                self.assertEqual(edge["atom_ids"], [relation_atom.id])
                self.assertIn("Alice knows Bob.", edge["description"])
                self.assertEqual(len(rag.relationships_vdb.records), 1)

                backup_id = await adapter.delete_episode(episode.id, rag=rag)
                self.assertIsNotNone(backup_id)
                self.assertEqual(rag.chunk_entity_relation_graph.nodes, {})
                self.assertEqual(rag.chunk_entity_relation_graph.edges, {})
                self.assertEqual(rag.relationships_vdb.records, {})
                self.assertEqual(
                    await backend.list_projection_tasks(ready_only=False), []
                )
                await backend.finalize()

        asyncio.run(scenario())
