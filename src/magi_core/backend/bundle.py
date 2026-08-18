"""Coordinate the retrieval engine, SQLite, and Neo4j as one backend."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Sequence

from magi_core.backend.engine import EngineBackend
from magi_core.backend.neo4j import Neo4jBackend
from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import Episode, EpisodeStatus, ExtractedMemory


@dataclass(frozen=True, slots=True)
class IndexResult:
    episode_ids: tuple[str, ...]
    indexed_count: int
    skipped_count: int
    track_id: str | None


class BackendBundle:
    """One lifecycle boundary over all three backend components."""

    def __init__(
        self,
        *,
        engine: EngineBackend,
        sqlite: SQLiteBackend,
        neo4j: Neo4jBackend,
    ) -> None:
        self.engine = engine
        self.sqlite = sqlite
        self.neo4j = neo4j
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.sqlite.initialize()
        try:
            await self.engine.initialize()
            await self.neo4j.initialize()
            if self.engine.memory_adapter is not None:
                await self.engine.memory_adapter.start_projection_worker(
                    self.engine.raw
                )
        except Exception:
            try:
                if self.engine.memory_adapter is not None:
                    await self.engine.memory_adapter.stop_projection_worker()
                await self.engine.finalize()
            finally:
                await self.sqlite.finalize()
            raise
        self._initialized = True

    async def finalize(self) -> None:
        if not self._initialized:
            return
        try:
            if self.engine.memory_adapter is not None:
                await self.engine.memory_adapter.stop_projection_worker()
            await self.neo4j.finalize()
        finally:
            try:
                await self.engine.finalize()
            finally:
                await self.sqlite.finalize()
                self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("MAGI backend bundle is not initialized")

    async def index(self, episodes: Sequence[Episode]) -> IndexResult:
        self._require_initialized()
        pending: list[Episode] = []
        skipped = 0
        for episode in episodes:
            record = await self.sqlite.get_episode(episode.id)
            if record is not None and record["content_hash"] != episode.content_hash:
                raise ValueError(
                    f"episode {episode.id!r} already exists with different content"
                )
            if record is not None and record["status"] == EpisodeStatus.INDEXED.value:
                skipped += 1
            else:
                pending.append(episode)

        if not pending:
            return IndexResult(
                episode_ids=tuple(episode.id for episode in episodes),
                indexed_count=0,
                skipped_count=skipped,
                track_id=None,
            )

        try:
            track_id = await self.engine.index(pending)
        except Exception as exc:
            for episode in pending:
                if await self.sqlite.get_episode(episode.id) is not None:
                    await self.sqlite.mark_episode(
                        episode.id,
                        EpisodeStatus.FAILED,
                        error=str(exc),
                    )
            raise

        for episode in pending:
            # Legacy/custom engines may not invoke the knowledge adapter even
            # after reporting the canonical document as processed.  Persisting
            # here is safe because admission and processing already succeeded.
            if await self.sqlite.get_episode(episode.id) is None:
                await self.sqlite.put_episode(episode)
            await self.sqlite.mark_episode(
                episode.id,
                EpisodeStatus.INDEXED,
                track_id=track_id,
            )
        return IndexResult(
            episode_ids=tuple(episode.id for episode in episodes),
            indexed_count=len(pending),
            skipped_count=skipped,
            track_id=track_id,
        )

    async def query(
        self,
        text: str,
        *,
        mode: str,
        query_options: dict[str, Any],
    ) -> Any:
        self._require_initialized()
        return await self.engine.query(
            text,
            mode=mode,
            query_options=query_options,
        )

    async def ingest_extracted(
        self,
        memories: Sequence[ExtractedMemory],
        *,
        track_id: str | None = None,
    ) -> IndexResult:
        self._require_initialized()
        pending: list[ExtractedMemory] = []
        skipped = 0
        resolved_episode_ids: list[str] = []
        for memory in memories:
            # Admission owns durable Episode creation.  Looking up here keeps
            # idempotent replays cheap without staging an Episode that could be
            # orphaned when the document pipeline rejects the input.
            record = await self.sqlite.get_episode(memory.episode.id)
            if (
                record is not None
                and record["content_hash"] != memory.episode.content_hash
            ):
                raise ValueError(
                    f"episode {memory.episode.id!r} already exists with different content"
                )
            if record is None:
                record = await self.sqlite.get_episode_by_content_hash(
                    memory.episode.content_hash
                )
            if record is not None and record["status"] == EpisodeStatus.INDEXED.value:
                skipped += 1
                resolved_episode_ids.append(str(record["episode_id"]))
            elif record is not None and record["episode_id"] != memory.episode.id:
                # A retry can arrive while the original server-owned Episode
                # is still queued. Follow that canonical admission instead of
                # enqueueing a new ID that retained content dedup will reject.
                status_row = await self.engine.wait_for_document(record["episode_id"])
                await self.sqlite.mark_episode(
                    record["episode_id"],
                    EpisodeStatus.INDEXED,
                    track_id=status_row.get("track_id"),
                )
                skipped += 1
                resolved_episode_ids.append(str(record["episode_id"]))
            else:
                pending.append(memory)
                resolved_episode_ids.append(memory.episode.id)
        if not pending:
            return IndexResult(
                episode_ids=tuple(resolved_episode_ids),
                indexed_count=0,
                skipped_count=skipped,
                track_id=None,
            )
        try:
            engine_track_id = (
                await self.engine.ingest_extracted(pending)
                if track_id is None
                else await self.engine.ingest_extracted(pending, track_id=track_id)
            )
        except TimeoutError:
            # Admission remains live and may still complete in the background.
            # Do not rewrite the canonical Episode as failed on caller timeout.
            raise
        except Exception as exc:
            for memory in pending:
                # A rejected admission has no canonical document and therefore
                # must not leave an Episode.  Once admission has created the
                # matching Episode, preserve the failure on both sides.
                if await self.sqlite.get_episode(memory.episode.id) is not None:
                    await self.sqlite.mark_episode(
                        memory.episode.id,
                        EpisodeStatus.FAILED,
                        error=str(exc),
                    )
            raise
        for memory in pending:
            if await self.sqlite.get_episode(memory.episode.id) is None:
                await self.sqlite.put_episode(memory.episode)
            await self.sqlite.mark_episode(
                memory.episode.id,
                EpisodeStatus.INDEXED,
                track_id=engine_track_id,
            )
        return IndexResult(
            episode_ids=tuple(resolved_episode_ids),
            indexed_count=len(pending),
            skipped_count=skipped,
            track_id=engine_track_id,
        )

    async def status(self) -> dict[str, Any]:
        self._require_initialized()
        engine_status, memory_status = await asyncio.gather(
            self.engine.status(), self.sqlite.memory_overview()
        )
        return {
            "initialized": self._initialized,
            "engine": engine_status,
            "memory": memory_status,
            "neo4j": {
                "available": self.neo4j._initialized,
                "backend": type(self.neo4j.raw).__name__,
            },
        }
