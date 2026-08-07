from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import (
    AtomClassification,
    AtomDecision,
    AtomEvidence,
    AtomRecord,
    Episode,
)
from magi_core.memory.adapter import MagiKnowledgeAdapter


class FakeEmbedding:
    embedding_dim = 3
    model_name = "fake-3d"

    async def __call__(self, texts, **kwargs):
        return np.ones((len(texts), 3), dtype=np.float32)


class FakeEngine:
    embedding_func = FakeEmbedding()


class AtomResolutionApplicationTests(unittest.TestCase):
    def test_duplicate_reuses_atom_and_only_adds_evidence(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(Path(temporary) / "memory.db", "workspace-a")
                await backend.initialize()
                reference = datetime(2026, 8, 5, tzinfo=timezone.utc)
                for episode_id in ("episode-old", "episode-new"):
                    await backend.put_episode(
                        Episode(id=episode_id, content=f"content {episode_id}")
                    )
                old = AtomRecord(
                    id="atom-old",
                    workspace_id="workspace-a",
                    owner_id="entity-a",
                    content="Alice joined MAGI.",
                    valid_at=reference,
                )
                await backend.put_atom(
                    old, AtomEvidence(atom_id=old.id, episode_id="episode-old")
                )
                adapter = MagiKnowledgeAdapter(backend)
                stored = await adapter._apply_atom_resolution(
                    engine=FakeEngine(),
                    atom=AtomRecord(
                        id="atom-new",
                        workspace_id="workspace-a",
                        owner_id="entity-a",
                        content="Alice joined MAGI.",
                        valid_at=reference + timedelta(days=1),
                    ),
                    vector=np.ones(3, dtype=np.float32),
                    evidence=AtomEvidence(atom_id="atom-new", episode_id="episode-new"),
                    resolution=AtomClassification(
                        decision=AtomDecision.DUPLICATE,
                        matched_atom_id=old.id,
                    ),
                )
                self.assertEqual(stored.id, old.id)
                self.assertEqual(stored.support_count, 2)
                self.assertIsNone(await backend.get_atom("atom-new"))
                await backend.finalize()

        asyncio.run(scenario())

    def test_refinement_expires_old_system_revision_without_invalidating_fact(
        self,
    ) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(Path(temporary) / "memory.db", "workspace-a")
                await backend.initialize()
                await backend.put_episode(Episode(id="episode-old", content="old"))
                await backend.put_episode(Episode(id="episode-new", content="new"))
                reference = datetime(2026, 8, 5, tzinfo=timezone.utc)
                old = AtomRecord(
                    id="atom-old",
                    workspace_id="workspace-a",
                    owner_id="entity-a",
                    content="Alice works in research.",
                    valid_at=reference,
                )
                await backend.put_atom(
                    old, AtomEvidence(atom_id=old.id, episode_id="episode-old")
                )
                adapter = MagiKnowledgeAdapter(backend)
                await adapter._apply_atom_resolution(
                    engine=FakeEngine(),
                    atom=AtomRecord(
                        id="atom-new",
                        workspace_id="workspace-a",
                        owner_id="entity-a",
                        content="Alice researches temporal knowledge graphs.",
                        valid_at=reference,
                    ),
                    vector=np.ones(3, dtype=np.float32),
                    evidence=AtomEvidence(atom_id="atom-new", episode_id="episode-new"),
                    resolution=AtomClassification(
                        decision=AtomDecision.REFINEMENT,
                        matched_atom_id=old.id,
                    ),
                )
                updated = await backend.get_atom(old.id)
                self.assertIsNotNone(updated.expired_at)
                self.assertIsNone(updated.invalid_at)
                evolution = await backend.list_atom_evolutions("atom-new")
                self.assertEqual(evolution[0]["source_atom_id"], "atom-new")
                self.assertEqual(evolution[0]["target_atom_id"], "atom-old")
                self.assertEqual(evolution[0]["relation_type"], "REFINEMENT")
                await backend.finalize()

        asyncio.run(scenario())

    def test_temporal_successor_ends_old_validity_and_system_revision(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(Path(temporary) / "memory.db", "workspace-a")
                await backend.initialize()
                await backend.put_episode(Episode(id="episode-old", content="old"))
                await backend.put_episode(Episode(id="episode-new", content="new"))
                old_time = datetime(2026, 8, 1, tzinfo=timezone.utc)
                transition = datetime(2026, 8, 7, tzinfo=timezone.utc)
                old = AtomRecord(
                    id="atom-old",
                    workspace_id="workspace-a",
                    owner_id="entity-a",
                    content="Alice lives in Tokyo.",
                    valid_at=old_time,
                )
                await backend.put_atom(
                    old, AtomEvidence(atom_id=old.id, episode_id="episode-old")
                )
                adapter = MagiKnowledgeAdapter(backend)
                await adapter._apply_atom_resolution(
                    engine=FakeEngine(),
                    atom=AtomRecord(
                        id="atom-new",
                        workspace_id="workspace-a",
                        owner_id="entity-a",
                        content="Alice lives in Kyoto.",
                        valid_at=transition,
                    ),
                    vector=np.ones(3, dtype=np.float32),
                    evidence=AtomEvidence(atom_id="atom-new", episode_id="episode-new"),
                    resolution=AtomClassification(
                        decision=AtomDecision.TEMPORAL_SUCCESSOR,
                        matched_atom_id=old.id,
                        target_invalid_at=transition,
                    ),
                )
                updated = await backend.get_atom(old.id)
                self.assertEqual(updated.invalid_at, transition)
                self.assertIsNotNone(updated.expired_at)
                await backend.finalize()

        asyncio.run(scenario())

    def test_contradiction_preserves_old_atom_by_default(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                backend = SQLiteBackend(Path(temporary) / "memory.db", "workspace-a")
                await backend.initialize()
                await backend.put_episode(Episode(id="episode-old", content="old"))
                await backend.put_episode(Episode(id="episode-new", content="new"))
                reference = datetime(2026, 8, 5, tzinfo=timezone.utc)
                old = AtomRecord(
                    id="atom-old",
                    workspace_id="workspace-a",
                    owner_id="entity-a",
                    content="Alice prefers tea.",
                    valid_at=reference,
                )
                await backend.put_atom(
                    old, AtomEvidence(atom_id=old.id, episode_id="episode-old")
                )
                adapter = MagiKnowledgeAdapter(backend)
                await adapter._apply_atom_resolution(
                    engine=FakeEngine(),
                    atom=AtomRecord(
                        id="atom-new",
                        workspace_id="workspace-a",
                        owner_id="entity-a",
                        content="Alice dislikes tea.",
                        valid_at=reference,
                    ),
                    vector=np.ones(3, dtype=np.float32),
                    evidence=AtomEvidence(atom_id="atom-new", episode_id="episode-new"),
                    resolution=AtomClassification(
                        decision=AtomDecision.CONTRADICTION,
                        matched_atom_id=old.id,
                    ),
                )
                updated = await backend.get_atom(old.id)
                self.assertIsNone(updated.invalid_at)
                self.assertIsNone(updated.expired_at)
                self.assertEqual(
                    (await backend.list_atom_evolutions("atom-new"))[0][
                        "relation_type"
                    ],
                    "CONTRADICTION",
                )
                await backend.finalize()

        asyncio.run(scenario())
