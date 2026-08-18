"""Managed background execution for accepted memory ingestion jobs."""

from __future__ import annotations

import asyncio
from typing import Any

from magi_core.memory import ExtractedMemory
from magi_core.utils import logger


async def start_managed_extracted_ingest(
    *,
    managed_tasks: set[asyncio.Task[Any]],
    handle: Any,
    memory: ExtractedMemory,
    track_id: str,
) -> asyncio.Task[None]:
    """Schedule one accepted extracted ingest and return without running it inline."""

    async def ingest_in_background() -> None:
        try:
            await handle.ingest_extracted(memory, track_id=track_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Background extracted-memory ingestion failed for %s",
                memory.episode.id,
            )
        finally:
            await handle.close()

    task = asyncio.create_task(
        ingest_in_background(),
        name=f"magi-extracted-{memory.episode.id}",
    )
    managed_tasks.add(task)
    task.add_done_callback(managed_tasks.discard)
    # create_task plus the managed_tasks strong reference is the admission
    # boundary.  Do not yield here: the ingest coroutine may do substantial
    # synchronous work before its first await, which would delay HTTP 202 and
    # make callers time out even though the write eventually succeeds.
    return task
