from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from magi_core.api.background_memory import start_managed_extracted_ingest
from magi_core.memory import Episode, ExtractedAtom, ExtractedEntity, ExtractedMemory


class _BlockingHandle:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.track_id: str | None = None

    async def ingest_extracted(
        self,
        memory: ExtractedMemory,
        *,
        track_id: str | None = None,
    ) -> Any:
        assert memory.episode.content == "Alice prefers concise reports."
        self.track_id = track_id
        self.started.set()
        await self.release.wait()

    async def close(self) -> None:
        self.closed = True


class _SlowBeforeFirstAwaitHandle:
    def __init__(self) -> None:
        self.closed = False

    async def ingest_extracted(
        self,
        memory: ExtractedMemory,
        *,
        track_id: str | None = None,
    ) -> None:
        # Model a real ingest path that performs synchronous work before its
        # first suspension point.
        time.sleep(0.1)

    async def close(self) -> None:
        self.closed = True


def _memory() -> ExtractedMemory:
    return ExtractedMemory.create(
        episode=Episode(content="Alice prefers concise reports."),
        entities=(
            ExtractedEntity(
                "Alice",
                (ExtractedAtom("Alice prefers concise reports."),),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_background_ingest_returns_control_while_managed_task_runs() -> None:
    handle = _BlockingHandle()
    managed_tasks: set[asyncio.Task[Any]] = set()

    task = await start_managed_extracted_ingest(
        managed_tasks=managed_tasks,
        handle=handle,
        memory=_memory(),
        track_id="ingest-extracted-test",
    )

    await asyncio.wait_for(handle.started.wait(), timeout=1)
    assert task in managed_tasks
    assert not task.done()
    assert handle.track_id == "ingest-extracted-test"

    handle.release.set()
    await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)
    assert handle.closed
    assert task not in managed_tasks


@pytest.mark.asyncio
async def test_background_admission_yields_before_ingest_work_starts() -> None:
    handle = _SlowBeforeFirstAwaitHandle()
    managed_tasks: set[asyncio.Task[Any]] = set()

    started_at = time.monotonic()
    task = await start_managed_extracted_ingest(
        managed_tasks=managed_tasks,
        handle=handle,
        memory=_memory(),
        track_id="ingest-extracted-slow-start",
    )
    admission_elapsed = time.monotonic() - started_at

    assert admission_elapsed < 0.05
    assert not task.done()
    await asyncio.wait_for(task, timeout=1)
    assert handle.closed
