"""Lifecycle facade around the tested retrieval engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from magi_core.memory import Episode, ExtractedMemory
from magi_core.workspace import WorkspaceLayout

if TYPE_CHECKING:
    from magi_core.memory.adapter import MagiKnowledgeAdapter


class EngineBackend:
    """Keep the full MAGI Core retrieval lifecycle intact."""

    def __init__(
        self,
        rag: Any,
        layout: WorkspaceLayout,
        memory_adapter: "MagiKnowledgeAdapter | None" = None,
    ) -> None:
        self.raw = rag
        self.layout = layout
        self.memory_adapter = memory_adapter
        self._initialized = False

    def _validate_configuration(self) -> None:
        actual_dir = Path(self.raw.working_dir).expanduser().resolve()
        if actual_dir != self.layout.ragstore:
            raise ValueError(
                "engine working_dir must be the MAGI ragstore: "
                f"expected {self.layout.ragstore}, got {actual_dir}"
            )
        actual_workspace = str(getattr(self.raw, "workspace", ""))
        if actual_workspace != self.layout.workspace_id:
            raise ValueError(
                "engine workspace must match the MAGI workspace: "
                f"expected {self.layout.workspace_id!r}, got {actual_workspace!r}"
            )

    async def initialize(self) -> None:
        self._validate_configuration()
        if self.memory_adapter is not None:
            self.raw.register_knowledge_ingestion_adapter(self.memory_adapter)
        try:
            await self.raw.initialize_storages()
            await self.raw.check_and_migrate_data()
        except Exception:
            try:
                await self.raw.finalize_storages()
            finally:
                if self.memory_adapter is not None:
                    self.raw.register_knowledge_ingestion_adapter(None)
            raise
        self._initialized = True

    async def finalize(self) -> None:
        if self._initialized:
            await self.raw.finalize_storages()
            if self.memory_adapter is not None:
                self.raw.register_knowledge_ingestion_adapter(None)
            self._initialized = False

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("MAGI engine backend is not initialized")

    async def wait_for_document(self, doc_id: str) -> dict[str, Any]:
        """Wait until an admitted document reaches a durable terminal state."""

        self._require_initialized()
        llm_timeout = float(getattr(self.raw, "default_llm_timeout", 300) or 300)
        deadline = asyncio.get_running_loop().time() + max(60.0, llm_timeout * 2)
        strict_get = getattr(self.raw.doc_status, "get_by_id_strict", None)
        get_by_id = strict_get if callable(strict_get) else self.raw.doc_status.get_by_id
        last_status = "missing"
        while True:
            status_row = await get_by_id(doc_id)
            status = status_row.get("status") if status_row else None
            status_value = getattr(status, "value", status)
            last_status = str(status_value or "missing")
            if status_row is None:
                # Enqueue admission persists doc_status before returning. A
                # missing row therefore means rejection/dedup, not queued work.
                raise RuntimeError(f"{doc_id}: missing")
            if status_value == "processed":
                return status_row
            if status_value == "failed":
                error = (status_row or {}).get("error_msg") or last_status
                raise RuntimeError(f"{doc_id}: {error}")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for {doc_id!r} to finish; "
                    f"last status: {last_status}"
                )
            await asyncio.sleep(0.05)

    async def index(self, episodes: Sequence[Episode]) -> str | None:
        self._require_initialized()
        if not episodes:
            return None
        if self.memory_adapter is not None:
            self.memory_adapter.register_episodes(episodes)
        track_id = await self.raw.ainsert(
            [episode.content for episode in episodes],
            ids=[episode.id for episode in episodes],
            file_paths=[
                episode.source_uri or f"episode://{episode.id}"
                for episode in episodes
            ],
        )
        failures: list[str] = []
        for episode in episodes:
            status_row = await self.raw.doc_status.get_by_id(episode.id)
            status = status_row.get("status") if status_row else None
            status_value = getattr(status, "value", status)
            if status_value != "processed":
                error = (status_row or {}).get("error_msg") or status_value or "missing"
                failures.append(f"{episode.id}: {error}")
        if failures:
            if self.memory_adapter is not None:
                self.memory_adapter.unregister_episodes(
                    [episode.id for episode in episodes]
                )
            raise RuntimeError(
                "MAGI indexing did not reach the processed state: "
                + "; ".join(failures)
            )
        return track_id

    async def ingest_extracted(
        self,
        memories: Sequence[ExtractedMemory],
        *,
        track_id: str | None = None,
    ) -> str | None:
        self._require_initialized()
        if not memories:
            return None
        if self.memory_adapter is not None:
            self.memory_adapter.register_episodes(
                [memory.episode for memory in memories]
            )
        if track_id is None:
            track_id = await self.raw.aingest_extracted(list(memories))
        else:
            track_id = await self.raw.aingest_extracted(
                list(memories),
                track_id=track_id,
            )
        failures: list[str] = []
        for memory in memories:
            try:
                await self.wait_for_document(memory.episode.id)
            except RuntimeError as exc:
                failures.append(str(exc))
        if failures:
            if self.memory_adapter is not None:
                self.memory_adapter.unregister_episodes(
                    [memory.episode.id for memory in memories]
                )
            raise RuntimeError(
                "MAGI extracted-memory ingestion did not reach the processed "
                "state: " + "; ".join(failures)
            )
        return track_id

    async def status(self) -> dict[str, Any]:
        self._require_initialized()
        result: dict[str, Any] = {}
        for key, method_name in (
            ("documents", "get_processing_status"),
            ("llm_queues", "get_llm_queue_status"),
            ("embedding_queue", "get_embedding_queue_status"),
            ("rerank_queue", "get_rerank_queue_status"),
        ):
            method = getattr(self.raw, method_name, None)
            if not callable(method):
                continue
            try:
                result[key] = await method()
            except Exception as exc:
                result[key] = {"error": str(exc)}
        return result

    async def query(
        self,
        text: str,
        *,
        mode: str,
        query_options: dict[str, Any],
    ) -> Any:
        self._require_initialized()
        from magi_core import QueryParam

        param = QueryParam(mode=mode, **query_options)
        return await self.raw.aquery(text, param=param)
