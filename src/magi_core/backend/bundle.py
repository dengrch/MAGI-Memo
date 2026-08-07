"""Coordinate the retrieval engine, SQLite, and Neo4j as one backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from magi_core.backend.engine import EngineBackend
from magi_core.backend.neo4j import Neo4jBackend
from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import Episode, EpisodeStatus


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
        except Exception:
            try:
                await self.engine.finalize()
            finally:
                await self.sqlite.finalize()
            raise
        self._initialized = True

    async def finalize(self) -> None:
        if not self._initialized:
            return
        try:
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
            record = await self.sqlite.put_episode(episode)
            if record["status"] == EpisodeStatus.INDEXED.value:
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
                await self.sqlite.mark_episode(
                    episode.id,
                    EpisodeStatus.FAILED,
                    error=str(exc),
                )
            raise

        for episode in pending:
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
