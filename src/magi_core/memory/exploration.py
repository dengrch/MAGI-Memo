"""Name-facing active graph exploration over materialized MAGI memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from magi_core.memory.models import (
    normalize_name,
    stable_entity_id,
    stable_relation_id,
)


class ExplorationReadError(RuntimeError):
    """Raised when materialized graph state cannot be read consistently."""


def _clean_name(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ValueError("entity names must not be empty")
    return cleaned


def _normalized_pair(source: str, target: str) -> tuple[str, str]:
    return tuple(sorted((normalize_name(source), normalize_name(target))))


def _display_pair(source: str, target: str) -> tuple[str, str]:
    return tuple(sorted((_clean_name(source), _clean_name(target))))


@dataclass(frozen=True, slots=True)
class _ResolvedEntity:
    name: str
    stable_id: str
    graph: dict[str, Any]
    record: Any


class MemoryExplorer:
    """Implement stateless expansion, description, and evidence lookup.

    Entity names are the public addressing surface. Stable MAGI ids remain an
    internal validation mechanism and are included only in the raw Core result,
    so clients may remove them before exposing results to a model.
    """

    def __init__(self, graph: Any, memory_db: Any) -> None:
        self.graph = graph
        self.memory_db = memory_db

    async def _resolve_entity(self, name: str) -> _ResolvedEntity | None:
        requested = _clean_name(name)
        node = await self.graph.get_node(requested)
        if node is None:
            return None
        canonical = str(node.get("entity_id") or requested)
        stable_id = str(
            node.get("magi_entity_id")
            or stable_entity_id(self.memory_db.workspace_id, canonical)
        )
        record = await self.memory_db.get_entity(stable_id)
        if record is None:
            raise ExplorationReadError(
                f"entity {canonical!r} has no matching MAGI stable record"
            )
        if normalize_name(record.canonical_name) != normalize_name(canonical):
            raise ExplorationReadError(
                f"entity {canonical!r} resolves to a mismatched MAGI stable record"
            )
        return _ResolvedEntity(canonical, stable_id, dict(node), record)

    async def _resolve_relation(
        self, source: str, target: str
    ) -> tuple[tuple[str, str], str, dict[str, Any], Any] | None:
        source_entity = await self._resolve_entity(source)
        target_entity = await self._resolve_entity(target)
        if source_entity is None or target_entity is None:
            return None
        endpoints = _display_pair(source_entity.name, target_entity.name)
        edge = await self.graph.get_edge(*endpoints)
        if edge is None:
            return None
        stable_id = str(
            edge.get("magi_relation_id")
            or stable_relation_id(
                self.memory_db.workspace_id,
                source_entity.stable_id,
                target_entity.stable_id,
            )
        )
        record = await self.memory_db.get_relation(stable_id)
        if record is None:
            raise ExplorationReadError(
                f"relation {endpoints!r} has no matching MAGI stable record"
            )
        record_pair = _normalized_pair(
            record.entity_a_name, record.entity_b_name
        )
        if record_pair != _normalized_pair(*endpoints):
            raise ExplorationReadError(
                f"relation {endpoints!r} resolves to a mismatched MAGI stable record"
            )
        return endpoints, stable_id, dict(edge), record

    async def expand(
        self,
        *,
        frontier: Sequence[str],
        visit_entities: Iterable[str],
        visit_relations: Iterable[tuple[str, str]],
        max_candidates_per_frontier: int,
    ) -> dict[str, Any]:
        """Return complete one-hop candidates after visit-based de-duplication."""

        visited_entities = {normalize_name(name) for name in visit_entities}
        visited_relations = {
            _normalized_pair(source, target)
            for source, target in visit_relations
        }
        unique_frontier: list[str] = []
        frontier_keys: set[str] = set()
        for raw_name in frontier:
            name = _clean_name(raw_name)
            key = normalize_name(name)
            if key not in frontier_keys:
                frontier_keys.add(key)
                unique_frontier.append(name)

        results: list[dict[str, Any]] = []
        missing: list[str] = []
        any_truncated = False
        total_available = 0
        total_returned = 0
        for requested in unique_frontier:
            resolved = await self._resolve_entity(requested)
            if resolved is None:
                missing.append(requested)
                results.append(
                    {
                        "frontier": requested,
                        "state": "not_found",
                        "complete": False,
                        "truncated": False,
                        "available_count": 0,
                        "returned_count": 0,
                        "candidates": [],
                    }
                )
                continue

            edges = await self.graph.get_node_edges(resolved.name)
            if edges is None:
                raise ExplorationReadError(
                    f"one-hop expansion failed for existing entity {resolved.name!r}"
                )

            candidates: list[dict[str, Any]] = []
            candidate_keys: set[tuple[str, str]] = set()
            for edge_source, edge_target in edges:
                neighbor_name = (
                    edge_target
                    if normalize_name(edge_source) == normalize_name(resolved.name)
                    else edge_source
                )
                relation_pair = _display_pair(edge_source, edge_target)
                dedupe_key = (
                    normalize_name(neighbor_name),
                    "\x00".join(_normalized_pair(*relation_pair)),
                )
                if dedupe_key in candidate_keys:
                    continue
                candidate_keys.add(dedupe_key)
                if (
                    normalize_name(neighbor_name) in visited_entities
                    and _normalized_pair(*relation_pair) in visited_relations
                ):
                    continue

                neighbor = await self._resolve_entity(neighbor_name)
                relation = await self._resolve_relation(*relation_pair)
                if neighbor is None or relation is None:
                    raise ExplorationReadError(
                        f"one-hop edge {relation_pair!r} is missing materialized MAGI data"
                    )
                endpoints, _relation_id, edge, _record = relation
                candidates.append(
                    {
                        "entity": {
                            "name": neighbor.name,
                            "entity_type": neighbor.graph.get("entity_type", "UNKNOWN"),
                        },
                        "relation": {
                            "endpoints": list(endpoints),
                            "keywords": edge.get("keywords", ""),
                        },
                    }
                )

            available_count = len(candidates)
            returned = candidates[:max_candidates_per_frontier]
            truncated = available_count > len(returned)
            any_truncated = any_truncated or truncated
            total_available += available_count
            total_returned += len(returned)
            results.append(
                {
                    "frontier": resolved.name,
                    "state": "expanded",
                    "complete": not truncated,
                    "truncated": truncated,
                    "available_count": available_count,
                    "returned_count": len(returned),
                    "candidates": returned,
                }
            )

        status = "partial" if missing else "truncated" if any_truncated else "complete"
        exhausted = (
            not missing
            and not any_truncated
            and total_available == 0
        )
        return {
            "status": status,
            "exhausted": exhausted,
            "truncated": any_truncated,
            "missing_frontier": missing,
            "available_count": total_available,
            "returned_count": total_returned,
            "results": results,
        }

    async def describe(self, owners: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Return materialized graph descriptions for name-addressed owners."""

        found: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for owner in owners:
            if "entity" in owner:
                name = _clean_name(str(owner["entity"]))
                resolved = await self._resolve_entity(name)
                if resolved is None:
                    missing.append({"entity": name})
                    continue
                found.append(
                    {
                        "owner": {"type": "entity", "name": resolved.name},
                        "entity_type": resolved.graph.get(
                            "entity_type", "UNKNOWN"
                        ),
                        "description": resolved.graph.get("description", ""),
                    }
                )
                continue

            source, target = owner["relation"]
            relation = await self._resolve_relation(source, target)
            if relation is None:
                missing.append(
                    {"relation": list(_display_pair(source, target))}
                )
                continue
            endpoints, _owner_id, edge, _record = relation
            found.append(
                {
                    "owner": {"type": "relation", "name": list(endpoints)},
                    "keywords": edge.get("keywords", ""),
                    "description": edge.get("description", ""),
                }
            )

        return {
            "status": "partial" if missing else "complete",
            "missing_owners": missing,
            "owners": found,
        }

    async def evidence(self, owners: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Return Atom evidence and evolution history for name-addressed owners."""

        found: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for owner in owners:
            if "entity" in owner:
                name = _clean_name(str(owner["entity"]))
                resolved = await self._resolve_entity(name)
                if resolved is None:
                    missing.append({"entity": name})
                    continue
                owner_type = "entity"
                owner_name: str | list[str] = resolved.name
                owner_id = resolved.stable_id
            else:
                source, target = owner["relation"]
                relation = await self._resolve_relation(source, target)
                if relation is None:
                    missing.append({"relation": list(_display_pair(source, target))})
                    continue
                endpoints, owner_id, _edge, _record = relation
                owner_type = "relation"
                owner_name = list(endpoints)

            atoms = await self.memory_db.list_owner_atoms(owner_id)
            atom_views: list[dict[str, Any]] = []
            for atom in atoms:
                view = await self.memory_db.get_atom_memory_view(atom.id)
                if view is None:
                    raise ExplorationReadError(
                        f"atom {atom.id!r} disappeared while reading owner evidence"
                    )
                evolutions = await self.memory_db.list_atom_evolutions(atom.id)
                enriched_evolutions: list[dict[str, Any]] = []
                for evolution in evolutions:
                    related_id = (
                        evolution["target_atom_id"]
                        if evolution["source_atom_id"] == atom.id
                        else evolution["source_atom_id"]
                    )
                    related = await self.memory_db.get_atom_memory_view(related_id)
                    enriched_evolutions.append(
                        {
                            **evolution,
                            "direction": (
                                "outgoing"
                                if evolution["source_atom_id"] == atom.id
                                else "incoming"
                            ),
                            "related_atom": None
                            if related is None
                            else {
                                "atom_id": related.get("atom_id"),
                                "content": related.get("content"),
                                "temporal_status": related.get("temporal_status"),
                                "valid_at": related.get("valid_at"),
                                "invalid_at": related.get("invalid_at"),
                            },
                        }
                    )
                atom_views.append({**view, "evolutions": enriched_evolutions})

            found.append(
                {
                    "owner": {"type": owner_type, "name": owner_name},
                    "magi_owner_id": owner_id,
                    "atoms": atom_views,
                }
            )

        return {
            "status": "partial" if missing else "complete",
            "missing_owners": missing,
            "owners": found,
        }
