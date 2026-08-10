"""Optional integration seam for knowledge commits.

The default LightRAG path remains unchanged. External memory systems can
participate after extraction and before the graph/vector mutation phase without
reimplementing the document pipeline or storage lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class KnowledgeCommitContext:
    """Operation context supplied to an external knowledge commit adapter."""

    rag: Any
    doc_id: str | None
    file_path: str
    full_entities_storage: Any = None
    full_relations_storage: Any = None
    pipeline_status: dict[str, Any] | None = None
    pipeline_status_lock: Any = None
    current_file_number: int = 0
    total_files: int = 0


class KnowledgeIngestionAdapter(Protocol):
    """Commit extracted knowledge while preserving the LightRAG pipeline."""

    async def prepare_episode(
        self,
        *,
        doc_id: str,
        content: str,
        file_path: str,
        reference_at: str | None = None,
    ) -> None:
        """Persist and register the Episode before its extraction call."""
        ...

    async def stage_episode(self, episode: Any) -> None:
        """Persist a structured Episode supplied by a direct ingest caller."""
        ...

    async def complete_episode(
        self, doc_id: str, *, track_id: str | None = None
    ) -> None:
        """Mark the Episode indexed after derived stores are durable."""
        ...

    async def fail_episode(self, doc_id: str, *, error: str) -> None:
        """Mirror a pipeline failure into the Episode record when present."""
        ...

    def get_extraction_context(self, doc_id: str | None) -> dict[str, Any]:
        """Return Episode context used by the extraction prompt."""
        ...

    async def commit(
        self,
        chunk_results: list,
        context: KnowledgeCommitContext,
    ) -> None: ...
