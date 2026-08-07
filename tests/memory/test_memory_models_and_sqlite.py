from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from magi_core.backend.sqlite import SQLiteBackend
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
    def test_memory_workbench_views_are_paginated_and_evidence_aware(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(Path(temporary) / "magi-memory.db", "workspace-a")
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
            valid_at=datetime(
                2026, 8, 5, 8, 0, tzinfo=timezone(timedelta(hours=8))
            ),
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
                relation_id = stable_relation_id(
                    "workspace-a", entity_id, bob_id
                )
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
                self.assertEqual(stored.temporal_status(reference), TemporalStatus.ACTIVE)
                self.assertIn("status=active", stored.presentation(reference))

                old_fingerprint = stored.fingerprint
                invalid_at = reference + timedelta(days=1)
                await backend.set_atom_invalid_at(
                    stored.id, invalid_at=invalid_at
                )
                invalidated = await backend.get_atom(stored.id)
                self.assertEqual(invalidated.invalid_at, invalid_at)
                self.assertIsNone(
                    await backend.find_atom_by_fingerprint(old_fingerprint)
                )
                self.assertEqual(
                    (
                        await backend.find_atom_by_fingerprint(
                            invalidated.fingerprint
                        )
                    ).id,
                    stored.id,
                )

                await backend.put_embedding(
                    object_kind="entity_name",
                    object_id=stable_entity_name_embedding_id(
                        entity_id, "Alice"
                    ),
                    owner_id=entity_id,
                    source_text="Alice",
                    vector=np.asarray([1.0, 0.0], dtype=np.float32),
                    model_name="test-2d",
                )
                await backend.put_embedding(
                    object_kind="entity_name",
                    object_id=stable_entity_name_embedding_id(
                        entity_id, "A. Example"
                    ),
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
                    adapter.get_extraction_context("episode-webui")[
                        "reference_at"
                    ],
                    "2026-08-05T00:00:00+00:00",
                )

                await adapter.complete_episode(
                    "episode-webui", track_id="track-webui"
                )
                completed = await backend.get_episode("episode-webui")
                self.assertEqual(
                    completed["status"], EpisodeStatus.INDEXED.value
                )
                self.assertEqual(completed["track_id"], "track-webui")
                await adapter.fail_episode(
                    "episode-webui", error="forced failure"
                )
                failed = await backend.get_episode("episode-webui")
                self.assertEqual(failed["status"], EpisodeStatus.FAILED.value)
                self.assertEqual(failed["error"], "forced failure")
                await backend.finalize()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
