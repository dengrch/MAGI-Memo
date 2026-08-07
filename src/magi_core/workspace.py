"""Workspace layout shared by every backend component."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    """Resolve all local paths for one isolated MAGI workspace.

    MAGI Core and SQLite intentionally share ``ragstore``. Neo4j is an external
    service, so it has no local workspace directory.
    """

    root: Path
    workspace_id: str
    ragstore_name: str = "ragstore"
    sqlite_name: str = "magi-memory.db"

    @classmethod
    def from_root(
        cls,
        root: str | Path,
        *,
        workspace_id: str | None = None,
    ) -> "WorkspaceLayout":
        resolved_root = Path(root).expanduser().resolve()
        resolved_workspace = (workspace_id or resolved_root.name).strip()
        if not resolved_workspace:
            raise ValueError("workspace_id must not be empty")
        return cls(root=resolved_root, workspace_id=resolved_workspace)

    @property
    def ragstore(self) -> Path:
        return self.root / self.ragstore_name

    @property
    def sqlite_path(self) -> Path:
        return self.ragstore / self.sqlite_name

    def ensure(self) -> None:
        self.ragstore.mkdir(parents=True, exist_ok=True)
