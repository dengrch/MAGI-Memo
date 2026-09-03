"""BALTHASAR Dreaming status and manually triggered full rebuilds."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from magi_core.api.utils_api import get_combined_auth_dependency, internal_server_error
from magi_core.memory.dreaming import DreamingRunner
from magi_core.utils import logger


class DreamingRunRequest(BaseModel):
    """Small, bounded Leiden control surface for one full rebuild."""

    model_config = ConfigDict(extra="forbid")

    random_seed: int = Field(default=19, ge=0, le=2_147_483_647)
    gamma: float = Field(default=1.0, gt=0, le=100)
    theta: float = Field(default=0.01, gt=0, le=1)
    max_levels: int = Field(default=10, ge=1, le=100)
    concurrency: int = Field(default=4, ge=1, le=64)


def create_dreaming_routes(
    rag: Any,
    memory_db: Any,
    api_key: Optional[str] = None,
    *,
    runner_factory: Callable[[Any, Any], DreamingRunner] = DreamingRunner,
) -> APIRouter:
    """Create authenticated Dreaming control-plane routes."""

    router = APIRouter(prefix="/dreaming", tags=["dreaming"])
    combined_auth = get_combined_auth_dependency(api_key)
    protected = [Depends(combined_auth)]

    @router.get("/status", dependencies=protected)
    async def get_dreaming_status():
        try:
            return await memory_db.get_dreaming_status()
        except Exception as exc:
            logger.error("Failed to read Dreaming status: %s", exc)
            raise internal_server_error(exc)

    @router.get("/communities", dependencies=protected)
    async def get_dreaming_communities():
        try:
            latest_loader = getattr(
                memory_db, "get_latest_dreaming_memberships", None
            )
            sqlite_list = getattr(memory_db, "list_communities", None)
            if not callable(latest_loader) or not callable(sqlite_list):
                graph_loader = getattr(
                    rag.chunk_entity_relation_graph,
                    "get_dreaming_community_reports",
                    None,
                )
                if not callable(graph_loader):
                    raise HTTPException(
                        status_code=409,
                        detail="Dreaming community reports are unavailable",
                    )
                return {"items": await graph_loader()}
            latest = await latest_loader()
            if latest is not None and not latest.get("reports"):
                loader = getattr(
                    rag.chunk_entity_relation_graph,
                    "get_dreaming_community_reports",
                    None,
                )
                if callable(loader):
                    graph_reports = await loader(latest["snapshot_id"])
                    if graph_reports:
                        await memory_db.store_dreaming_community_reports(
                            latest["snapshot_id"], graph_reports
                        )
            page = await sqlite_list(page=1, page_size=100)
            return {"items": page["items"]}
        except Exception as exc:
            logger.error("Failed to read Dreaming community reports: %s", exc)
            raise internal_server_error(exc)

    @router.post(
        "/runs",
        dependencies=protected,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_dreaming_run(payload: DreamingRunRequest, request: Request):
        if os.environ.get("LIGHTRAG_GUNICORN_MODE"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Manual Dreaming currently requires the single-process server; "
                    "cross-worker job ownership is not enabled yet"
                ),
            )
        if runner_factory is DreamingRunner:
            runner = runner_factory(
                rag.chunk_entity_relation_graph,
                memory_db,
                llm_func=rag.role_llm_funcs.get("dream"),
            )
        else:
            runner = runner_factory(rag.chunk_entity_relation_graph, memory_db)
        try:
            runner.ensure_supported(rag.chunk_entity_relation_graph)
            config = payload.model_dump()
            run = await memory_db.start_dreaming_run(config)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to reserve Dreaming run: %s", exc)
            raise internal_server_error(exc)

        async def execute() -> None:
            try:
                await runner.run(run["run_id"], config)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The runner already persists the failure and full traceback.
                return

        task = asyncio.create_task(
            execute(), name=f"balthasar-dream-{run['run_id']}"
        )
        managed_tasks = request.app.state.background_tasks
        managed_tasks.add(task)
        task.add_done_callback(managed_tasks.discard)
        return run

    return router
