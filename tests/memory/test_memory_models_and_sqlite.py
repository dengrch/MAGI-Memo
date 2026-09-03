from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from magi_core.backend.sqlite import SQLiteBackend
from magi_core.backend.sqlite.migrations import MIGRATION_1, SCHEMA_VERSION
from magi_core.memory.adapter import MagiKnowledgeAdapter
from magi_core.memory import (
    AtomEvidence,
    AtomRecord,
    EntityRecord,
    Episode,
    EpisodeStatus,
    RelationRecord,
    TemporalStatus,
    stable_entity_id,
    stable_entity_name_embedding_id,
    stable_relation_id,
)


class MemoryModelsAndSQLiteTests(unittest.TestCase):
    def test_exploration_trace_events_are_ordered_replayable_and_archivable(
        self,
    ) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "magi-memory.db", "workspace-a"
                )
                await backend.initialize()
                await backend.register_exploration_trace(
                    "explore-1", "Who is Alice?"
                )
                first = await backend.append_exploration_event(
                    exploration_id="explore-1",
                    event_type="expand_started",
                    agent_id="s0",
                    call_id="call-1",
                    payload={"frontier": ["Alice"]},
                )
                second = await backend.append_exploration_event(
                    exploration_id="explore-1",
                    event_type="expand_completed",
                    agent_id="s0",
                    call_id="call-1",
                    payload={"result": {"results": []}},
                )
                self.assertEqual((first["seq"], second["seq"]), (1, 2))

                replay = await backend.get_exploration_events(
                    "explore-1", after_seq=1
                )
                self.assertEqual([event["seq"] for event in replay], [2])
                traces = await backend.list_exploration_traces()
                self.assertEqual(traces[0]["query"], "Who is Alice?")
                self.assertEqual(traces[0]["last_seq"], 2)

                self.assertTrue(await backend.archive_exploration_trace("explore-1"))
                self.assertEqual(await backend.list_exploration_traces(), [])
                archived = await backend.list_exploration_traces(
                    include_archived=True
                )
                self.assertEqual(archived[0]["status"], "archived")
                await backend.finalize()

        asyncio.run(scenario())

    def test_initialize_migrates_v4_evidence_without_loss(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "memory.sqlite3"
                now = datetime.now(timezone.utc).isoformat()
                with sqlite3.connect(path) as connection:
                    connection.executescript(MIGRATION_1)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, ?)",
                        (4, now),
                    )
                    connection.execute(
                        """
                        INSERT INTO episodes(
                            episode_id, workspace_id, kind, content, content_hash,
                            reference_at, metadata_json, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "episode-v4",
                            "workspace-a",
                            "document",
                            "Alice likes tea.",
                            "content-hash",
                            now,
                            "{}",
                            "indexed",
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO atoms(
                            atom_id, workspace_id, kind, owner_kind, owner_id,
                            content, normalized_hash, fingerprint,
                            relation_keywords_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "atom-v4",
                            "workspace-a",
                            "entity_fact",
                            "entity",
                            "entity-alice",
                            "Alice likes tea.",
                            "normalized-hash",
                            "fingerprint-v4",
                            "[]",
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO atom_evidence(
                            atom_id, episode_id, quote, span_start, span_end,
                            extraction_revision, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "atom-v4",
                            "episode-v4",
                            "Alice likes tea.",
                            -1,
                            -1,
                            "chunk-v4",
                            now,
                        ),
                    )

                backend = SQLiteBackend(path, "workspace-a")
                await backend.initialize()
                memory = await backend.get_atom_memory("atom-v4")
                self.assertEqual(len(memory.evidence), 1)
                self.assertEqual(memory.evidence[0].extraction_revision, "chunk-v4")
                await backend.finalize()

                with sqlite3.connect(path) as connection:
                    columns = {
                        row[1]
                        for row in connection.execute(
                            "PRAGMA table_info(atom_evidence)"
                        ).fetchall()
                    }
                    checkpoint_columns = {
                        row[1]
                        for row in connection.execute(
                            "PRAGMA table_info(owner_summary_checkpoints)"
                        ).fetchall()
                    }
                    version = connection.execute(
                        "SELECT max(version) FROM schema_migrations"
                    ).fetchone()[0]
                    checkpoint_table = connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'owner_summary_checkpoints'"
                    ).fetchone()
                self.assertIn("evidence_id", columns)
                self.assertIn("metrics_json", checkpoint_columns)
                self.assertIsNotNone(checkpoint_table)
                self.assertEqual(version, SCHEMA_VERSION)

        asyncio.run(scenario())

    def test_memory_workbench_views_are_paginated_and_evidence_aware(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "magi-memory.db", "workspace-a"
                )
                await backend.initialize()
                episode = Episode(id="episode-ui", content="Alice remembers MAGI.")
                await backend.put_episode(episode)
                await backend.mark_episode(episode.id, EpisodeStatus.INDEXED)
                entity_id = stable_entity_id("workspace-a", "Alice")
                await backend.put_entity(
                    EntityRecord(
                        id=entity_id,
                        workspace_id="workspace-a",
                        canonical_name="Alice",
                    )
                )
                atom = AtomRecord(
                    id="atom-ui",
                    workspace_id="workspace-a",
                    owner_id=entity_id,
                    content="Alice remembers MAGI.",
                    valid_at=episode.effective_reference_at,
                )
                await backend.put_atom(
                    atom,
                    AtomEvidence(
                        atom_id=atom.id,
                        episode_id=episode.id,
                        quote="Alice remembers MAGI.",
                    ),
                )

                overview = await backend.memory_overview()
                self.assertEqual(overview["episodes"], 1)
                self.assertEqual(overview["atoms"], 1)
                self.assertEqual(overview["evidence"], 1)
                self.assertEqual(overview["entities"], 1)

                episode_page = await backend.list_episodes(query="Alice")
                self.assertEqual(episode_page["total"], 1)
                self.assertEqual(episode_page["items"][0]["atom_count"], 1)
                episode_view = await backend.get_episode_memory(episode.id)
                self.assertEqual(episode_view["atoms"][0]["atom_id"], atom.id)

                atom_page = await backend.list_atoms(
                    owner_type="entity", temporal_status="active"
                )
                self.assertEqual(atom_page["total"], 1)
                self.assertEqual(atom_page["items"][0]["owner_name"], "Alice")
                atom_view = await backend.get_atom_memory_view(atom.id)
                self.assertEqual(atom_view["evidence"][0]["episode_id"], episode.id)
                await backend.finalize()

        asyncio.run(scenario())

    def test_atom_memory_has_many_episode_evidence_rows(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await backend.initialize()
                first = Episode(id="episode-first", content="Alice is here.")
                second = Episode(id="episode-second", content="Alice is still here.")
                await backend.put_episode(first)
                await backend.put_episode(second)
                atom = AtomRecord(
                    id="atom-shared",
                    workspace_id="workspace-a",
                    owner_id="entity-alice",
                    content="Alice is here.",
                    valid_at=first.effective_reference_at,
                )
                await backend.put_atom(
                    atom,
                    AtomEvidence(atom_id=atom.id, episode_id=first.id),
                )
                _, inserted = await backend.put_atom(
                    atom,
                    AtomEvidence(atom_id=atom.id, episode_id=second.id),
                )
                self.assertFalse(inserted)
                memory = await backend.get_atom_memory(atom.id)
                self.assertEqual(memory.episode_ids, (first.id, second.id))
                self.assertEqual(memory.atom.support_count, 2)

                deletion = await backend.backup_and_delete_episode(first.id)
                remaining = await backend.get_atom_memory(atom.id)
                self.assertEqual(remaining.episode_ids, (second.id,))
                self.assertEqual(remaining.atom.support_count, 1)
                await backend.restore_episode_backup(deletion["backup_id"])
                restored = await backend.get_atom_memory(atom.id)
                self.assertEqual(restored.episode_ids, (first.id, second.id))
                self.assertEqual(restored.atom.support_count, 2)
                await backend.finalize()

        asyncio.run(scenario())

    def test_relation_workbench_view_exposes_endpoints_and_atoms(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "magi-memory.db", "workspace-a"
                )
                await backend.initialize()
                alice = EntityRecord(
                    id="entity-alice",
                    workspace_id="workspace-a",
                    canonical_name="Alice",
                )
                bob = EntityRecord(
                    id="entity-bob",
                    workspace_id="workspace-a",
                    canonical_name="Bob",
                )
                await backend.put_entity(alice)
                await backend.put_entity(bob)
                episode = Episode(
                    id="episode-relation", content="Alice collaborates with Bob."
                )
                await backend.put_episode(episode)
                relation = RelationRecord(
                    id="relation-alice-bob",
                    workspace_id="workspace-a",
                    entity_a_id=alice.id,
                    entity_b_id=bob.id,
                    entity_a_name=alice.canonical_name,
                    entity_b_name=bob.canonical_name,
                    keywords=("collaborates",),
                )
                await backend.put_relation(relation)
                atom = AtomRecord(
                    id="atom-relation",
                    workspace_id="workspace-a",
                    owner_id=relation.id,
                    content="Alice collaborates with Bob.",
                    valid_at=datetime.now(timezone.utc),
                    subject_entity_id=alice.id,
                    object_entity_id=bob.id,
                )
                await backend.put_atom(
                    atom,
                    AtomEvidence(atom_id=atom.id, episode_id=episode.id),
                )

                page = await backend.list_relations(query="collaborates")
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["items"][0]["atom_count"], 1)
                detail = await backend.get_relation_memory_view(relation.id)
                self.assertEqual(
                    [endpoint["canonical_name"] for endpoint in detail["endpoints"]],
                    ["Alice", "Bob"],
                )
                self.assertEqual(detail["atoms"][0]["atom_id"], atom.id)
                await backend.finalize()

        asyncio.run(scenario())

    def test_entity_alias_ambiguity_and_manual_atom_conflict_resolution(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "magi-memory.db", "workspace-a"
                )
                await backend.initialize()
                episode = Episode(id="episode-conflict", content="Conflicting facts")
                await backend.put_episode(episode)
                first_entity = EntityRecord(
                    id="entity-alice-researcher",
                    workspace_id="workspace-a",
                    canonical_name="Alice Chen",
                    aliases=("Alice",),
                    entity_type="PERSON",
                )
                second_entity = EntityRecord(
                    id="entity-alice-author",
                    workspace_id="workspace-a",
                    canonical_name="Alice Smith",
                    aliases=("Alice",),
                    entity_type="PERSON",
                )
                await backend.put_entity(first_entity)
                await backend.put_entity(second_entity)
                await backend.put_entity(
                    EntityRecord(
                        id="entity-aaron",
                        workspace_id="workspace-a",
                        canonical_name="Aaron",
                        entity_type="PERSON",
                    )
                )
                first_atom = AtomRecord(
                    id="atom-prefers-tea",
                    workspace_id="workspace-a",
                    owner_id=first_entity.id,
                    content="Alice prefers tea.",
                    valid_at=episode.effective_reference_at,
                )
                second_atom = AtomRecord(
                    id="atom-dislikes-tea",
                    workspace_id="workspace-a",
                    owner_id=first_entity.id,
                    content="Alice dislikes tea.",
                    valid_at=episode.effective_reference_at,
                )
                await backend.put_atom(
                    first_atom,
                    AtomEvidence(
                        atom_id=first_atom.id,
                        episode_id=episode.id,
                    ),
                )
                await backend.put_atom(
                    second_atom,
                    AtomEvidence(
                        atom_id=second_atom.id,
                        episode_id=episode.id,
                    ),
                )
                await backend.add_atom_evolution(
                    second_atom.id,
                    first_atom.id,
                    "CONTRADICTION",
                    metadata={"reason": "overlapping claims"},
                )

                prioritized_atoms = await backend.list_atoms()
                self.assertEqual(
                    prioritized_atoms["items"][0]["unresolved_conflict_count"],
                    1,
                )
                prioritized_entities = await backend.list_entities()
                self.assertIn(
                    prioritized_entities["items"][0]["entity_id"],
                    {first_entity.id, second_entity.id},
                )

                overview = await backend.memory_overview()
                self.assertEqual(
                    overview["time_bounds"]["episode_reference"]["min"],
                    episode.effective_reference_at.isoformat(),
                )
                self.assertEqual(
                    (
                        await backend.list_episodes(
                            reference_to=(
                                episode.effective_reference_at - timedelta(days=1)
                            ).isoformat()
                        )
                    )["total"],
                    0,
                )
                evolution_page = await backend.list_atoms(evolution_type="any")
                self.assertEqual(evolution_page["total"], 2)
                self.assertEqual(evolution_page["items"][0]["evolution_count"], 1)
                self.assertEqual(
                    (
                        await backend.list_atoms(
                            valid_time_from=(
                                episode.effective_reference_at + timedelta(days=1)
                            ).isoformat()
                        )
                    )["total"],
                    0,
                )
                earliest_created = min(first_atom.created_at, second_atom.created_at)
                self.assertEqual(
                    (
                        await backend.list_atoms(
                            system_time_to=(
                                earliest_created - timedelta(days=1)
                            ).isoformat()
                        )
                    )["total"],
                    0,
                )

                entity_page = await backend.list_entities(query="Alice")
                self.assertEqual(entity_page["total"], 2)
                researcher = next(
                    item
                    for item in entity_page["items"]
                    if item["entity_id"] == first_entity.id
                )
                self.assertEqual(researcher["ambiguity_count"], 1)
                entity_view = await backend.get_entity_memory_view(first_entity.id)
                self.assertEqual(entity_view["alias_ambiguities"][0]["alias"], "Alice")
                self.assertEqual(
                    entity_view["alias_ambiguities"][0]["candidates"][0][
                        "entity_id"
                    ],
                    second_entity.id,
                )
                self.assertEqual(entity_view["atom_count"], 2)
                resolved_entity = await backend.resolve_entity_alias(
                    "Alice", first_entity.id
                )
                self.assertEqual(resolved_entity["alias_ambiguities"], [])
                reordered_entities = await backend.list_entities()
                self.assertEqual(
                    reordered_entities["items"][0]["entity_id"], "entity-aaron"
                )
                losing_entity = await backend.get_entity(second_entity.id)
                self.assertNotIn("Alice", losing_entity.aliases)

                duplicate_aaron = EntityRecord(
                    id="entity-aaron-legacy-duplicate",
                    workspace_id="workspace-a",
                    canonical_name="Aaron",
                    aliases=("A. Aaron",),
                    entity_type="PERSON",
                )
                await backend.put_entity(duplicate_aaron)
                duplicate_atom = AtomRecord(
                    id="atom-aaron-legacy-duplicate",
                    workspace_id="workspace-a",
                    owner_id=duplicate_aaron.id,
                    content="Aaron has a legacy duplicate record.",
                    valid_at=episode.effective_reference_at,
                )
                await backend.put_atom(
                    duplicate_atom,
                    AtomEvidence(
                        atom_id=duplicate_atom.id,
                        episode_id=episode.id,
                    ),
                )
                merged_aaron = await backend.resolve_entity_alias(
                    "Aaron", "entity-aaron"
                )
                self.assertEqual(merged_aaron["alias_ambiguities"], [])
                self.assertEqual(merged_aaron["atom_count"], 1)
                migrated_atom = await backend.get_atom(duplicate_atom.id)
                self.assertEqual(migrated_atom.owner_id, "entity-aaron")
                expired_duplicate = await backend.get_entity(duplicate_aaron.id)
                self.assertIsNotNone(expired_duplicate.expired_at)

                atom_view = await backend.get_atom_memory_view(second_atom.id)
                self.assertEqual(atom_view["evolutions"][0]["relation_type"], "CONTRADICTION")
                self.assertFalse(atom_view["evolutions"][0]["resolved"])
                self.assertEqual(
                    atom_view["evolutions"][0]["related_atom"]["atom_id"],
                    first_atom.id,
                )

                resolution = await backend.resolve_atom_conflict(
                    second_atom.id,
                    first_atom.id,
                    second_atom.id,
                    note="Operator retained the newer evidence.",
                )
                self.assertEqual(resolution["winner_atom_id"], second_atom.id)
                self.assertEqual(resolution["retired_atom_id"], first_atom.id)
                retired = await backend.get_atom(first_atom.id)
                self.assertIsNotNone(retired.expired_at)
                resolved_view = await backend.get_atom_memory_view(second_atom.id)
                evolution = resolved_view["evolutions"][0]
                self.assertTrue(evolution["resolved"])
                self.assertEqual(evolution["metadata"]["winner_atom_id"], second_atom.id)
                self.assertEqual(
                    evolution["metadata"]["resolution_note"],
                    "Operator retained the newer evidence.",
                )
                reordered_atoms = await backend.list_atoms()
                self.assertTrue(
                    all(
                        item["unresolved_conflict_count"] == 0
                        for item in reordered_atoms["items"]
                    )
                )
                await backend.finalize()

        asyncio.run(scenario())

    def test_same_episode_chunk_evidence_has_stable_distinct_identity(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await backend.initialize()
                episode = Episode(
                    id="episode-multi-chunk",
                    content="The same fact appeared in two chunks.",
                )
                await backend.put_episode(episode)
                atom = AtomRecord(
                    id="atom-multi-chunk",
                    workspace_id="workspace-a",
                    owner_id="entity-alice",
                    content="Alice likes tea.",
                    valid_at=None,
                )
                await backend.put_atom(
                    atom,
                    AtomEvidence(
                        atom_id=atom.id,
                        episode_id=episode.id,
                        quote="Alice likes tea.",
                        extraction_revision="chunk-a",
                    ),
                )
                chunk_b = AtomEvidence(
                    atom_id=atom.id,
                    episode_id=episode.id,
                    quote="Alice enjoys tea.",
                    extraction_revision="chunk-b",
                )
                await backend.add_atom_evidence(atom.id, chunk_b)
                await backend.add_atom_evidence(atom.id, chunk_b)

                memory = await backend.get_atom_memory(atom.id)
                self.assertEqual(len(memory.evidence), 2)
                self.assertEqual(
                    {item.extraction_revision for item in memory.evidence},
                    {"chunk-a", "chunk-b"},
                )
                self.assertEqual(memory.atom.support_count, 1)

                deletion = await backend.backup_and_delete_episode(episode.id)
                await backend.restore_episode_backup(deletion["backup_id"])
                restored = await backend.get_atom_memory(atom.id)
                self.assertEqual(len(restored.evidence), 2)
                await backend.finalize()

        asyncio.run(scenario())

    def test_hard_delete_removes_orphan_projection_owners_and_restores_them(
        self,
    ) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(Path(temporary) / "memory.db", "workspace-a")
                await backend.initialize()
                episode = Episode(id="episode-delete", content="Alice knows Bob.")
                await backend.put_episode(episode)

                alice_id = stable_entity_id("workspace-a", "Alice")
                bob_id = stable_entity_id("workspace-a", "Bob")
                for entity_id, name in ((alice_id, "Alice"), (bob_id, "Bob")):
                    await backend.put_entity(
                        EntityRecord(
                            id=entity_id,
                            workspace_id="workspace-a",
                            canonical_name=name,
                        )
                    )

                relation_id = stable_relation_id("workspace-a", alice_id, bob_id)
                await backend.put_relation(
                    RelationRecord(
                        id=relation_id,
                        workspace_id="workspace-a",
                        entity_a_id=alice_id,
                        entity_b_id=bob_id,
                        entity_a_name="Alice",
                        entity_b_name="Bob",
                    )
                )
                atoms = (
                    AtomRecord(
                        id="atom-alice",
                        workspace_id="workspace-a",
                        owner_id=alice_id,
                        content="Alice exists.",
                        valid_at=None,
                    ),
                    AtomRecord(
                        id="atom-relation",
                        workspace_id="workspace-a",
                        owner_id=relation_id,
                        content="Alice knows Bob.",
                        subject_entity_id=alice_id,
                        predicate="knows",
                        object_entity_id=bob_id,
                        valid_at=None,
                    ),
                )
                for atom in atoms:
                    await backend.put_atom(
                        atom,
                        AtomEvidence(atom_id=atom.id, episode_id=episode.id),
                    )

                for owner_id, atom_id in (
                    (alice_id, "atom-alice"),
                    (relation_id, "atom-relation"),
                ):
                    created = await backend.compare_and_set_owner_summary_checkpoint(
                        owner_id,
                        expected_revision=None,
                        checkpoint_summary=f"summary for {owner_id}",
                        covered_atom_ids=[atom_id],
                        pending_atom_ids=[],
                        prompt_version="test-v1",
                        model_identity="test-model",
                        incremental_compaction_count=0,
                        last_compacted_at=None,
                        reason="test",
                    )
                    self.assertTrue(created)

                before = await backend.memory_overview()
                self.assertEqual(before["entities"], 2)
                self.assertEqual(before["relations"], 1)

                deletion = await backend.backup_and_delete_episode(episode.id)
                after = await backend.memory_overview()
                self.assertEqual(after["episodes"], 0)
                self.assertEqual(after["atoms"], 0)
                self.assertEqual(after["evidence"], 0)
                self.assertEqual(after["entities"], 0)
                self.assertEqual(after["relations"], 0)
                self.assertEqual(await backend.search_entity_names("Alice"), [])
                self.assertIsNone(
                    await backend.get_owner_summary_checkpoint(alice_id)
                )
                self.assertIsNone(
                    await backend.get_owner_summary_checkpoint(relation_id)
                )

                await backend.restore_episode_backup(deletion["backup_id"])
                restored = await backend.memory_overview()
                self.assertEqual(restored["episodes"], 1)
                self.assertEqual(restored["atoms"], 2)
                self.assertEqual(restored["entities"], 2)
                self.assertEqual(restored["relations"], 1)
                # Checkpoints are derived materializations. Restore rebuilds
                # them from authoritative Atom records instead of reviving a
                # summary that may no longer match the active model/prompt.
                self.assertIsNone(
                    await backend.get_owner_summary_checkpoint(alice_id)
                )
                self.assertIsNone(
                    await backend.get_owner_summary_checkpoint(relation_id)
                )
                await backend.finalize()

        asyncio.run(scenario())

    def test_initialize_repairs_legacy_orphan_projection_owners(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "memory.db"
                backend = SQLiteBackend(path, "workspace-a")
                await backend.initialize()
                alice_id = stable_entity_id("workspace-a", "Alice")
                bob_id = stable_entity_id("workspace-a", "Bob")
                for entity_id, name in ((alice_id, "Alice"), (bob_id, "Bob")):
                    await backend.put_entity(
                        EntityRecord(
                            id=entity_id,
                            workspace_id="workspace-a",
                            canonical_name=name,
                        )
                    )
                await backend.put_relation(
                    RelationRecord(
                        id=stable_relation_id("workspace-a", alice_id, bob_id),
                        workspace_id="workspace-a",
                        entity_a_id=alice_id,
                        entity_b_id=bob_id,
                        entity_a_name="Alice",
                        entity_b_name="Bob",
                    )
                )
                await backend.finalize()

                reopened = SQLiteBackend(path, "workspace-a")
                await reopened.initialize()
                overview = await reopened.memory_overview()
                self.assertEqual(overview["entities"], 0)
                self.assertEqual(overview["relations"], 0)
                self.assertEqual(await reopened.search_entity_names("Alice"), [])
                await reopened.finalize()

        asyncio.run(scenario())

    def test_domain_timestamps_are_fixed_and_normalized_to_utc(self) -> None:
        episode = Episode(id="episode-time", content="A fact.")
        first = episode.effective_reference_at
        second = episode.effective_reference_at
        self.assertEqual(first, second)
        self.assertEqual(first.tzinfo, timezone.utc)

        atom = AtomRecord(
            id="atom-time",
            workspace_id="workspace-a",
            owner_id="entity-a",
            content="A timed fact.",
            valid_at=datetime(2026, 8, 5, 8, 0, tzinfo=timezone(timedelta(hours=8))),
        )
        self.assertEqual(
            atom.valid_at,
            datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc),
        )

    def test_atom_temporal_presentation_and_shared_embedding_index(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await backend.initialize()
                reference = datetime(2026, 8, 5, tzinfo=timezone.utc)
                episode = Episode(
                    id="episode-a",
                    content="Alice joined MAGI.",
                    reference_at=reference,
                )
                await backend.put_episode(episode)
                entity_id = stable_entity_id("workspace-a", "Alice")
                await backend.put_entity(
                    EntityRecord(
                        id=entity_id,
                        workspace_id="workspace-a",
                        canonical_name="Alice",
                        aliases=("Alice", "A. Example"),
                    )
                )
                bob_id = stable_entity_id("workspace-a", "Bob")
                await backend.put_entity(
                    EntityRecord(
                        id=bob_id,
                        workspace_id="workspace-a",
                        canonical_name="Bob",
                    )
                )
                relation_id = stable_relation_id("workspace-a", entity_id, bob_id)
                relation = await backend.put_relation(
                    RelationRecord(
                        id=relation_id,
                        workspace_id="workspace-a",
                        entity_a_id=entity_id,
                        entity_b_id=bob_id,
                        entity_a_name="Alice",
                        entity_b_name="Bob",
                    )
                )
                self.assertEqual(
                    {relation.entity_a_name, relation.entity_b_name},
                    {"Alice", "Bob"},
                )
                self.assertEqual(
                    (await backend.find_entity_exact("a. example")).id,
                    entity_id,
                )

                atom = AtomRecord(
                    id="atom-a",
                    workspace_id="workspace-a",
                    owner_id=entity_id,
                    content="Alice joined MAGI.",
                    valid_at=reference,
                )
                stored, inserted = await backend.put_atom(
                    atom,
                    AtomEvidence(atom_id=atom.id, episode_id=episode.id),
                )
                self.assertTrue(inserted)
                aggregate = await backend.get_atom_memory(stored.id)
                self.assertEqual(aggregate.episode_ids, (episode.id,))
                self.assertEqual(
                    stored.temporal_status(reference), TemporalStatus.ACTIVE
                )
                self.assertIn("status=active", stored.presentation(reference))

                old_fingerprint = stored.fingerprint
                invalid_at = reference + timedelta(days=1)
                await backend.set_atom_invalid_at(stored.id, invalid_at=invalid_at)
                invalidated = await backend.get_atom(stored.id)
                self.assertEqual(invalidated.invalid_at, invalid_at)
                self.assertIsNone(
                    await backend.find_atom_by_fingerprint(old_fingerprint)
                )
                self.assertEqual(
                    (
                        await backend.find_atom_by_fingerprint(invalidated.fingerprint)
                    ).id,
                    stored.id,
                )

                await backend.put_embedding(
                    object_kind="entity_name",
                    object_id=stable_entity_name_embedding_id(entity_id, "Alice"),
                    owner_id=entity_id,
                    source_text="Alice",
                    vector=np.asarray([1.0, 0.0], dtype=np.float32),
                    model_name="test-2d",
                )
                await backend.put_embedding(
                    object_kind="entity_name",
                    object_id=stable_entity_name_embedding_id(entity_id, "A. Example"),
                    owner_id=entity_id,
                    source_text="A. Example",
                    vector=np.asarray([0.0, 1.0], dtype=np.float32),
                    model_name="test-2d",
                )
                await backend.put_embedding(
                    object_kind="atom",
                    object_id=stored.id,
                    owner_id=entity_id,
                    source_text=stored.content,
                    vector=np.asarray([0.0, 1.0], dtype=np.float32),
                    model_name="test-2d",
                )
                entity_matches = await backend.search_embeddings(
                    object_kind="entity_name",
                    query_vector=np.asarray([1.0, 0.0], dtype=np.float32),
                    model_name="test-2d",
                )
                atom_matches = await backend.search_embeddings(
                    object_kind="atom",
                    owner_id=entity_id,
                    query_vector=np.asarray([0.0, 1.0], dtype=np.float32),
                    model_name="test-2d",
                )
                self.assertEqual(entity_matches[0].object_id, entity_id)
                alias_matches = await backend.search_embeddings(
                    object_kind="entity_name",
                    query_vector=np.asarray([0.0, 1.0], dtype=np.float32),
                    model_name="test-2d",
                )
                self.assertEqual(alias_matches[0].object_id, entity_id)
                self.assertEqual(atom_matches[0].object_id, stored.id)

                deletion = await backend.backup_and_delete_episode(episode.id)
                self.assertIsNotNone(deletion)
                self.assertIsNone(await backend.get_atom(stored.id))
                await backend.restore_episode_backup(deletion["backup_id"])
                restored = await backend.get_atom_memory(stored.id)
                self.assertEqual(restored.episode_ids, (episode.id,))
                await backend.finalize()

        asyncio.run(scenario())

    def test_adapter_mirrors_episode_lifecycle(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(
                    Path(temporary) / "memory.sqlite3", "workspace-a"
                )
                await backend.initialize()
                adapter = MagiKnowledgeAdapter(backend)
                await adapter.prepare_episode(
                    doc_id="episode-webui",
                    content="Alice joined MAGI.",
                    file_path="episode.txt",
                    reference_at="2026-08-05T00:00:00+00:00",
                )
                staged = await backend.get_episode("episode-webui")
                self.assertEqual(staged["status"], EpisodeStatus.PENDING.value)
                self.assertEqual(
                    adapter.get_extraction_context("episode-webui")["reference_at"],
                    "2026-08-05T00:00:00+00:00",
                )

                await adapter.complete_episode("episode-webui", track_id="track-webui")
                completed = await backend.get_episode("episode-webui")
                self.assertEqual(completed["status"], EpisodeStatus.INDEXED.value)
                self.assertEqual(completed["track_id"], "track-webui")
                await adapter.fail_episode("episode-webui", error="forced failure")
                failed = await backend.get_episode("episode-webui")
                self.assertEqual(failed["status"], EpisodeStatus.FAILED.value)
                self.assertEqual(failed["error"], "forced failure")
                await backend.finalize()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
