"""Single-instance, multi-handle runtime for MAGI Core."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping
from uuid import uuid4

from magi_core.backend import BackendBundle
from magi_core.backend.engine import EngineBackend
from magi_core.backend.neo4j import Neo4jBackend
from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory.adapter import MagiKnowledgeAdapter
from magi_runtime.workspaces import WorkspaceManager, WorkspaceRecord


EngineFactory = Callable[[Path, str], Any]


class RuntimeState(str, Enum):
    NEW = "new"
    INITIALIZING = "initializing"
    READY = "ready"
    DRAINING = "draining"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"
    FAILED = "failed"


class HandleState(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(slots=True)
class RuntimeHandle:
    id: str
    owner: str | None
    metadata: Mapping[str, Any]
    state: HandleState = HandleState.OPEN
    in_flight: int = 0
    idle: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        self.idle.set()


class MagiRuntime:
    """Own exactly one active Core instance and any number of handles."""

    def __init__(
        self,
        *,
        workspace_manager: WorkspaceManager,
        core_factory: EngineFactory,
    ) -> None:
        self.workspace_manager = workspace_manager
        self.core_factory = core_factory
        self.state = RuntimeState.NEW
        self.active_workspace_id: str | None = None
        self.backend: BackendBundle | None = None
        self._handles: dict[str, RuntimeHandle] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._handle_lock = asyncio.Lock()
        self._owning_loop: asyncio.AbstractEventLoop | None = None

    async def instance_init(self, workspace_id: str | None = None) -> None:
        async with self._lifecycle_lock:
            if self.state is not RuntimeState.NEW:
                raise RuntimeError(f"cannot initialize runtime in state {self.state.value}")
            self.state = RuntimeState.INITIALIZING
            self._owning_loop = asyncio.get_running_loop()
            try:
                self.workspace_manager.initialize()
                selected = workspace_id or self.workspace_manager.active_workspace_id
                layout = self.workspace_manager.layout(selected)
                layout.ensure()
                engine = self.core_factory(layout.ragstore, layout.workspace_id)
                sqlite = SQLiteBackend(layout.sqlite_path, layout.workspace_id)
                adapter = MagiKnowledgeAdapter(sqlite)
                engine_backend = EngineBackend(engine, layout, memory_adapter=adapter)
                backend = BackendBundle(
                    engine=engine_backend,
                    sqlite=sqlite,
                    neo4j=Neo4jBackend(engine.chunk_entity_relation_graph),
                )
                await backend.initialize()
                self.workspace_manager.set_active(selected)
            except BaseException:
                self.state = RuntimeState.FAILED
                self.backend = None
                self.active_workspace_id = None
                raise
            self.backend = backend
            self.active_workspace_id = selected
            self.state = RuntimeState.READY

    async def instance_destroy(
        self,
        *,
        timeout: float | None = None,
        final: bool = True,
    ) -> None:
        async with self._lifecycle_lock:
            if self.state is RuntimeState.FINALIZED:
                return
            if self.state is RuntimeState.NEW:
                self.state = RuntimeState.FINALIZED if final else RuntimeState.NEW
                return
            if self.state not in (
                RuntimeState.READY,
                RuntimeState.DRAINING,
                RuntimeState.FAILED,
            ):
                raise RuntimeError(f"cannot destroy runtime in state {self.state.value}")
            if self.state is not RuntimeState.DRAINING:
                self.state = RuntimeState.DRAINING
                async with self._handle_lock:
                    handles = list(self._handles.values())
                    for handle in handles:
                        handle.state = HandleState.CLOSING
            else:
                async with self._handle_lock:
                    handles = list(self._handles.values())
            drain = asyncio.gather(*(handle.idle.wait() for handle in handles))
            if timeout is None:
                await drain
            else:
                await asyncio.wait_for(drain, timeout=timeout)
            async with self._handle_lock:
                for handle in handles:
                    handle.state = HandleState.CLOSED
                self._handles.clear()

            self.state = RuntimeState.FINALIZING
            backend = self.backend
            try:
                if backend is not None:
                    await backend.finalize()
            finally:
                self.backend = None
                self.active_workspace_id = None
                self.state = RuntimeState.FINALIZED if final else RuntimeState.NEW
                if not final:
                    self._owning_loop = None

    async def switch_workspace(
        self, workspace_id: str, *, timeout: float | None = None
    ) -> None:
        self._require_loop()
        if workspace_id == self.active_workspace_id:
            return
        # Resolve before destroying the active instance so an unknown target
        # cannot take a healthy workspace offline.
        self.workspace_manager.get(workspace_id)
        async with self._handle_lock:
            if self._handles:
                raise RuntimeError(
                    "workspace switch requires every public handle to be closed"
                )
        await self.instance_destroy(timeout=timeout, final=False)
        await self.instance_init(workspace_id)

    async def open_handle(
        self,
        *,
        owner: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeHandle:
        self._require_ready()
        handle = RuntimeHandle(
            id=f"handle-{uuid4().hex}",
            owner=owner,
            metadata=dict(metadata or {}),
        )
        async with self._handle_lock:
            if self.state is not RuntimeState.READY:
                raise RuntimeError("runtime stopped accepting handles")
            self._handles[handle.id] = handle
        return handle

    async def close_handle(self, handle_id: str) -> None:
        self._require_loop()
        async with self._handle_lock:
            handle = self._handles.get(handle_id)
            if handle is None or handle.state is HandleState.CLOSED:
                return
            handle.state = HandleState.CLOSING
        await handle.idle.wait()
        async with self._handle_lock:
            handle.state = HandleState.CLOSED
            self._handles.pop(handle_id, None)

    @asynccontextmanager
    async def use(self, handle_id: str) -> AsyncIterator[BackendBundle]:
        self._require_ready()
        async with self._handle_lock:
            handle = self._handles.get(handle_id)
            if handle is None or handle.state is not HandleState.OPEN:
                raise RuntimeError(f"MAGI handle {handle_id!r} is closed or foreign")
            handle.in_flight += 1
            handle.idle.clear()
        try:
            backend = self.backend
            if backend is None:
                raise RuntimeError("runtime instance is unavailable")
            yield backend
        finally:
            async with self._handle_lock:
                handle.in_flight -= 1
                if handle.in_flight == 0:
                    handle.idle.set()

    def is_handle_open(self, handle_id: str) -> bool:
        handle = self._handles.get(handle_id)
        return (
            self.state is RuntimeState.READY
            and handle is not None
            and handle.state is HandleState.OPEN
        )

    async def status(self) -> dict[str, Any]:
        self._require_loop()
        backend_status = None
        if self.state is RuntimeState.READY and self.backend is not None:
            backend_status = await self.backend.status()
        return {
            "state": self.state.value,
            "workspace_id": self.active_workspace_id,
            "open_handles": sum(
                handle.state is HandleState.OPEN for handle in self._handles.values()
            ),
            "in_flight": sum(handle.in_flight for handle in self._handles.values()),
            "backend": backend_status,
        }

    def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        return self.workspace_manager.list()

    def create_workspace(
        self, workspace_id: str | None = None, *, label: str | None = None
    ) -> WorkspaceRecord:
        return self.workspace_manager.create(workspace_id, label=label)

    def _require_ready(self) -> None:
        self._require_loop()
        if self.state is not RuntimeState.READY:
            raise RuntimeError(f"runtime is not ready: {self.state.value}")

    def _require_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._owning_loop is not None and loop is not self._owning_loop:
            raise RuntimeError("MAGI Runtime cannot cross asyncio event loops")
