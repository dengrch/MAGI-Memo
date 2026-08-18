from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pytest

from magi_core.workspace_cleanup import clear_workspace_data
from magi_runtime.workspaces import WorkspaceManager


@dataclass
class _SharedWorkspaceStorage:
    """Model a shared service whose drop operation is workspace-filtered."""

    rows: dict[str, set[str]]
    workspace: str
    namespace: str

    async def drop(self) -> dict[str, str]:
        self.rows.pop(self.workspace, None)
        return {"status": "success"}


class _WorkspaceRag:
    def __init__(self, workspace: str, stores: list[dict[str, set[str]]]) -> None:
        self.workspace = workspace
        names = (
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
        for name, rows in zip(names, stores, strict=True):
            setattr(self, name, _SharedWorkspaceStorage(rows, workspace, name))

    async def _clear_knowledge_memory(self) -> list[str]:
        return [f"episode-{self.workspace}"]


@pytest.mark.asyncio
async def test_workspace_cleanup_preserves_other_workspace_in_shared_stores() -> None:
    stores = [
        {"alpha": {"alpha-row"}, "beta": {"beta-row"}}
        for _ in range(12)
    ]
    rag = _WorkspaceRag("alpha", stores)

    result = await clear_workspace_data(rag)

    assert result.successful
    assert len(result.dropped_storages) == 12
    assert result.episode_backup_ids == ("episode-alpha",)
    assert all("alpha" not in rows for rows in stores)
    assert all(rows["beta"] == {"beta-row"} for rows in stores)


def test_manager_deletes_only_managed_workspace_directory(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path, default_workspace_id="default")
    manager.initialize()
    deleted = manager.create("research", label="Research")
    marker = deleted.path / "inputs" / "episode.txt"
    marker.write_text("memory", encoding="utf-8")
    manager.set_active("research")

    result = manager.delete("research")

    assert result.deleted.id == "research"
    assert result.active_workspace_id == "default"
    assert not deleted.path.exists()
    assert manager.active_workspace_id == "default"
    assert [item.id for item in manager.list()] == ["default"]


def test_manager_refuses_default_and_external_workspace_roots(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "home", default_workspace_id="default")
    manager.initialize()
    manager.create("managed", label="Managed")
    external_root = tmp_path / "external"
    manager.create("external", label="External", root=external_root)

    with pytest.raises(ValueError, match="default workspace"):
        manager.delete("default")
    with pytest.raises(ValueError, match="Externally managed"):
        manager.delete("external")

    assert external_root.exists()
