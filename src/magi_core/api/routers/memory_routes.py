"""Read-only HTTP views over MAGI's local Episode/Atom record layer."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from magi_core.api.utils_api import (
    get_combined_auth_dependency,
    internal_server_error,
)
from magi_core.backend.sqlite import SQLiteBackend
from magi_core.utils import logger


def create_memory_routes(
    memory_db: SQLiteBackend, api_key: Optional[str] = None
) -> APIRouter:
    """Create the SQLite memory-inspection routes used by the WebUI."""

    router = APIRouter(prefix="/memory", tags=["memory"])
    combined_auth = get_combined_auth_dependency(api_key)
    protected = [Depends(combined_auth)]

    @router.get("/overview", dependencies=protected)
    async def get_memory_overview():
        try:
            return await memory_db.memory_overview()
        except Exception as exc:
            logger.error("Failed to read memory overview: %s", exc)
            raise internal_server_error(exc)

    @router.get("/episodes", dependencies=protected)
    async def list_episodes(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        status: Literal["pending", "indexed", "failed"] | None = None,
        kind: Literal["document", "conversation", "multimodal_text"] | None = None,
        q: str | None = Query(None, max_length=500),
    ):
        try:
            return await memory_db.list_episodes(
                page=page,
                page_size=page_size,
                status=status,
                kind=kind,
                query=q,
            )
        except Exception as exc:
            logger.error("Failed to list Episodes: %s", exc)
            raise internal_server_error(exc)

    @router.get("/episodes/{episode_id}", dependencies=protected)
    async def get_episode(episode_id: str):
        try:
            result = await memory_db.get_episode_memory(episode_id)
        except Exception as exc:
            logger.error("Failed to read Episode %s: %s", episode_id, exc)
            raise internal_server_error(exc)
        if result is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        return result

    @router.get("/atoms", dependencies=protected)
    async def list_atoms(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        owner_type: Literal["entity", "relation"] | None = None,
        temporal_status: Literal["pending", "active", "invalid", "expired"]
        | None = None,
        q: str | None = Query(None, max_length=500),
        episode_id: str | None = Query(None, max_length=200),
    ):
        try:
            return await memory_db.list_atoms(
                page=page,
                page_size=page_size,
                owner_type=owner_type,
                temporal_status=temporal_status,
                query=q,
                episode_id=episode_id,
            )
        except Exception as exc:
            logger.error("Failed to list Atoms: %s", exc)
            raise internal_server_error(exc)

    @router.get("/atoms/{atom_id}", dependencies=protected)
    async def get_atom(atom_id: str):
        try:
            result = await memory_db.get_atom_memory_view(atom_id)
        except Exception as exc:
            logger.error("Failed to read Atom %s: %s", atom_id, exc)
            raise internal_server_error(exc)
        if result is None:
            raise HTTPException(status_code=404, detail="Atom not found")
        return result

    return router
