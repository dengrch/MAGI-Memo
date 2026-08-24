"""HTTP views and stateless exploration over MAGI's memory layer."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from magi_core.api.utils_api import (
    get_combined_auth_dependency,
    internal_server_error,
)
from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory.exploration import ExplorationReadError, MemoryExplorer
from magi_core.utils import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VisitRequest(BaseModel):
    """Only the selected graph identities needed for expansion de-duplication."""

    model_config = ConfigDict(extra="forbid")

    entities: list[str] = Field(default_factory=list, max_length=10_000)
    relations: list[tuple[str, str]] = Field(default_factory=list, max_length=10_000)

    @field_validator("entities")
    @classmethod
    def validate_entities(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("visit entity names must not be empty")
        return values

    @field_validator("relations")
    @classmethod
    def validate_relations(
        cls, values: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        if any(not source.strip() or not target.strip() for source, target in values):
            raise ValueError("visit relation endpoint names must not be empty")
        return values


class ExpandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier: list[str] = Field(min_length=1, max_length=200)
    visit: VisitRequest = Field(default_factory=VisitRequest)
    max_candidates_per_frontier: int = Field(default=50, ge=1, le=500)

    @field_validator("frontier")
    @classmethod
    def validate_frontier(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("frontier entity names must not be empty")
        return values


class EvidenceOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str | None = None
    relation: tuple[str, str] | None = None

    @model_validator(mode="after")
    def validate_owner(self) -> "EvidenceOwnerRequest":
        if (self.entity is None) == (self.relation is None):
            raise ValueError("exactly one of entity or relation is required")
        if self.entity is not None and not self.entity.strip():
            raise ValueError("entity name must not be empty")
        if self.relation is not None and any(
            not endpoint.strip() for endpoint in self.relation
        ):
            raise ValueError("relation endpoint names must not be empty")
        return self


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owners: list[EvidenceOwnerRequest] = Field(min_length=1, max_length=200)


def create_memory_routes(
    rag: Any, memory_db: SQLiteBackend, api_key: Optional[str] = None
) -> APIRouter:
    """Create memory-inspection and active-exploration routes."""

    router = APIRouter(prefix="/memory", tags=["memory"])
    combined_auth = get_combined_auth_dependency(api_key)
    protected = [Depends(combined_auth)]

    def explorer() -> MemoryExplorer:
        return MemoryExplorer(rag.chunk_entity_relation_graph, memory_db)

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

    @router.post("/explore/expand", dependencies=protected)
    async def expand_memory(request: ExpandRequest):
        """Expand entity frontiers by one complete graph hop after visit de-dup."""

        try:
            return await explorer().expand(
                frontier=request.frontier,
                visit_entities=request.visit.entities,
                visit_relations=request.visit.relations,
                max_candidates_per_frontier=request.max_candidates_per_frontier,
            )
        except ExplorationReadError as exc:
            logger.error("Failed to expand active-memory frontier: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to expand active-memory frontier: %s", exc)
            raise internal_server_error(exc)

    @router.post("/explore/evidence", dependencies=protected)
    async def get_memory_evidence(request: EvidenceRequest):
        """Read Atom evidence and evolution history by entity/relation names."""

        owners = [owner.model_dump(exclude_none=True) for owner in request.owners]
        try:
            return await explorer().evidence(owners)
        except ExplorationReadError as exc:
            logger.error("Failed to read active-memory evidence: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to read active-memory evidence: %s", exc)
            raise internal_server_error(exc)

    return router
