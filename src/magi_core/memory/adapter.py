"""Strict Episode -> Atom -> graph materialization adapter."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Sequence
from uuid import uuid4

import numpy as np

from magi_core.ingestion import KnowledgeCommitContext
from magi_core.constants import GRAPH_FIELD_SEP
from magi_core.memory.decisions import (
    LLMMemoryDecisionProvider,
    MemoryDecisionProvider,
)
from magi_core.memory.models import (
    AtomClassification,
    AtomDecision,
    AtomEvidence,
    AtomRecord,
    AtomResolutionRequest,
    CandidateMatch,
    EntityRecord,
    EntityResolution,
    EntityResolutionRequest,
    Episode,
    EpisodeStatus,
    RelationRecord,
    normalize_name,
    stable_relation_id,
    stable_atom_id,
    stable_entity_name_embedding_id,
    utc_now,
)
from magi_core.operate import _truncate_vdb_content, merge_nodes_and_edges
from magi_core.utils import compute_mdhash_id

if TYPE_CHECKING:
    from magi_core.backend.sqlite import SQLiteBackend


def _parse_optional_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _score(value: Any) -> float | None:
    if value is None:
        return None
    return min(1.0, max(0.0, float(value)))


class MagiKnowledgeAdapter:
    """Own strict disambiguation and Atom semantics for one workspace."""

    def __init__(
        self,
        sqlite: "SQLiteBackend",
        *,
        decision_provider: MemoryDecisionProvider | None = None,
        entity_similarity_threshold: float = 0.72,
        candidate_limit: int = 8,
        atom_full_context_limit: int = 16,
        atom_candidate_limit: int = 8,
        atom_similarity_threshold: float = 0.45,
    ) -> None:
        self.sqlite = sqlite
        self.decision_provider = decision_provider
        self.entity_similarity_threshold = entity_similarity_threshold
        self.candidate_limit = candidate_limit
        self.atom_full_context_limit = atom_full_context_limit
        self.atom_candidate_limit = atom_candidate_limit
        self.atom_similarity_threshold = atom_similarity_threshold
        self._episode_contexts: dict[str, dict[str, Any]] = {}
        self._commit_lock = asyncio.Lock()

    def register_episodes(self, episodes: Sequence[Episode]) -> None:
        for episode in episodes:
            self._episode_contexts[episode.id] = {
                "episode_id": episode.id,
                "reference_at": episode.effective_reference_at.isoformat(),
                "kind": episode.kind.value,
                "source_uri": episode.source_uri,
            }

    def get_extraction_context(self, doc_id: str | None) -> dict[str, Any]:
        return dict(self._episode_contexts.get(doc_id or "", {}))

    async def prepare_episode(
        self,
        *,
        doc_id: str,
        content: str,
        file_path: str,
        reference_at: str | None = None,
    ) -> None:
        existing = await self.sqlite.get_episode_model(doc_id)
        if existing is None:
            episode = Episode(
                id=doc_id,
                content=content,
                reference_at=_parse_optional_time(reference_at) or utc_now(),
                source_uri=file_path,
            )
            await self.sqlite.put_episode(episode)
        else:
            if (
                existing.content_hash
                != Episode(
                    id=doc_id,
                    content=content,
                    reference_at=existing.reference_at,
                    source_uri=existing.source_uri,
                ).content_hash
            ):
                raise ValueError(
                    f"Episode {doc_id!r} already exists with different content"
                )
            episode = existing
            record = await self.sqlite.get_episode(doc_id)
            if record and record["status"] == EpisodeStatus.FAILED.value:
                await self.sqlite.mark_episode(doc_id, EpisodeStatus.PENDING)
        self.register_episodes([episode])

    async def complete_episode(
        self, doc_id: str, *, track_id: str | None = None
    ) -> None:
        if await self.sqlite.get_episode(doc_id) is None:
            return
        await self.sqlite.mark_episode(doc_id, EpisodeStatus.INDEXED, track_id=track_id)
        self._episode_contexts.pop(doc_id, None)

    async def fail_episode(self, doc_id: str, *, error: str) -> None:
        if await self.sqlite.get_episode(doc_id) is None:
            return
        await self.sqlite.mark_episode(doc_id, EpisodeStatus.FAILED, error=error)
        self._episode_contexts.pop(doc_id, None)

    async def delete_episode(self, doc_id: str, *, rag: Any) -> str | None:
        """Delete SQLite evidence and refresh surviving graph projections."""

        result = await self.sqlite.backup_and_delete_episode(doc_id)
        if result is None:
            return None
        global_config = rag._build_global_config()
        graph = rag.chunk_entity_relation_graph
        for owner_id in result["affected_owner_ids"]:
            atoms = await self.sqlite.list_owner_atoms(owner_id)
            active_atoms = await self.sqlite.list_owner_atoms(
                owner_id, active_only=True
            )
            description = GRAPH_FIELD_SEP.join(
                atom.presentation() for atom in active_atoms
            )
            atom_ids = [atom.id for atom in atoms]
            if owner_id.startswith("entity-"):
                entity = await self.sqlite.get_entity(owner_id)
                if entity is None:
                    continue
                node = await graph.get_node(entity.canonical_name)
                if node is None:
                    continue
                node = {
                    **node,
                    "description": description,
                    "magi_entity_id": entity.id,
                    "atom_ids": atom_ids,
                }
                await graph.upsert_node(entity.canonical_name, node_data=node)
                if rag.entities_vdb is not None:
                    content = _truncate_vdb_content(
                        f"{entity.canonical_name}\n{description}",
                        global_config,
                        f"entity:{entity.canonical_name}",
                    )
                    await rag.entities_vdb.upsert(
                        {
                            compute_mdhash_id(entity.canonical_name, prefix="ent-"): {
                                "content": content,
                                "entity_name": entity.canonical_name,
                                "entity_type": node.get("entity_type", "UNKNOWN"),
                                "source_id": node.get("source_id", ""),
                                "file_path": node.get("file_path", "unknown_source"),
                            }
                        }
                    )
                continue

            relation = await self.sqlite.get_relation(owner_id)
            if relation is None:
                continue
            source, target = sorted((relation.entity_a_name, relation.entity_b_name))
            edge = await graph.get_edge(source, target)
            if edge is None:
                continue
            edge = {
                **edge,
                "description": description,
                "magi_relation_id": relation.id,
                "atom_ids": atom_ids,
            }
            await graph.upsert_edge(source, target, edge_data=edge)
            if rag.relationships_vdb is not None:
                relation_id = compute_mdhash_id(source + target, prefix="rel-")
                reverse_id = compute_mdhash_id(target + source, prefix="rel-")
                await rag.relationships_vdb.delete([relation_id, reverse_id])
                keywords = edge.get("keywords", "")
                content = _truncate_vdb_content(
                    f"{keywords}\t{source}\n{target}\n{description}",
                    global_config,
                    f"relationship:{source}-{target}",
                )
                await rag.relationships_vdb.upsert(
                    {
                        relation_id: {
                            "src_id": source,
                            "tgt_id": target,
                            "source_id": edge.get("source_id", ""),
                            "content": content,
                            "keywords": keywords,
                            "description": description,
                            "weight": edge.get("weight", 1.0),
                            "file_path": edge.get("file_path", "unknown_source"),
                        }
                    }
                )
        return str(result["backup_id"])

    async def clear_memory(self) -> list[str]:
        self._episode_contexts.clear()
        return await self.sqlite.backup_and_clear()

    @staticmethod
    def _embedding_model(engine: Any) -> str:
        return (
            getattr(engine.embedding_func, "model_name", None)
            or f"embedding-{engine.embedding_func.embedding_dim}d"
        )

    @staticmethod
    async def _embed(engine: Any, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, engine.embedding_func.embedding_dim), dtype=np.float32)
        return np.asarray(
            await engine.embedding_func(list(texts), context="document"),
            dtype=np.float32,
        )

    async def _entity_candidates(
        self,
        *,
        names_and_vectors: Sequence[tuple[str, np.ndarray]],
        model_name: str,
    ) -> list[CandidateMatch]:
        combined: dict[str, CandidateMatch] = {}
        for name, vector in names_and_vectors:
            for candidate in await self.sqlite.search_entity_names(
                name, limit=self.candidate_limit
            ):
                current = combined.get(candidate.object_id)
                if current is None or candidate.score > current.score:
                    combined[candidate.object_id] = candidate
            for candidate in await self.sqlite.search_embeddings(
                object_kind="entity_name",
                query_vector=vector,
                model_name=model_name,
                limit=self.candidate_limit,
                threshold=self.entity_similarity_threshold,
            ):
                current = combined.get(candidate.object_id)
                if current is None or candidate.score > current.score:
                    combined[candidate.object_id] = candidate

        enriched: list[CandidateMatch] = []
        for candidate in combined.values():
            entity = await self.sqlite.get_entity(candidate.object_id)
            if entity is None:
                continue
            atoms = await self.sqlite.list_owner_atoms(entity.id)
            evidence_text = "\n".join(atom.presentation() for atom in atoms[-8:])
            enriched.append(
                CandidateMatch(
                    object_id=entity.id,
                    score=candidate.score,
                    text=(
                        f"{entity.canonical_name}\n{evidence_text}"
                        if evidence_text
                        else entity.canonical_name
                    ),
                    match_kind=candidate.match_kind,
                )
            )
        enriched.sort(key=lambda item: item.score, reverse=True)
        return enriched[: self.candidate_limit]

    @staticmethod
    def _episode_aliases(
        name: str, records: Sequence[dict[str, Any]]
    ) -> tuple[str, ...]:
        aliases = [name]
        for record in records:
            raw_aliases = record.get("aliases", [])
            if isinstance(raw_aliases, list):
                aliases.extend(
                    str(alias) for alias in raw_aliases if str(alias).strip()
                )
        return tuple(dict.fromkeys(aliases))

    @classmethod
    def _coalesce_episode_entities(
        cls,
        all_nodes: dict[str, list[dict[str, Any]]],
        all_edges: dict[tuple[str, str], list[dict[str, Any]]],
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[tuple[str, str], list[dict[str, Any]]],
    ]:
        """Merge entity containers that share an explicit Episode-local alias."""

        names = list(all_nodes)
        parent = {name: name for name in names}

        def find(name: str) -> str:
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name

        def union(left: str, right: str) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        alias_owner: dict[str, str] = {}
        for name in names:
            for alias in cls._episode_aliases(name, all_nodes[name]):
                normalized = normalize_name(alias)
                if normalized in alias_owner:
                    union(alias_owner[normalized], name)
                else:
                    alias_owner[normalized] = name

        representative = {name: find(name) for name in names}
        merged_nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        group_aliases: dict[str, list[str]] = defaultdict(list)
        for name in names:
            root = representative[name]
            group_aliases[root].extend(cls._episode_aliases(name, all_nodes[name]))
            merged_nodes[root].extend(all_nodes[name])
        for root, records in merged_nodes.items():
            aliases = list(dict.fromkeys(group_aliases[root]))
            for record in records:
                record["aliases"] = aliases

        merged_edges: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for (source, target), records in all_edges.items():
            source = representative.get(source, source)
            target = representative.get(target, target)
            if source == target:
                continue
            pair = (source, target)
            for record in records:
                record["src_id"] = source
                record["tgt_id"] = target
            merged_edges[pair].extend(records)
        return dict(merged_nodes), dict(merged_edges)

    async def _resolve_entities(
        self,
        *,
        engine: Any,
        all_nodes: dict[str, list[dict[str, Any]]],
    ) -> tuple[dict[str, EntityRecord], dict[str, np.ndarray]]:
        names = list(all_nodes)
        aliases_by_name = {
            name: self._episode_aliases(name, all_nodes[name]) for name in names
        }
        lookup_names = list(
            dict.fromkeys(
                alias for aliases in aliases_by_name.values() for alias in aliases
            )
        )
        vectors = await self._embed(engine, lookup_names)
        vector_by_name = dict(zip(lookup_names, vectors))
        model_name = self._embedding_model(engine)
        provider = self.decision_provider or LLMMemoryDecisionProvider(engine)
        recent_episodes = await self.sqlite.recent_episode_context(limit=4)
        resolved: dict[str, EntityRecord] = {}
        requests: list[EntityResolutionRequest] = []
        candidates_by_name: dict[str, list[CandidateMatch]] = {}
        for name in names:
            candidates = await self._entity_candidates(
                names_and_vectors=[
                    (alias, vector_by_name[alias]) for alias in aliases_by_name[name]
                ],
                model_name=model_name,
            )
            candidates_by_name[name] = candidates
            requests.append(
                EntityResolutionRequest(
                    key=name,
                    name=name,
                    aliases=aliases_by_name[name],
                    entity_type=(
                        all_nodes[name][0].get("entity_type")
                        if all_nodes[name]
                        else None
                    ),
                    atom_texts=tuple(
                        item.get("description", "") for item in all_nodes[name]
                    ),
                    candidates=tuple(candidates),
                )
            )

        resolutions = await provider.resolve_entities(
            requests=requests, recent_episodes=recent_episodes
        )
        for request in requests:
            name = request.name
            candidates = candidates_by_name[name]
            resolution = resolutions.get(
                request.key,
                EntityResolution(None, None, 0.0, "missing batch decision"),
            )
            entity = (
                await self.sqlite.get_entity(resolution.canonical_entity_id)
                if resolution.canonical_entity_id
                else None
            )
            if entity is None:
                canonical_name = resolution.canonical_name or name
                candidate_names = {
                    normalize_name(candidate.text.splitlines()[0])
                    for candidate in candidates
                }
                if normalize_name(canonical_name) in candidate_names:
                    entity_type = request.entity_type or "entity"
                    canonical_name = f"{name} ({entity_type}:{uuid4().hex[:8]})"
                entity = EntityRecord(
                    id=f"entity-{uuid4().hex}",
                    workspace_id=self.sqlite.workspace_id,
                    canonical_name=canonical_name,
                    aliases=request.aliases,
                    entity_type=request.entity_type,
                )
            else:
                existing_aliases = {normalize_name(alias) for alias in entity.aliases}
                new_aliases = tuple(
                    alias
                    for alias in request.aliases
                    if normalize_name(alias) not in existing_aliases
                )
                if new_aliases:
                    entity = replace(entity, aliases=(*entity.aliases, *new_aliases))
            entity = await self.sqlite.put_entity(entity)
            for alias in request.aliases:
                await self.sqlite.put_embedding(
                    object_kind="entity_name",
                    object_id=stable_entity_name_embedding_id(entity.id, alias),
                    owner_id=entity.id,
                    source_text=alias,
                    vector=vector_by_name[alias],
                    model_name=model_name,
                )
            resolved[name] = entity
        return resolved, vector_by_name

    @staticmethod
    def _atom_from_payload(
        *,
        payload: dict[str, Any],
        content: str,
        episode: Episode,
        owner_id: str,
        subject_entity_id: str | None = None,
        object_entity_id: str | None = None,
        relation_keywords: tuple[str, ...] = (),
        atom_id: str | None = None,
    ) -> AtomRecord:
        valid_at = (
            _parse_optional_time(payload.get("valid_at"))
            if "valid_at" in payload
            else episode.effective_reference_at
        )
        return AtomRecord(
            id=atom_id or f"atom-{uuid4().hex}",
            workspace_id=episode.metadata.get("workspace_id", "") or "",
            owner_id=owner_id,
            content=content,
            valid_at=valid_at,
            invalid_at=_parse_optional_time(payload.get("invalid_at")),
            subject_entity_id=subject_entity_id,
            predicate=str(payload.get("predicate") or "").strip() or None,
            object_entity_id=object_entity_id,
            relation_keywords=relation_keywords,
            temporal_text=str(payload.get("temporal_text") or "").strip() or None,
            temporal_precision=str(payload.get("temporal_precision") or "").strip()
            or None,
            confidence=_score(payload.get("confidence")),
            importance=_score(payload.get("importance")),
        )

    async def _persist_atom(
        self,
        *,
        engine: Any,
        atom: AtomRecord,
        vector: np.ndarray,
        evidence: AtomEvidence,
    ) -> AtomRecord:
        atom = replace(atom, workspace_id=self.sqlite.workspace_id)
        model_name = self._embedding_model(engine)
        stored, _ = await self.sqlite.put_atom(atom, evidence)
        await self.sqlite.put_embedding(
            object_kind="atom",
            object_id=stored.id,
            owner_id=stored.owner_id,
            source_text=stored.content,
            vector=vector,
            model_name=model_name,
        )
        return stored

    async def _atom_resolution_request(
        self,
        *,
        atom: AtomRecord,
        vector: np.ndarray,
        model_name: str,
    ) -> tuple[AtomResolutionRequest, AtomClassification | None]:
        """Build one owner-isolated request and its optional local fast path."""

        existing_atoms = await self.sqlite.list_owner_atoms(atom.owner_id)
        for existing in existing_atoms:
            # Repeated current assertions often receive a later inferred
            # valid_at from the new Episode. Exact owner-scoped text is still
            # the same fact unless either assertion carries a different
            # explicit end time.
            if (
                existing.temporal_status().value == "active"
                and existing.normalized_content == atom.normalized_content
                and existing.invalid_at == atom.invalid_at
            ):
                return (
                    AtomResolutionRequest(atom=atom, candidates=()),
                    AtomClassification(
                        decision=AtomDecision.DUPLICATE,
                        matched_atom_id=existing.id,
                        confidence=1.0,
                        reason="owner-scoped normalized text match",
                    ),
                )

        if len(existing_atoms) <= self.atom_full_context_limit:
            candidates = tuple(
                CandidateMatch(
                    object_id=existing.id,
                    score=1.0,
                    text=existing.presentation(),
                    match_kind="owner_full_context",
                )
                for existing in existing_atoms
            )
        else:
            retrieved = await self.sqlite.search_embeddings(
                object_kind="atom",
                query_vector=vector,
                model_name=model_name,
                owner_id=atom.owner_id,
                limit=self.atom_candidate_limit,
                threshold=self.atom_similarity_threshold,
            )
            by_id = {item.id: item for item in existing_atoms}
            selected: dict[str, CandidateMatch] = {
                candidate.object_id: CandidateMatch(
                    object_id=candidate.object_id,
                    score=candidate.score,
                    text=(
                        by_id[candidate.object_id].presentation()
                        if candidate.object_id in by_id
                        else candidate.text
                    ),
                    match_kind=candidate.match_kind,
                )
                for candidate in retrieved
            }
            # Embedding similarity is not sufficient for state changes. Keep
            # a small recent active tail in the same bounded context.
            active_tail = [
                item
                for item in existing_atoms
                if item.temporal_status().value == "active"
            ][-4:]
            for existing in active_tail:
                selected.setdefault(
                    existing.id,
                    CandidateMatch(
                        object_id=existing.id,
                        score=0.0,
                        text=existing.presentation(),
                        match_kind="recent_active",
                    ),
                )
            candidates = tuple(selected.values())

        return (
            AtomResolutionRequest(
                atom=atom,
                candidates=candidates,
                owner_summary=await self.sqlite.get_owner_summary(atom.owner_id),
            ),
            None,
        )

    async def _apply_atom_resolution(
        self,
        *,
        engine: Any,
        atom: AtomRecord,
        vector: np.ndarray,
        evidence: AtomEvidence,
        resolution: AtomClassification,
    ) -> AtomRecord:
        """Apply a validated semantic decision without rewriting Atom text.

        Evolution edges point from the newly inserted Atom (source) to the
        matched historical Atom (target): new --REFINES/SUCCEEDS/CONTRADICTS--> old.
        """

        matched = (
            await self.sqlite.get_atom(resolution.matched_atom_id)
            if resolution.matched_atom_id
            else None
        )
        if matched is not None and matched.owner_id != atom.owner_id:
            matched = None
        decision = resolution.decision
        if decision is not AtomDecision.INDEPENDENT and matched is None:
            decision = AtomDecision.INDEPENDENT

        if decision is AtomDecision.DUPLICATE and matched is not None:
            return await self.sqlite.add_atom_evidence(
                matched.id,
                replace(evidence, atom_id=matched.id),
            )

        stored = await self._persist_atom(
            engine=engine,
            atom=atom,
            vector=vector,
            evidence=evidence,
        )
        if matched is None or decision is AtomDecision.INDEPENDENT:
            return stored

        await self.sqlite.add_atom_evolution(
            stored.id,
            matched.id,
            decision.name,
            metadata={
                "confidence": resolution.confidence,
                "reason": resolution.reason,
            },
        )
        system_time = utc_now()
        if decision is AtomDecision.REFINEMENT:
            # The old claim may remain historically true, but the more precise
            # Atom replaces it in MAGI's current materialized projection.
            await self.sqlite.expire_atom(matched.id, expired_at=system_time)
        elif decision is AtomDecision.TEMPORAL_SUCCESSOR:
            transition = resolution.target_invalid_at or stored.valid_at
            if transition is not None and (
                matched.valid_at is None or transition >= matched.valid_at
            ):
                await self.sqlite.set_atom_invalid_at(
                    matched.id,
                    invalid_at=transition,
                )
            await self.sqlite.expire_atom(matched.id, expired_at=system_time)
        elif decision is AtomDecision.CONTRADICTION and resolution.supersedes_target:
            transition = resolution.target_invalid_at
            if transition is not None and (
                matched.valid_at is None or transition >= matched.valid_at
            ):
                await self.sqlite.set_atom_invalid_at(
                    matched.id,
                    invalid_at=transition,
                )
            await self.sqlite.expire_atom(matched.id, expired_at=system_time)
        return stored

    async def commit(
        self,
        chunk_results: list,
        context: KnowledgeCommitContext,
    ) -> None:
        # Entity resolution assumes the persisted registry contains every
        # earlier decision. Serialize the phase-one write boundary so two
        # documents cannot both observe an empty candidate set and create
        # duplicate identities. Finer owner-scoped locking can replace this
        # once throughput measurements justify the added complexity.
        async with self._commit_lock:
            await self._commit_serialized(chunk_results, context)

    async def _commit_serialized(
        self,
        chunk_results: list,
        context: KnowledgeCommitContext,
    ) -> None:
        if not context.doc_id:
            raise ValueError("strict MAGI ingestion requires an Episode document id")
        episode = await self.sqlite.get_episode_model(context.doc_id)
        if episode is None:
            raise KeyError(f"Episode {context.doc_id!r} was not staged in SQLite")

        mode = await self.sqlite.get_workspace_mode()
        if mode not in (None, "magi_strict"):
            raise RuntimeError(
                f"workspace uses incompatible knowledge commit mode {mode!r}"
            )
        if mode is None:
            existing_labels = (
                await context.rag.chunk_entity_relation_graph.get_all_labels()
            )
            if existing_labels:
                raise RuntimeError(
                    "strict MAGI ingestion cannot be enabled over an existing "
                    "unregistered graph; use a new workspace or run a migration"
                )
            await self.sqlite.set_workspace_mode("magi_strict")

        all_nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        all_edges: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for maybe_nodes, maybe_edges in chunk_results:
            for name, records in maybe_nodes.items():
                all_nodes[name].extend(records)
            for pair, records in maybe_edges.items():
                all_edges[tuple(pair)].extend(records)

        all_nodes, all_edges = self._coalesce_episode_entities(
            dict(all_nodes), dict(all_edges)
        )

        missing_endpoints = {
            endpoint
            for pair in all_edges
            for endpoint in pair
            if endpoint not in all_nodes
        }
        if missing_endpoints:
            raise ValueError(
                "MAGI relationships require extracted entity Atom containers "
                f"for every endpoint; missing {sorted(missing_endpoints)!r}"
            )

        entities, _ = await self._resolve_entities(
            engine=context.rag, all_nodes=all_nodes
        )
        atom_inputs: list[
            tuple[str, Any, dict[str, Any], AtomRecord, AtomEvidence]
        ] = []

        for extracted_name, records in all_nodes.items():
            entity = entities[extracted_name]
            for record_index, record in enumerate(records):
                payload = record.get("atom_payload")
                if not isinstance(payload, dict):
                    raise ValueError(
                        "MAGI extraction returned a non-Atom entity record"
                    )
                atom = self._atom_from_payload(
                    payload=payload,
                    content=record["description"],
                    episode=episode,
                    owner_id=entity.id,
                    atom_id=stable_atom_id(
                        self.sqlite.workspace_id,
                        episode.id,
                        entity.id,
                        f"entity:{record.get('source_id', '')}:{record_index}",
                    ),
                )
                evidence = AtomEvidence(
                    atom_id=atom.id,
                    episode_id=episode.id,
                    quote=record["description"],
                    extraction_revision=record.get("source_id"),
                )
                atom_inputs.append(("entity", entity, record, atom, evidence))

        relation_by_pair: dict[tuple[str, str], RelationRecord] = {}
        for extracted_pair, records in all_edges.items():
            source_name, target_name = extracted_pair
            source = entities[source_name]
            target = entities[target_name]
            relation_id = stable_relation_id(
                self.sqlite.workspace_id, source.id, target.id
            )
            keywords = tuple(
                dict.fromkeys(
                    keyword.strip()
                    for record in records
                    for keyword in str(record.get("keywords") or "").split(",")
                    if keyword.strip()
                )
            )
            relation = await self.sqlite.put_relation(
                RelationRecord(
                    id=relation_id,
                    workspace_id=self.sqlite.workspace_id,
                    entity_a_id=source.id,
                    entity_b_id=target.id,
                    entity_a_name=source.canonical_name,
                    entity_b_name=target.canonical_name,
                    keywords=keywords,
                )
            )
            relation_by_pair[extracted_pair] = relation
            for record_index, record in enumerate(records):
                payload = record.get("atom_payload")
                if not isinstance(payload, dict):
                    raise ValueError(
                        "MAGI extraction returned a non-Atom relation record"
                    )
                atom = self._atom_from_payload(
                    payload=payload,
                    content=record["description"],
                    episode=episode,
                    owner_id=relation.id,
                    subject_entity_id=source.id,
                    object_entity_id=target.id,
                    relation_keywords=keywords,
                    atom_id=stable_atom_id(
                        self.sqlite.workspace_id,
                        episode.id,
                        relation.id,
                        f"relation:{record.get('source_id', '')}:{record_index}",
                    ),
                )
                evidence = AtomEvidence(
                    atom_id=atom.id,
                    episode_id=episode.id,
                    quote=record["description"],
                    extraction_revision=record.get("source_id"),
                )
                atom_inputs.append(
                    ("relation", (source, target, relation), record, atom, evidence)
                )

        vectors = await self._embed(
            context.rag, [item[3].content for item in atom_inputs]
        )
        model_name = self._embedding_model(context.rag)
        resolution_requests: list[AtomResolutionRequest] = []
        atom_resolutions: dict[str, AtomClassification] = {}
        for item, vector in zip(atom_inputs, vectors):
            atom = item[3]
            request, local_resolution = await self._atom_resolution_request(
                atom=atom,
                vector=vector,
                model_name=model_name,
            )
            if local_resolution is not None:
                atom_resolutions[atom.id] = local_resolution
            else:
                resolution_requests.append(request)

        provider = self.decision_provider or LLMMemoryDecisionProvider(context.rag)
        if resolution_requests:
            atom_resolutions.update(
                await provider.resolve_atoms(
                    requests=resolution_requests,
                    recent_episodes=await self.sqlite.recent_episode_context(limit=4),
                )
            )
        projected_nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        projected_edges: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        touched_nodes: dict[str, str] = {}
        touched_edges: dict[tuple[str, str], str] = {}

        for item, vector in zip(atom_inputs, vectors):
            kind, owner, record, atom, evidence = item
            stored = await self._apply_atom_resolution(
                engine=context.rag,
                atom=atom,
                vector=vector,
                evidence=evidence,
                resolution=atom_resolutions.get(
                    atom.id,
                    AtomClassification(
                        decision=AtomDecision.INDEPENDENT,
                        reason="missing batch decision",
                    ),
                ),
            )
            projected = {
                **record,
                "description": stored.presentation(),
                "atom_id": stored.id,
            }
            if kind == "entity":
                entity = owner
                projected["entity_name"] = entity.canonical_name
                projected_nodes[entity.canonical_name].append(projected)
                touched_nodes[entity.canonical_name] = entity.id
            else:
                source, target, relation = owner
                pair = tuple(sorted((source.canonical_name, target.canonical_name)))
                projected["src_id"] = source.canonical_name
                projected["tgt_id"] = target.canonical_name
                projected_edges[pair].append(projected)
                touched_edges[pair] = relation.id

        # Materialize every Atom for touched owners, not only this Episode's
        # delta. The LightRAG merge then summarizes Atom presentations rather
        # than recursively merging an older summary with new facts.
        for canonical_name, entity_id in touched_nodes.items():
            template = projected_nodes[canonical_name][0]
            atoms = await self.sqlite.list_owner_atoms(entity_id, active_only=True)
            projected_nodes[canonical_name] = [
                {
                    **template,
                    "description": atom.presentation(),
                    "atom_id": atom.id,
                    "magi_atom_order": index,
                }
                for index, atom in enumerate(atoms)
            ]
        for pair, relation_id in touched_edges.items():
            template = projected_edges[pair][0]
            atoms = await self.sqlite.list_owner_atoms(relation_id, active_only=True)
            projected_edges[pair] = [
                {
                    **template,
                    "description": atom.presentation(),
                    "atom_id": atom.id,
                    "magi_atom_order": index,
                }
                for index, atom in enumerate(atoms)
            ]

        merge_config = context.rag._build_global_config()
        merge_config["magi_projection_replace"] = True
        merge_config["magi_preserve_atom_order"] = True
        await merge_nodes_and_edges(
            chunk_results=[(dict(projected_nodes), dict(projected_edges))],
            knowledge_graph_inst=context.rag.chunk_entity_relation_graph,
            entity_vdb=context.rag.entities_vdb,
            relationships_vdb=context.rag.relationships_vdb,
            global_config=merge_config,
            full_entities_storage=context.full_entities_storage,
            full_relations_storage=context.full_relations_storage,
            doc_id=context.doc_id,
            pipeline_status=context.pipeline_status,
            pipeline_status_lock=context.pipeline_status_lock,
            llm_response_cache=context.rag.llm_response_cache,
            entity_chunks_storage=context.rag.entity_chunks,
            relation_chunks_storage=context.rag.relation_chunks,
            current_file_number=context.current_file_number,
            total_files=context.total_files,
            file_path=context.file_path,
        )

        graph = context.rag.chunk_entity_relation_graph
        for canonical_name, entity_id in touched_nodes.items():
            node = await graph.get_node(canonical_name) or {}
            atoms = await self.sqlite.list_owner_atoms(entity_id)
            # Neo4JStorage uses ``entity_id`` as the graph lookup key and it
            # must remain the canonical graph name. MAGI's stable registry id
            # is a separate cross-layer identifier.
            node["entity_id"] = canonical_name
            node["magi_entity_id"] = entity_id
            node["atom_ids"] = [atom.id for atom in atoms]
            await graph.upsert_node(canonical_name, node_data=node)
        for pair, relation_id in touched_edges.items():
            edge = await graph.get_edge(*pair) or {}
            atoms = await self.sqlite.list_owner_atoms(relation_id)
            edge["magi_relation_id"] = relation_id
            edge["atom_ids"] = [atom.id for atom in atoms]
            await graph.upsert_edge(pair[0], pair[1], edge_data=edge)

        self._episode_contexts.pop(episode.id, None)
