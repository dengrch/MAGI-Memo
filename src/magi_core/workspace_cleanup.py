"""Reusable workspace-scoped storage teardown."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkspaceCleanupResult:
    """Result of clearing every durable store owned by one workspace."""

    episode_backup_ids: tuple[str, ...]
    dropped_storages: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def successful(self) -> bool:
        return not self.errors


def _workspace_storages(rag: Any) -> list[Any]:
    """Return each retained workspace storage exactly once."""

    candidates = tuple(
        getattr(rag, name, None)
        for name in (
            "text_chunks",
            "full_docs",
            "full_entities",
            "full_relations",
            "entity_chunks",
            "relation_chunks",
            "entities_vdb",
            "relationships_vdb",
            "chunks_vdb",
            "chunk_entity_relation_graph",
            "doc_status",
            "llm_response_cache",
        )
    )
    result: list[Any] = []
    seen: set[int] = set()
    for storage in candidates:
        if storage is None or id(storage) in seen:
            continue
        seen.add(id(storage))
        result.append(storage)
    return result


async def clear_workspace_data(
    rag: Any,
    *,
    reinitialize_doc_status: bool = False,
) -> WorkspaceCleanupResult:
    """Clear MAGI memory and every retained storage for ``rag.workspace``.

    The caller owns concurrency fencing.  Interactive document clearing holds
    the destructive pipeline reservation; runtime workspace deletion requires
    an idle workspace and no public handles before calling this primitive.
    """

    clear_memory = getattr(rag, "_clear_knowledge_memory", None)
    backup_ids = await clear_memory() if callable(clear_memory) else []

    storages = _workspace_storages(rag)
    results = await asyncio.gather(
        *(storage.drop() for storage in storages),
        return_exceptions=True,
    )
    dropped: list[str] = []
    errors: list[str] = []
    for storage, result in zip(storages, results, strict=True):
        storage_name = type(storage).__name__
        identity = (
            f"{storage_name}:"
            f"{getattr(storage, 'workspace', getattr(rag, 'workspace', 'unknown'))}/"
            f"{getattr(storage, 'namespace', 'unknown')}"
        )
        if isinstance(result, Exception):
            errors.append(f"{identity}: {result}")
        elif isinstance(result, dict) and result.get("status") != "success":
            errors.append(f"{identity}: {result.get('message', 'unknown error')}")
        else:
            dropped.append(identity)

    doc_status = getattr(rag, "doc_status", None)
    if reinitialize_doc_status and doc_status is not None:
        try:
            initialize = getattr(doc_status, "initialize", None)
            if callable(initialize):
                await initialize()
        except Exception as exc:
            errors.append(f"doc_status reinitialize: {exc}")

    return WorkspaceCleanupResult(
        episode_backup_ids=tuple(str(item) for item in backup_ids),
        dropped_storages=tuple(dropped),
        errors=tuple(errors),
    )
