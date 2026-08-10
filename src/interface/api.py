"""Stable public API over the MAGI Runtime lifecycle."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from magi_core.backend import IndexResult
from magi_core.memory import Episode, ExtractedMemory
from magi_runtime import MagiRuntime, WorkspaceManager, WorkspaceRecord


EngineFactory = Callable[[Path, str], Any]
Extension = Callable[..., Awaitable[Any] | Any]


class APIState(str, Enum):
    NEW = "new"
    INITIALIZING = "initializing"
    READY = "ready"
    INITIALIZED = "ready"  # compatibility alias
    DRAINING = "draining"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MagiHandle:
    """A caller handle over one shared workspace instance."""

    id: str
    owner: str | None
    metadata: Mapping[str, Any]
    _api: "MagiAPI" = field(repr=False, compare=False)

    @property
    def is_open(self) -> bool:
        return self._api.is_handle_open(self)

    async def close(self) -> None:
        await self._api.close(self)

    async def ingest(
        self, episodes: Episode | Sequence[Episode]
    ) -> IndexResult:
        """Ingest raw Episodes through the retained LLM extraction pipeline."""

        return await self._api.ingest(self, episodes)

    async def ingest_file(
        self, path: str | Path, *, episode_id: str | None = None
    ) -> IndexResult:
        return await self._api.ingest_file(self, path, episode_id=episode_id)

    async def ingest_extracted(
        self, memories: ExtractedMemory | Sequence[ExtractedMemory]
    ) -> IndexResult:
        """Ingest already-extracted knowledge after the first LLM stage."""

        return await self._api.ingest_extracted(self, memories)

    async def search(
        self, text: str, *, mode: str = "mix", **query_options: Any
    ) -> Any:
        return await self._api.search(self, text, mode=mode, **query_options)

    async def status(self) -> dict[str, Any]:
        return await self._api.status(self)

    # Phase-one compatibility names.
    index = ingest
    index_file = ingest_file
    query = search

    async def extension(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return await self._api.extension(self, name, *args, **kwargs)


class MagiAPI:
    """Public ``init/open/close/finalize`` facade over :class:`MagiRuntime`.

    The Runtime owns instance creation/destruction; public handles never own or
    directly finalize Core, SQLite or Neo4j resources.
    """

    def __init__(self, runtime: MagiRuntime) -> None:
        self.runtime = runtime
        self._handles: dict[str, MagiHandle] = {}
        self._extensions: dict[str, Extension] = {}

    @classmethod
    def build(
        cls,
        *,
        workspace: str | Path | None = None,
        workspace_home: str | Path | None = None,
        core_factory: EngineFactory,
        workspace_id: str | None = None,
    ) -> "MagiAPI":
        if workspace is None and workspace_home is None:
            raise ValueError("workspace_home is required")
        if workspace is not None and workspace_home is not None:
            legacy_root = Path(workspace).expanduser().resolve()
            explicit_root = Path(workspace_home).expanduser().resolve()
            if legacy_root != explicit_root:
                raise ValueError("workspace and workspace_home must resolve equally")
        root = Path(workspace_home or workspace).expanduser().resolve()
        selected = (workspace_id or root.name).strip()
        manager = WorkspaceManager(
            root,
            default_workspace_id=selected,
            default_workspace_root=root,
        )
        return cls(
            MagiRuntime(workspace_manager=manager, core_factory=core_factory)
        )

    @classmethod
    def from_engine(
        cls,
        *,
        workspace: str | Path,
        engine: Any,
        workspace_id: str | None = None,
    ) -> "MagiAPI":
        root = Path(workspace).expanduser().resolve()
        selected = (workspace_id or root.name).strip()

        def fixed_factory(ragstore: Path, candidate_workspace: str) -> Any:
            actual_dir = Path(engine.working_dir).expanduser().resolve()
            if actual_dir != ragstore or str(engine.workspace) != candidate_workspace:
                raise ValueError("configured engine does not match selected workspace")
            return engine

        return cls.build(
            workspace=root,
            workspace_id=selected,
            core_factory=fixed_factory,
        )

    @classmethod
    def from_lightrag(
        cls,
        *,
        workspace: str | Path,
        rag: Any,
        workspace_id: str | None = None,
    ) -> "MagiAPI":
        return cls.from_engine(
            workspace=workspace,
            workspace_id=workspace_id,
            engine=rag,
        )

    @property
    def state(self) -> APIState:
        return APIState(self.runtime.state.value)

    @property
    def raw_engine(self) -> Any:
        """Internal compatibility access for the retained WebUI routers."""

        backend = self.runtime.backend
        if backend is None:
            raise RuntimeError("MAGI instance is not initialized")
        return backend.engine.raw

    async def init(self, *, workspace_id: str | None = None) -> "MagiAPI":
        await self.runtime.instance_init(workspace_id)
        return self

    async def finalize(self, *, timeout: float | None = None) -> None:
        await self.runtime.instance_destroy(timeout=timeout, final=True)
        self._handles.clear()

    async def __aenter__(self) -> "MagiAPI":
        return await self.init()

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.finalize()

    async def open(
        self,
        *,
        owner: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MagiHandle:
        internal = await self.runtime.open_handle(owner=owner, metadata=metadata)
        handle = MagiHandle(
            id=internal.id,
            owner=internal.owner,
            metadata=internal.metadata,
            _api=self,
        )
        self._handles[handle.id] = handle
        return handle

    async def close(self, handle: MagiHandle) -> None:
        self._require_own_handle(handle)
        await self.runtime.close_handle(handle.id)
        self._handles.pop(handle.id, None)

    def is_handle_open(self, handle: MagiHandle) -> bool:
        return (
            handle._api is self
            and self._handles.get(handle.id) is handle
            and self.runtime.is_handle_open(handle.id)
        )

    async def switch_workspace(
        self, workspace_id: str, *, timeout: float | None = None
    ) -> None:
        await self.runtime.switch_workspace(workspace_id, timeout=timeout)

    def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        return self.runtime.list_workspaces()

    def create_workspace(
        self, name: str, *, workspace_id: str | None = None
    ) -> WorkspaceRecord:
        """Create a named workspace and optionally pin its storage identifier.

        ``workspace_id`` remains available for agents and integrations that need
        a stable external identifier. Human-facing clients should pass only the
        visible ``name``.
        """

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("workspace name must not be empty")
        return self.runtime.create_workspace(
            workspace_id,
            label=normalized_name,
        )

    async def ingest(
        self,
        handle: MagiHandle,
        episodes: Episode | Sequence[Episode],
    ) -> IndexResult:
        normalized = [episodes] if isinstance(episodes, Episode) else list(episodes)
        if not normalized:
            raise ValueError("at least one Episode is required")
        async with self._use(handle) as backend:
            return await backend.index(normalized)

    async def ingest_file(
        self,
        handle: MagiHandle,
        path: str | Path,
        *,
        episode_id: str | None = None,
    ) -> IndexResult:
        return await self.ingest(
            handle,
            Episode.from_file(path, episode_id=episode_id),
        )

    async def ingest_extracted(
        self,
        handle: MagiHandle,
        memories: ExtractedMemory | Sequence[ExtractedMemory],
    ) -> IndexResult:
        normalized = (
            [memories] if isinstance(memories, ExtractedMemory) else list(memories)
        )
        if not normalized:
            raise ValueError("at least one extracted memory input is required")
        async with self._use(handle) as backend:
            return await backend.ingest_extracted(normalized)

    async def search(
        self,
        handle: MagiHandle,
        text: str,
        *,
        mode: str = "mix",
        **query_options: Any,
    ) -> Any:
        if not text.strip():
            raise ValueError("search text must not be empty")
        async with self._use(handle) as backend:
            return await backend.query(
                text,
                mode=mode,
                query_options=query_options,
            )

    async def status(self, handle: MagiHandle | None = None) -> dict[str, Any]:
        if handle is None:
            return await self.runtime.status()
        async with self._use(handle):
            return await self.runtime.status()

    # Compatibility aliases for phase-one clients.
    index = ingest
    index_file = ingest_file
    query = search

    def register_extension(self, name: str, extension: Extension) -> None:
        if not name or name.startswith("_"):
            raise ValueError("extension name must be public and non-empty")
        if name in self._extensions:
            raise ValueError(f"extension {name!r} is already registered")
        self._extensions[name] = extension

    async def extension(
        self,
        handle: MagiHandle,
        name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        async with self._use(handle):
            try:
                extension = self._extensions[name]
            except KeyError as exc:
                raise KeyError(f"unknown MAGI extension {name!r}") from exc
            result = extension(handle, *args, **kwargs)
            return await result if inspect.isawaitable(result) else result

    def _require_own_handle(self, handle: MagiHandle) -> None:
        if handle._api is not self or self._handles.get(handle.id) is not handle:
            raise RuntimeError(f"MAGI handle {handle.id!r} is closed or foreign")

    def _use(self, handle: MagiHandle):
        self._require_own_handle(handle)
        return self.runtime.use(handle.id)
