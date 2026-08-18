from __future__ import annotations

from typing import Any

import pytest

from magi_core import MagiCore
from magi_core.memory import ExtractedAtom, ExtractedEntity, ExtractedMemory, Episode


class _ContextOnlyAdapter:
    def __init__(self) -> None:
        self.registered: list[Episode] = []

    def register_episodes(self, episodes: list[Episode]) -> None:
        self.registered.extend(episodes)

    async def stage_episode(self, episode: Episode) -> None:
        raise AssertionError("Episode must not be persisted before admission")


class _CapturingCore:
    def __init__(self) -> None:
        self._knowledge_ingestion_adapter = _ContextOnlyAdapter()
        self.enqueue: dict[str, Any] | None = None
        self.processed = False

    async def apipeline_enqueue_documents(
        self,
        inputs: list[str],
        ids: list[str],
        file_paths: list[str],
        track_id: str,
        **kwargs: Any,
    ) -> None:
        self.enqueue = {
            "inputs": inputs,
            "ids": ids,
            "file_paths": file_paths,
            "track_id": track_id,
            **kwargs,
        }

    async def apipeline_process_enqueue_documents(self) -> None:
        self.processed = True


@pytest.mark.asyncio
async def test_extracted_source_uri_is_not_pipeline_document_identity() -> None:
    core = _CapturingCore()
    episode = Episode(
        content="Alice prefers concise reports.",
        source_uri="dsh-session:daily-chat",
    )
    memory = ExtractedMemory.create(
        episode=episode,
        entities=(
            ExtractedEntity(
                "Alice",
                (ExtractedAtom("Alice prefers concise reports."),),
            ),
        ),
    )

    track_id = await MagiCore.aingest_extracted(core, memory)  # type: ignore[arg-type]

    assert core._knowledge_ingestion_adapter.registered == [episode]
    assert core.enqueue is not None
    assert core.enqueue["ids"] == [episode.id]
    assert core.enqueue["file_paths"] == [f"episode-{episode.id}.txt"]
    assert core.enqueue["file_paths"] != [episode.source_uri]
    assert core.enqueue["track_id"] == track_id
    assert core.processed
