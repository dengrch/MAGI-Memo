#!/usr/bin/env python3
"""Seed deterministic, AI-free Atom evolution examples into one workspace."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import (
    AtomEvidence,
    AtomRecord,
    EntityRecord,
    Episode,
    EpisodeStatus,
    stable_entity_id,
)


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def resolve_workspace(repo_root: Path, requested: str | None) -> tuple[str, Path]:
    registry = json.loads((repo_root / "mgc-test" / "workspaces.json").read_text())
    workspace_id = requested or str(registry["active_workspace_id"])
    workspace = next(
        (item for item in registry["workspaces"] if item["id"] == workspace_id),
        None,
    )
    if workspace is None:
        raise ValueError(f"unknown workspace {workspace_id!r}")
    return workspace_id, Path(workspace["root"]) / "ragstore" / "magi-memory.db"


async def seed(repo_root: Path, requested_workspace: str | None) -> None:
    workspace_id, database_path = resolve_workspace(repo_root, requested_workspace)
    backend = SQLiteBackend(database_path, workspace_id)
    await backend.initialize()
    try:
        entity_id = stable_entity_id(workspace_id, "MAGI Evolution QA Fixture")
        await backend.put_entity(
            EntityRecord(
                id=entity_id,
                workspace_id=workspace_id,
                canonical_name="MAGI Evolution QA Fixture",
                aliases=("演化关系人工验证样例",),
                entity_type="TEST_FIXTURE",
            )
        )
        episode = Episode(
            id="episode-manual-evolution-fixtures-v1",
            content=(
                "人工构造的 Memory Core 演化关系验证记录，不代表真实世界事实。\n"
                "状态先为草稿，随后转为已发布；同一时段另有互不相容的校验结果。"
            ),
            reference_at=utc("2026-09-03T00:00:00"),
            source_uri="manual://memory-core/evolution-fixtures/v1",
            metadata={"manual_seed": True, "purpose": "memory-core-ui-validation"},
        )
        await backend.put_episode(episode)
        await backend.mark_episode(episode.id, EpisodeStatus.INDEXED)

        atoms = (
            AtomRecord(
                id="atom-manual-state-draft-v1",
                workspace_id=workspace_id,
                owner_id=entity_id,
                content="人工验证样例在2025年1月1日至2026年1月1日期间处于草稿状态。",
                valid_at=utc("2025-01-01T00:00:00"),
                invalid_at=utc("2026-01-01T00:00:00"),
                created_at=utc("2025-01-01T00:00:00"),
                expired_at=utc("2026-01-01T00:00:01"),
                temporal_text="2025-01-01 to 2026-01-01",
                temporal_precision="day",
                confidence=1.0,
            ),
            AtomRecord(
                id="atom-manual-state-published-v1",
                workspace_id=workspace_id,
                owner_id=entity_id,
                content="人工验证样例自2026年1月1日起处于已发布状态。",
                valid_at=utc("2026-01-01T00:00:00"),
                created_at=utc("2026-01-01T00:00:01"),
                temporal_text="since 2026-01-01",
                temporal_precision="day",
                confidence=1.0,
            ),
            AtomRecord(
                id="atom-manual-validation-pass-v1",
                workspace_id=workspace_id,
                owner_id=entity_id,
                content="人工验证样例在2026年2月1日的校验结果为通过。",
                valid_at=utc("2026-02-01T00:00:00"),
                created_at=utc("2026-02-01T00:00:01"),
                temporal_text="2026-02-01",
                temporal_precision="day",
                confidence=1.0,
            ),
            AtomRecord(
                id="atom-manual-validation-fail-v1",
                workspace_id=workspace_id,
                owner_id=entity_id,
                content="人工验证样例在2026年2月1日的校验结果为未通过。",
                valid_at=utc("2026-02-01T00:00:00"),
                created_at=utc("2026-02-01T00:00:02"),
                temporal_text="2026-02-01",
                temporal_precision="day",
                confidence=1.0,
            ),
        )
        for atom in atoms:
            await backend.put_atom(
                atom,
                AtomEvidence(
                    atom_id=atom.id,
                    episode_id=episode.id,
                    quote=atom.content,
                    extraction_revision="manual-evolution-fixture-v1",
                ),
            )
        await backend.add_atom_evolution(
            "atom-manual-state-published-v1",
            "atom-manual-state-draft-v1",
            "TEMPORAL_SUCCESSOR",
            metadata={
                "confidence": 1.0,
                "manual_seed": True,
                "reason": "Explicit state transition at 2026-01-01",
            },
        )
        await backend.add_atom_evolution(
            "atom-manual-validation-fail-v1",
            "atom-manual-validation-pass-v1",
            "CONTRADICTION",
            metadata={
                "confidence": 1.0,
                "manual_seed": True,
                "reason": "Mutually exclusive outcomes over the same valid-time point",
            },
        )
        print(
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "database_path": str(database_path),
                    "episode_id": episode.id,
                    "atom_ids": [atom.id for atom in atoms],
                    "evolutions": ["TEMPORAL_SUCCESSOR", "CONTRADICTION"],
                },
                ensure_ascii=False,
            )
        )
    finally:
        await backend.finalize()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    asyncio.run(seed(repo_root, args.workspace))


if __name__ == "__main__":
    main()
