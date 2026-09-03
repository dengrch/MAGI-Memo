"""HTTP views and stateless exploration over MAGI's memory layer."""

from __future__ import annotations

from datetime import datetime
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


class ExplorationTraceRequest(BaseModel):
    """Server-owned visualization identity automatically supplied by a caller."""

    model_config = ConfigDict(extra="forbid")

    exploration_id: str = Field(min_length=1, max_length=200)
    agent_id: str = Field(min_length=1, max_length=200)
    call_id: str = Field(min_length=1, max_length=200)
    subquery: str | None = Field(default=None, max_length=10_000)

    @field_validator("exploration_id", "agent_id", "call_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("trace identities must not be empty")
        return normalized

    @field_validator("subquery")
    @classmethod
    def validate_subquery(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ExpandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier: list[str] = Field(min_length=1, max_length=200)
    visit: VisitRequest = Field(default_factory=VisitRequest)
    max_candidates_per_frontier: int = Field(default=50, ge=1, le=500)
    trace: ExplorationTraceRequest | None = None

    @field_validator("frontier")
    @classmethod
    def validate_frontier(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("frontier entity names must not be empty")
        return values


class ExplorationRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exploration_id: str = Field(min_length=1, max_length=200)
    query: str | None = Field(default=None, max_length=10_000)
    initial_visit: VisitRequest | None = None

    @field_validator("exploration_id")
    @classmethod
    def validate_exploration_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("exploration_id must not be empty")
        return normalized

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ExplorationOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str | None = None
    relation: tuple[str, str] | None = None

    @model_validator(mode="after")
    def validate_owner(self) -> "ExplorationOwnerRequest":
        if (self.entity is None) == (self.relation is None):
            raise ValueError("exactly one of entity or relation is required")
        if self.entity is not None and not self.entity.strip():
            raise ValueError("entity name must not be empty")
        if self.relation is not None and any(
            not endpoint.strip() for endpoint in self.relation
        ):
            raise ValueError("relation endpoint names must not be empty")
        return self


class ExplorationOwnersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owners: list[ExplorationOwnerRequest] = Field(min_length=1, max_length=200)


class AtomConflictResolutionRequest(BaseModel):
    """Explicit operator choice for one persisted Atom contradiction."""

    model_config = ConfigDict(extra="forbid")

    source_atom_id: str = Field(min_length=1, max_length=200)
    target_atom_id: str = Field(min_length=1, max_length=200)
    winner_atom_id: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_conflict_pair(self) -> "AtomConflictResolutionRequest":
        if self.source_atom_id == self.target_atom_id:
            raise ValueError("conflicting Atom ids must be different")
        if self.winner_atom_id not in {
            self.source_atom_id,
            self.target_atom_id,
        }:
            raise ValueError("winner_atom_id must be one of the conflicting Atoms")
        if self.note is not None:
            self.note = self.note.strip() or None
        return self


class EntityAliasResolutionRequest(BaseModel):
    """Explicit operator choice for one ambiguous normalized alias."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=500)
    winner_entity_id: str = Field(min_length=1, max_length=200)

    @field_validator("alias", "winner_entity_id")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


def create_memory_routes(
    rag: Any, memory_db: SQLiteBackend, api_key: Optional[str] = None
) -> APIRouter:
    """Create memory-inspection and active-exploration routes."""

    router = APIRouter(prefix="/memory", tags=["memory"])
    combined_auth = get_combined_auth_dependency(api_key)
    protected = [Depends(combined_auth)]

    def explorer() -> MemoryExplorer:
        return MemoryExplorer(rag.chunk_entity_relation_graph, memory_db)

    async def sync_latest_community_reports() -> None:
        """One-time backfill for snapshots created before SQLite stored reports."""

        latest = await memory_db.get_latest_dreaming_memberships()
        if latest is None or latest.get("reports"):
            return
        loader = getattr(
            rag.chunk_entity_relation_graph,
            "get_dreaming_community_reports",
            None,
        )
        if not callable(loader):
            return
        reports = await loader(latest["snapshot_id"])
        if reports:
            await memory_db.store_dreaming_community_reports(
                latest["snapshot_id"], reports
            )

    async def visit_trace_payload(visit: VisitRequest) -> dict[str, Any]:
        """Enrich identity-only visit state for visualization replay."""

        metadata: list[dict[str, Any]] = []
        try:
            nodes = await rag.chunk_entity_relation_graph.get_nodes_batch(
                list(dict.fromkeys(visit.entities))
            )
            for requested in visit.entities:
                node = nodes.get(requested)
                if node is None:
                    continue
                metadata.append(
                    {
                        "name": str(node.get("entity_id") or requested),
                        "entity_type": node.get("entity_type", "UNKNOWN"),
                        "description": node.get("description", ""),
                    }
                )
        except Exception as exc:
            # Trace enrichment is observational and must never make expand fail.
            logger.warning("Failed to enrich exploration visit trace: %s", exc)
        return {
            "entities": visit.entities,
            "relations": visit.relations,
            "entity_metadata": metadata,
        }

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
        reference_from: datetime | None = None,
        reference_to: datetime | None = None,
    ):
        try:
            return await memory_db.list_episodes(
                page=page,
                page_size=page_size,
                status=status,
                kind=kind,
                query=q,
                reference_from=(
                    reference_from.isoformat() if reference_from else None
                ),
                reference_to=reference_to.isoformat() if reference_to else None,
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

    @router.get("/entities", dependencies=protected)
    async def list_entities(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        q: str | None = Query(None, max_length=500),
    ):
        try:
            return await memory_db.list_entities(
                page=page,
                page_size=page_size,
                query=q,
            )
        except Exception as exc:
            logger.error("Failed to list memory entities: %s", exc)
            raise internal_server_error(exc)

    @router.get("/entities/{entity_id}", dependencies=protected)
    async def get_entity(entity_id: str):
        try:
            result = await memory_db.get_entity_memory_view(entity_id)
        except Exception as exc:
            logger.error("Failed to read memory entity %s: %s", entity_id, exc)
            raise internal_server_error(exc)
        if result is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        return result

    @router.post("/entities/aliases/resolve", dependencies=protected)
    async def resolve_entity_alias(payload: EntityAliasResolutionRequest):
        try:
            return await memory_db.resolve_entity_alias(
                payload.alias, payload.winner_entity_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to resolve entity alias %s: %s", payload.alias, exc)
            raise internal_server_error(exc)

    @router.get("/relations", dependencies=protected)
    async def list_relations(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        q: str | None = Query(None, max_length=500),
    ):
        try:
            return await memory_db.list_relations(
                page=page, page_size=page_size, query=q
            )
        except Exception as exc:
            logger.error("Failed to list memory relations: %s", exc)
            raise internal_server_error(exc)

    @router.get("/relations/{relation_id}", dependencies=protected)
    async def get_relation(relation_id: str):
        try:
            result = await memory_db.get_relation_memory_view(relation_id)
        except Exception as exc:
            logger.error("Failed to read memory relation %s: %s", relation_id, exc)
            raise internal_server_error(exc)
        if result is None:
            raise HTTPException(status_code=404, detail="Relation not found")
        return result

    @router.get("/communities", dependencies=protected)
    async def list_communities(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        q: str | None = Query(None, max_length=500),
    ):
        try:
            await sync_latest_community_reports()
            return await memory_db.list_communities(
                page=page, page_size=page_size, query=q
            )
        except Exception as exc:
            logger.error("Failed to list memory communities: %s", exc)
            raise internal_server_error(exc)

    @router.get("/communities/{community_id}", dependencies=protected)
    async def get_community(community_id: str):
        try:
            await sync_latest_community_reports()
            result = await memory_db.get_community_memory_view(community_id)
        except Exception as exc:
            logger.error("Failed to read memory community %s: %s", community_id, exc)
            raise internal_server_error(exc)
        if result is None:
            raise HTTPException(status_code=404, detail="Community not found")
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
        evolution_type: Literal[
            "any", "REFINEMENT", "TEMPORAL_SUCCESSOR", "CONTRADICTION"
        ]
        | None = None,
        valid_time_from: datetime | None = None,
        valid_time_to: datetime | None = None,
        system_time_from: datetime | None = None,
        system_time_to: datetime | None = None,
    ):
        try:
            return await memory_db.list_atoms(
                page=page,
                page_size=page_size,
                owner_type=owner_type,
                temporal_status=temporal_status,
                query=q,
                episode_id=episode_id,
                evolution_type=evolution_type,
                valid_time_from=(
                    valid_time_from.isoformat() if valid_time_from else None
                ),
                valid_time_to=valid_time_to.isoformat() if valid_time_to else None,
                system_time_from=(
                    system_time_from.isoformat() if system_time_from else None
                ),
                system_time_to=system_time_to.isoformat() if system_time_to else None,
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

    @router.post("/atoms/conflicts/resolve", dependencies=protected)
    async def resolve_atom_conflict(request: AtomConflictResolutionRequest):
        try:
            return await memory_db.resolve_atom_conflict(
                request.source_atom_id,
                request.target_atom_id,
                request.winner_atom_id,
                note=request.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to resolve Atom conflict: %s", exc)
            raise internal_server_error(exc)

    @router.post("/explore/expand", dependencies=protected)
    async def expand_memory(request: ExpandRequest):
        """Expand entity frontiers by one complete graph hop after visit de-dup."""

        trace = request.trace
        if trace is not None:
            visit_payload = await visit_trace_payload(request.visit)
            await memory_db.append_exploration_event(
                exploration_id=trace.exploration_id,
                event_type="expand_started",
                agent_id=trace.agent_id,
                call_id=trace.call_id,
                payload={
                    "frontier": request.frontier,
                    "visit": visit_payload,
                    **(
                        {"subquery": trace.subquery}
                        if trace.subquery is not None
                        else {}
                    ),
                },
            )
        try:
            result = await explorer().expand(
                frontier=request.frontier,
                visit_entities=request.visit.entities,
                visit_relations=request.visit.relations,
                max_candidates_per_frontier=request.max_candidates_per_frontier,
            )
        except ExplorationReadError as exc:
            if trace is not None:
                await memory_db.append_exploration_event(
                    exploration_id=trace.exploration_id,
                    event_type="expand_failed",
                    agent_id=trace.agent_id,
                    call_id=trace.call_id,
                    payload={"frontier": request.frontier, "error": str(exc)},
                )
            logger.error("Failed to expand active-memory frontier: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            if trace is not None:
                await memory_db.append_exploration_event(
                    exploration_id=trace.exploration_id,
                    event_type="expand_failed",
                    agent_id=trace.agent_id,
                    call_id=trace.call_id,
                    payload={"frontier": request.frontier, "error": str(exc)},
                )
            logger.error("Failed to expand active-memory frontier: %s", exc)
            raise internal_server_error(exc)
        if trace is not None:
            await memory_db.append_exploration_event(
                exploration_id=trace.exploration_id,
                event_type="expand_completed",
                agent_id=trace.agent_id,
                call_id=trace.call_id,
                payload={"frontier": request.frontier, "result": result},
            )
        return result

    @router.post("/explore/traces", dependencies=protected)
    async def register_exploration(request: ExplorationRegistrationRequest):
        """Register human-readable metadata for an exploration identity."""

        try:
            trace = await memory_db.register_exploration_trace(
                request.exploration_id, request.query
            )
            if request.initial_visit is not None:
                existing = await memory_db.get_exploration_events(
                    request.exploration_id, after_seq=0, limit=1
                )
                if not existing:
                    await memory_db.append_exploration_event(
                        exploration_id=request.exploration_id,
                        event_type="exploration_initialized",
                        agent_id=request.exploration_id,
                        call_id="initial",
                        payload={
                            "visit": await visit_trace_payload(
                                request.initial_visit
                            )
                        },
                    )
            return trace
        except Exception as exc:
            logger.error("Failed to register exploration trace: %s", exc)
            raise internal_server_error(exc)

    @router.get("/explore/traces", dependencies=protected)
    async def list_exploration_traces(
        include_archived: bool = False,
        limit: int = Query(50, ge=1, le=200),
    ):
        try:
            items = await memory_db.list_exploration_traces(
                include_archived=include_archived, limit=limit
            )
            return {"items": items}
        except Exception as exc:
            logger.error("Failed to list exploration traces: %s", exc)
            raise internal_server_error(exc)

    @router.get(
        "/explore/traces/{exploration_id}/events", dependencies=protected
    )
    async def list_exploration_events(
        exploration_id: str,
        after_seq: int = Query(0, ge=0),
        limit: int = Query(500, ge=1, le=2_000),
    ):
        try:
            events = await memory_db.get_exploration_events(
                exploration_id, after_seq=after_seq, limit=limit
            )
            return {
                "events": events,
                "last_seq": events[-1]["seq"] if events else after_seq,
            }
        except Exception as exc:
            logger.error("Failed to list exploration events: %s", exc)
            raise internal_server_error(exc)

    @router.post(
        "/explore/traces/{exploration_id}/archive", dependencies=protected
    )
    async def archive_exploration_trace(exploration_id: str):
        try:
            archived = await memory_db.archive_exploration_trace(exploration_id)
        except Exception as exc:
            logger.error("Failed to archive exploration trace: %s", exc)
            raise internal_server_error(exc)
        if not archived:
            raise HTTPException(status_code=404, detail="Exploration trace not found")
        return {"archived": True}

    @router.post("/explore/describe", dependencies=protected)
    async def describe_memory_owners(request: ExplorationOwnersRequest):
        """Read lightweight graph descriptions by entity/relation names."""

        owners = [owner.model_dump(exclude_none=True) for owner in request.owners]
        try:
            return await explorer().describe(owners)
        except ExplorationReadError as exc:
            logger.error("Failed to describe active-memory owners: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to describe active-memory owners: %s", exc)
            raise internal_server_error(exc)

    @router.post("/explore/evidence", dependencies=protected)
    async def get_memory_evidence(request: ExplorationOwnersRequest):
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
