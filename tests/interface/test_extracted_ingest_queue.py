from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from magi_core.backend.bundle import BackendBundle
from magi_core.backend.engine import EngineBackend
from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import (
    Episode,
    EpisodeStatus,
    ExtractedAtom,
    ExtractedEntity,
    ExtractedMemory,
)
from magi_core.workspace import WorkspaceLayout


class _QueuedDocStatus:
    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        self.status = "pending"

    async def get_by_id(self, doc_id: str) -> dict[str, str] | None:
        if doc_id != self.doc_id:
            return None
        return {"status": self.status, "track_id": "track-queued"}


class _QueuedRawEngine:
    default_llm_timeout = 1

    def __init__(self, doc_id: str) -> None:
        self.doc_status = _QueuedDocStatus(doc_id)
        self.track_id: str | None = None

    async def aingest_extracted(
        self,
        memories: list[ExtractedMemory],
        *,
        track_id: str | None = None,
    ) -> str:
        assert memories[0].episode.id == self.doc_status.doc_id
        self.track_id = track_id

        async def finish() -> None:
            await asyncio.sleep(0.01)
            self.doc_status.status = "processed"

        asyncio.create_task(finish())
        return track_id or "track-queued"


class _NoopEngine:
    async def ingest_extracted(self, memories: Any) -> None:
        raise AssertionError(f"duplicate replay was re-enqueued: {memories}")


def _memory(episode: Episode) -> ExtractedMemory:
    return ExtractedMemory.create(
        episode=episode,
        entities=(
            ExtractedEntity(
                "User",
                (ExtractedAtom("User recovered a backpack."),),
                entity_type="person",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_engine_waits_for_queued_extracted_episode(tmp_path: Path) -> None:
    episode = Episode(id="episode-queued", content="Recovered backpack.")
    raw = _QueuedRawEngine(episode.id)
    backend = EngineBackend(
        raw,
        WorkspaceLayout.from_root(tmp_path, workspace_id="queue-test"),
    )
    backend._initialized = True

    track_id = await backend.ingest_extracted([_memory(episode)])

    assert track_id == "track-queued"
    assert raw.doc_status.status == "processed"


@pytest.mark.asyncio
async def test_engine_preserves_caller_track_id_for_background_status(
    tmp_path: Path,
) -> None:
    episode = Episode(id="episode-background", content="Recovered backpack.")
    raw = _QueuedRawEngine(episode.id)
    backend = EngineBackend(
        raw,
        WorkspaceLayout.from_root(tmp_path, workspace_id="background-test"),
    )
    backend._initialized = True

    track_id = await backend.ingest_extracted(
        [_memory(episode)],
        track_id="ingest-extracted-background",
    )

    assert track_id == "ingest-extracted-background"
    assert raw.track_id == "ingest-extracted-background"


@pytest.mark.asyncio
async def test_extracted_content_replay_returns_existing_episode(
    tmp_path: Path,
) -> None:
    sqlite = SQLiteBackend(tmp_path / "magi-memory.db", "replay-test")
    await sqlite.initialize()
    existing = Episode(id="episode-original", content="Recovered backpack.")
    await sqlite.put_episode(existing)
    await sqlite.mark_episode(
        existing.id,
        EpisodeStatus.INDEXED,
        track_id="track-original",
    )
    bundle = BackendBundle(
        engine=_NoopEngine(),  # type: ignore[arg-type]
        sqlite=sqlite,
        neo4j=object(),  # type: ignore[arg-type]
    )
    bundle._initialized = True
    replay = Episode(id="episode-retry", content=existing.content)

    result = await bundle.ingest_extracted([_memory(replay)])

    assert result.episode_ids == (existing.id,)
    assert result.indexed_count == 0
    assert result.skipped_count == 1
    assert result.track_id is None
    assert await sqlite.get_episode(replay.id) is None
    await sqlite.finalize()
