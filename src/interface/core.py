"""Public lifecycle and handle API for one shared MAGI Core instance."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from magi_core.backend import BackendBundle, IndexResult
from magi_core.backend.engine import EngineBackend
from magi_core.backend.neo4j import Neo4jBackend
from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import Episode
from magi_core.memory.adapter import MagiKnowledgeAdapter
from magi_core.workspace import WorkspaceLayout


EngineFactory = Callable[[Path, str], Any]
Extension = Callable[..., Awaitable[Any] | Any]


class APIState(str, Enum):
    NEW = "new"
    INITIALIZED = "initialized"
    FINALIZED = "finalized"


@dataclass(frozen=True, slots=True)
class MagiHandle:
    """A lightweight caller identity over one shared core instance."""

    id: str
    owner: str | None
    metadata: Mapping[str, Any]
    _api: "MagiCoreAPI" = field(repr=False, compare=False)

    @property
    def is_open(self) -> bool:
        return self._api.is_handle_open(self)

    async def close(self) -> None:
        await self._api.close(self)

    async def index(
        self, episodes: Episode | Sequence[Episode]
    ) -> IndexResult:
        return await self._api.index(self, episodes)

    async def index_file(
        self, path: str | Path, *, episode_id: str | None = None
    ) -> IndexResult:
        return await self._api.index_file(self, path, episode_id=episode_id)

    async def query(
        self, text: str, *, mode: str = "mix", **query_options: Any
    ) -> Any:
        return await self._api.query(
            self, text, mode=mode, **query_options
        )

    async def extension(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return await self._api.extension(self, name, *args, **kwargs)


class MagiCoreAPI:
    """Lifecycle boundary shared by multiple agent-specific handles.

    ``init`` initializes the instance once. ``open`` and ``close`` only manage
    caller handles and never duplicate or tear down storage objects. ``finalize``
    invalidates all handles and terminates the underlying instance.
    """

    def __init__(
        self,
        *,
        layout: WorkspaceLayout,
        backend: BackendBundle,
    ) -> None:
        self.layout = layout
        self.backend = backend
        self._state = APIState.NEW
        self._handles: dict[str, MagiHandle] = {}
        self._extensions: dict[str, Extension] = {}

    @classmethod
    def from_engine(
        cls,
        *,
        workspace: str | Path,
        engine: Any,
        workspace_id: str | None = None,
    ) -> "MagiCoreAPI":
        layout = WorkspaceLayout.from_root(workspace, workspace_id=workspace_id)
        sqlite = SQLiteBackend(layout.sqlite_path, layout.workspace_id)
        memory_adapter = MagiKnowledgeAdapter(sqlite)
        engine_backend = EngineBackend(
            engine, layout, memory_adapter=memory_adapter
        )
        neo4j = Neo4jBackend(engine.chunk_entity_relation_graph)
        return cls(
            layout=layout,
            backend=BackendBundle(
                engine=engine_backend,
                sqlite=sqlite,
                neo4j=neo4j,
            ),
        )

    @classmethod
    def build(
        cls,
        *,
        workspace: str | Path,
        core_factory: EngineFactory,
        workspace_id: str | None = None,
    ) -> "MagiCoreAPI":
        layout = WorkspaceLayout.from_root(workspace, workspace_id=workspace_id)
        engine = core_factory(layout.ragstore, layout.workspace_id)
        return cls.from_engine(
            workspace=layout.root,
            workspace_id=layout.workspace_id,
            engine=engine,
        )

    @classmethod
    def from_lightrag(
        cls,
        *,
        workspace: str | Path,
        rag: Any,
        workspace_id: str | None = None,
    ) -> "MagiCoreAPI":
        """Compatibility constructor for a configured legacy instance."""

        return cls.from_engine(
            workspace=workspace,
            workspace_id=workspace_id,
            engine=rag,
        )

    @property
    def state(self) -> APIState:
        return self._state

    @property
    def raw_engine(self) -> Any:
        """Expose the shared engine for API/WebUI integration code."""

        return self.backend.engine.raw

    async def init(self) -> "MagiCoreAPI":
        if self._state is not APIState.NEW:
            raise RuntimeError(f"cannot initialize API in state {self._state.value}")
        self.layout.ensure()
        await self.backend.initialize()
        self._state = APIState.INITIALIZED
        return self

    async def open(
        self,
        *,
        owner: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MagiHandle:
        self._require_initialized()
        handle = MagiHandle(
            id=f"handle-{uuid4().hex}",
            owner=owner,
            metadata=dict(metadata or {}),
            _api=self,
        )
        self._handles[handle.id] = handle
        return handle

    async def close(self, handle: MagiHandle) -> None:
        self._require_handle(handle)
        del self._handles[handle.id]

    async def finalize(self) -> None:
        if self._state is APIState.FINALIZED:
            return
        if self._state is APIState.NEW:
            self._state = APIState.FINALIZED
            return
        self._handles.clear()
        try:
            await self.backend.finalize()
        finally:
            self._state = APIState.FINALIZED

    async def __aenter__(self) -> "MagiCoreAPI":
        return await self.init()

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.finalize()

    def is_handle_open(self, handle: MagiHandle) -> bool:
        return (
            self._state is APIState.INITIALIZED
            and handle._api is self
            and self._handles.get(handle.id) is handle
        )

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
        self._require_handle(handle)
        try:
            extension = self._extensions[name]
        except KeyError as exc:
            raise KeyError(f"unknown MAGI extension {name!r}") from exc
        result = extension(handle, *args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def index(
        self,
        handle: MagiHandle,
        episodes: Episode | Sequence[Episode],
    ) -> IndexResult:
        self._require_handle(handle)
        normalized = (
            [episodes]
            if isinstance(episodes, Episode)
            else list(episodes)
        )
        if not normalized:
            raise ValueError("at least one episode is required")
        return await self.backend.index(normalized)

    async def index_file(
        self,
        handle: MagiHandle,
        path: str | Path,
        *,
        episode_id: str | None = None,
    ) -> IndexResult:
        return await self.index(
            handle,
            Episode.from_file(path, episode_id=episode_id),
        )

    async def query(
        self,
        handle: MagiHandle,
        text: str,
        *,
        mode: str = "mix",
        **query_options: Any,
    ) -> Any:
        self._require_handle(handle)
        if not text.strip():
            raise ValueError("query text must not be empty")
        return await self.backend.query(
            text,
            mode=mode,
            query_options=query_options,
        )

    def _require_initialized(self) -> None:
        if self._state is not APIState.INITIALIZED:
            raise RuntimeError(f"API is not initialized: {self._state.value}")

    def _require_handle(self, handle: MagiHandle) -> None:
        self._require_initialized()
        if not self.is_handle_open(handle):
            raise RuntimeError(f"MAGI handle {handle.id!r} is closed or foreign")
