"""Strict Episode -> Atom -> graph materialization adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, replace
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
    EpisodeKind,
    EpisodeStatus,
    RelationRecord,
    normalize_name,
    stable_relation_id,
    stable_atom_id,
    stable_entity_name_embedding_id,
    utc_now,
)
from magi_core.operate import (
    _handle_entity_relation_summary,
    _truncate_vdb_content,
    merge_nodes_and_edges,
)
from magi_core.utils import compute_mdhash_id, logger

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


@dataclass(frozen=True, slots=True)
class _OwnerDescriptionState:
    """One internally consistent owner-summary projection snapshot."""

    description: str
    atoms: list[AtomRecord]
    checkpoint_revision: int | None


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
        self._projection_lock = asyncio.Lock()
        self._projection_worker_task: asyncio.Task[None] | None = None
        self._projection_wakeup: asyncio.Event | None = None
        self._projection_stop: asyncio.Event | None = None

    async def start_projection_worker(self, rag: Any) -> None:
        """Reconcile durable owner tasks, then keep retrying in the background."""

        if self._projection_worker_task is not None:
            return
        self._projection_wakeup = asyncio.Event()
        self._projection_stop = asyncio.Event()
        await self.reconcile_projections(rag, ready_only=False)
        self._projection_worker_task = asyncio.create_task(
            self._projection_worker_loop(rag),
            name=f"magi-projection-{self.sqlite.workspace_id}",
        )

    async def stop_projection_worker(self) -> None:
        task = self._projection_worker_task
        if task is None:
            return
        if self._projection_stop is not None:
            self._projection_stop.set()
        if self._projection_wakeup is not None:
            self._projection_wakeup.set()
        await task
        self._projection_worker_task = None
        self._projection_wakeup = None
        self._projection_stop = None

    def notify_projection_worker(self) -> None:
        if self._projection_wakeup is not None:
            self._projection_wakeup.set()

    async def _projection_worker_loop(self, rag: Any) -> None:
        if self._projection_wakeup is None or self._projection_stop is None:
            return
        while not self._projection_stop.is_set():
            try:
                await asyncio.wait_for(self._projection_wakeup.wait(), timeout=5.0)
            except TimeoutError:
                pass
            self._projection_wakeup.clear()
            if self._projection_stop.is_set():
                break
            try:
                await self.reconcile_projections(rag, ready_only=True)
            except Exception:
                logger.exception("Unexpected MAGI projection worker failure")

    async def reconcile_projections(
        self,
        rag: Any,
        *,
        ready_only: bool = True,
        limit: int = 100,
    ) -> dict[str, int]:
        """Project the latest SQLite state for queued owners idempotently."""

        async with self._projection_lock:
            return await self._reconcile_projections_locked(
                rag,
                ready_only=ready_only,
                limit=limit,
            )

    async def _reconcile_projections_locked(
        self,
        rag: Any,
        *,
        ready_only: bool,
        limit: int,
    ) -> dict[str, int]:
        tasks = await self.sqlite.list_projection_tasks(
            ready_only=ready_only,
            limit=limit,
        )
        return await self._apply_projection_tasks_locked(
            rag,
            tasks,
            raise_on_error=False,
        )

    async def _apply_projection_tasks_locked(
        self,
        rag: Any,
        tasks: Sequence[dict[str, Any]],
        *,
        raise_on_error: bool,
    ) -> dict[str, int]:
        result = {"processed": 0, "applied": 0, "failed": 0}
        successful_tasks: list[dict[str, Any]] = []
        for task in tasks:
            result["processed"] += 1
            try:
                await self._project_owner(rag, task)
                successful_tasks.append(task)
            except Exception as exc:
                result["failed"] += 1
                await self.sqlite.mark_projection_failed(
                    str(task["owner_id"]),
                    int(task["target_revision"]),
                    str(exc),
                )
                logger.exception(
                    "Failed to reconcile projection owner %s",
                    task["owner_id"],
                )
                if raise_on_error:
                    self.notify_projection_worker()
                    raise
        if successful_tasks:
            try:
                await self._flush_projection_storages(rag)
            except Exception as exc:
                for task in successful_tasks:
                    await self.sqlite.mark_projection_failed(
                        str(task["owner_id"]),
                        int(task["target_revision"]),
                        str(exc),
                    )
                result["failed"] += len(successful_tasks)
                successful_tasks.clear()
                self.notify_projection_worker()
                if raise_on_error:
                    raise
                logger.exception("Failed to flush reconciled projection storages")
            else:
                for task in successful_tasks:
                    applied = await self.sqlite.mark_projection_applied(
                        str(task["owner_id"]),
                        int(task["target_revision"]),
                    )
                    if applied:
                        result["applied"] += 1
        return result

    async def _owner_description_state(
        self,
        rag: Any,
        *,
        owner_id: str,
        description_type: str,
        description_name: str,
    ) -> _OwnerDescriptionState:
        """Materialize one owner from a durable checkpoint plus pending delta."""

        atoms: list[AtomRecord] = []
        current_atoms: list[AtomRecord] = []
        active_ids: list[str] = []
        atom_by_id: dict[str, AtomRecord] = {}
        global_config = rag._build_global_config()
        prompt_version = "magi-owner-summary-checkpoint-v1"
        model_identity = json.dumps(
            (global_config.get("llm_cache_identities") or {}).get("extract")
            or {"model": global_config.get("llm_model_name")},
            ensure_ascii=False,
            sort_keys=True,
        )
        delta_atom_threshold = max(
            1,
            int(
                global_config.get(
                    "magi_summary_delta_atom_threshold",
                    global_config.get("force_llm_summary_on_merge", 8),
                )
            ),
        )
        delta_token_threshold = max(
            1,
            int(
                global_config.get(
                    "magi_summary_delta_token_threshold",
                    global_config.get("summary_max_tokens", 1200),
                )
            ),
        )
        description_budget = max(
            1,
            int(
                global_config.get(
                    "magi_summary_description_token_budget",
                    global_config.get("summary_context_size", 12000),
                )
            ),
        )
        rebuild_interval = max(
            1,
            int(global_config.get("magi_summary_full_rebuild_interval", 8)),
        )

        def state(
            description: str,
            checkpoint_row: dict[str, Any] | None,
        ) -> _OwnerDescriptionState:
            return _OwnerDescriptionState(
                description=description,
                atoms=atoms,
                checkpoint_revision=(
                    int(checkpoint_row["summary_revision"])
                    if checkpoint_row is not None
                    else None
                ),
            )

        def token_count(parts: Sequence[str]) -> int:
            tokenizer = global_config.get("tokenizer")
            if tokenizer is None:
                return sum(len(part.split()) for part in parts)
            return sum(len(tokenizer.encode(part)) for part in parts)

        async def summarize(
            parts: list[str],
            *,
            cache_identity: dict[str, Any],
        ) -> tuple[str, bool, bool]:
            required = (
                "tokenizer",
                "summary_context_size",
                "summary_max_tokens",
                "force_llm_summary_on_merge",
            )
            if not all(key in global_config for key in required):
                return (
                    GRAPH_FIELD_SEP.join(part for part in parts if part),
                    False,
                    True,
                )
            summary_stats: dict[str, Any] = {
                "lineage_valid": True,
                "llm_calls": 0,
            }
            description, llm_was_used = await _handle_entity_relation_summary(
                description_type,
                description_name,
                parts,
                GRAPH_FIELD_SEP,
                global_config,
                getattr(rag, "llm_response_cache", None),
                summary_cache_identity=cache_identity,
                summary_stats=summary_stats,
            )
            return (
                description,
                llm_was_used,
                bool(summary_stats["lineage_valid"]),
            )

        def checkpoint_hash(checkpoint: dict[str, Any] | None) -> str | None:
            if checkpoint is None:
                return None
            summary = str(checkpoint.get("checkpoint_summary") or "")
            return hashlib.sha256(summary.encode("utf-8")).hexdigest()

        def updated_metrics(
            checkpoint: dict[str, Any] | None,
            *,
            mode: str,
            input_tokens: int,
            saved_tokens: int,
            llm_was_used: bool,
        ) -> dict[str, Any]:
            metrics = dict((checkpoint or {}).get("metrics") or {})
            metrics[f"{mode}_compaction_count"] = int(
                metrics.get(f"{mode}_compaction_count", 0)
            ) + 1
            if llm_was_used:
                metrics[f"{mode}_llm_call_count"] = int(
                    metrics.get(f"{mode}_llm_call_count", 0)
                ) + 1
            metrics["last_input_tokens"] = input_tokens
            metrics["total_input_tokens"] = int(
                metrics.get("total_input_tokens", 0)
            ) + input_tokens
            metrics["last_saved_tokens"] = saved_tokens
            metrics["total_saved_tokens"] = int(
                metrics.get("total_saved_tokens", 0)
            ) + saved_tokens
            metrics["last_lineage_valid"] = True
            metrics["rebuild_required"] = False
            return metrics

        async def record_lineage_failure(
            *,
            checkpoint: dict[str, Any] | None,
            expected_revision: int | None,
            mode: str,
            visible_description: str,
        ) -> _OwnerDescriptionState | None:
            metrics = dict((checkpoint or {}).get("metrics") or {})
            metrics["last_lineage_valid"] = False
            metrics["rebuild_required"] = mode == "rebuild"
            reason = f"lineage_validation_failed:{mode}"
            if checkpoint is None:
                checkpoint_summary = ""
                covered_ids: list[str] = []
                pending_ids = active_ids
                compaction_count = 0
                last_compacted_at = None
            else:
                checkpoint_summary = str(checkpoint["checkpoint_summary"])
                covered_ids = list(checkpoint["covered_atom_ids"])
                pending_ids = list(checkpoint["pending_atom_ids"])
                compaction_count = int(
                    checkpoint["incremental_compaction_count"]
                )
                last_compacted_at = checkpoint["last_compacted_at"]
            updated = await self.sqlite.compare_and_set_owner_summary_checkpoint(
                owner_id,
                expected_revision=expected_revision,
                checkpoint_summary=checkpoint_summary,
                covered_atom_ids=covered_ids,
                pending_atom_ids=pending_ids,
                prompt_version=prompt_version,
                model_identity=model_identity,
                incremental_compaction_count=compaction_count,
                last_compacted_at=last_compacted_at,
                reason=reason,
                metrics=metrics,
            )
            if not updated:
                return None
            return state(
                visible_description,
                {
                    "summary_revision": (
                        1 if expected_revision is None else expected_revision + 1
                    ),
                    "covered_atom_ids": covered_ids,
                    "pending_atom_ids": pending_ids,
                    "incremental_compaction_count": compaction_count,
                    "last_reason": reason,
                    "last_compacted_at": last_compacted_at,
                    "metrics": metrics,
                },
            )

        def raw_active_description() -> str:
            return GRAPH_FIELD_SEP.join(
                atom.presentation() for atom in current_atoms
            )

        async def active_snapshot_is_current() -> bool:
            latest_atoms = await self.sqlite.list_owner_atoms(owner_id)
            return [
                atom.id for atom in latest_atoms if atom.expired_at is None
            ] == active_ids

        for _ in range(4):
            atoms = await self.sqlite.list_owner_atoms(owner_id)
            current_atoms = [atom for atom in atoms if atom.expired_at is None]
            active_ids = [atom.id for atom in current_atoms]
            atom_by_id = {atom.id: atom for atom in current_atoms}
            checkpoint = await self.sqlite.get_owner_summary_checkpoint(owner_id)
            expected_revision = (
                int(checkpoint["summary_revision"])
                if checkpoint is not None
                else None
            )
            rebuild_reason: str | None = None
            if checkpoint is None:
                rebuild_reason = "missing_checkpoint"
            elif (checkpoint.get("metrics") or {}).get("rebuild_required"):
                rebuild_reason = "lineage_validation_failed"
            elif (
                checkpoint["prompt_version"] != prompt_version
                or checkpoint["model_identity"] != model_identity
            ):
                rebuild_reason = "summary_identity_changed"
            else:
                covered = set(checkpoint["covered_atom_ids"])
                if covered.difference(active_ids):
                    rebuild_reason = "covered_atom_removed"

            if rebuild_reason is not None:
                presentations = [atom.presentation() for atom in current_atoms]
                try:
                    rebuilt, llm_was_used, lineage_valid = await summarize(
                        presentations,
                        cache_identity={
                            "summary_schema": prompt_version,
                            "mode": "rebuild",
                            "reason": rebuild_reason,
                            "previous_revision": expected_revision,
                            "previous_checkpoint_hash": checkpoint_hash(checkpoint),
                            "active_atom_ids": active_ids,
                        },
                    )
                except Exception:
                    logger.exception(
                        "LLMmrg(rebuild) failed: %s | active=%d, reason=%s",
                        owner_id,
                        len(active_ids),
                        rebuild_reason,
                    )
                    if not await active_snapshot_is_current():
                        continue
                    return state(raw_active_description(), checkpoint)
                if not await active_snapshot_is_current():
                    continue
                if not lineage_valid:
                    failed_state = await record_lineage_failure(
                        checkpoint=checkpoint,
                        expected_revision=expected_revision,
                        mode="rebuild",
                        visible_description=raw_active_description(),
                    )
                    if failed_state is not None:
                        return failed_state
                    continue
                compacted_at = utc_now().isoformat()
                metrics = updated_metrics(
                    checkpoint,
                    mode="rebuild",
                    input_tokens=token_count(presentations),
                    saved_tokens=0,
                    llm_was_used=llm_was_used,
                )
                updated = await self.sqlite.compare_and_set_owner_summary_checkpoint(
                    owner_id,
                    expected_revision=expected_revision,
                    checkpoint_summary=rebuilt,
                    covered_atom_ids=active_ids,
                    pending_atom_ids=(),
                    prompt_version=prompt_version,
                    model_identity=model_identity,
                    incremental_compaction_count=0,
                    last_compacted_at=compacted_at,
                    reason=f"rebuild:{rebuild_reason}",
                    metrics=metrics,
                )
                if updated:
                    action = "LLMmrg" if llm_was_used else "Summary checkpointed"
                    logger.info(
                        "%s(rebuild): %s | active=%d, reason=%s, input_tokens=%d",
                        action,
                        owner_id,
                        len(active_ids),
                        rebuild_reason,
                        metrics["last_input_tokens"],
                    )
                    return state(
                        rebuilt,
                        {
                            "summary_revision": (
                                1
                                if expected_revision is None
                                else expected_revision + 1
                            ),
                            "covered_atom_ids": active_ids,
                            "pending_atom_ids": [],
                            "incremental_compaction_count": 0,
                            "last_reason": f"rebuild:{rebuild_reason}",
                            "last_compacted_at": compacted_at,
                            "metrics": metrics,
                        },
                    )
                continue

            covered_ids = list(checkpoint["covered_atom_ids"])
            covered = set(covered_ids)
            prior_pending = set(checkpoint["pending_atom_ids"])
            pending_set = prior_pending.intersection(active_ids)
            pending_set.update(
                atom_id
                for atom_id in active_ids
                if atom_id not in covered and atom_id not in prior_pending
            )
            pending_ids = [atom_id for atom_id in active_ids if atom_id in pending_set]
            if pending_ids != checkpoint["pending_atom_ids"]:
                if not await active_snapshot_is_current():
                    continue
                updated = await self.sqlite.compare_and_set_owner_summary_checkpoint(
                    owner_id,
                    expected_revision=expected_revision,
                    checkpoint_summary=str(checkpoint["checkpoint_summary"]),
                    covered_atom_ids=covered_ids,
                    pending_atom_ids=pending_ids,
                    prompt_version=prompt_version,
                    model_identity=model_identity,
                    incremental_compaction_count=int(
                        checkpoint["incremental_compaction_count"]
                    ),
                    last_compacted_at=checkpoint["last_compacted_at"],
                    reason="pending_delta_updated",
                    metrics=checkpoint.get("metrics"),
                )
                if not updated:
                    continue
                checkpoint = {
                    **checkpoint,
                    "pending_atom_ids": pending_ids,
                    "summary_revision": expected_revision + 1,
                    "last_reason": "pending_delta_updated",
                }
                expected_revision += 1

            checkpoint_summary = str(checkpoint["checkpoint_summary"] or "")
            pending_presentations = [
                atom_by_id[atom_id].presentation() for atom_id in pending_ids
            ]
            materialized_parts = [
                part for part in (checkpoint_summary, *pending_presentations) if part
            ]
            materialized = GRAPH_FIELD_SEP.join(materialized_parts)
            if not pending_ids:
                if not await active_snapshot_is_current():
                    continue
                logger.info(
                    "Summary reused: %s | covered=%d, delta=0",
                    owner_id,
                    len(covered_ids),
                )
                return state(checkpoint_summary, checkpoint)

            pending_tokens = token_count(pending_presentations)
            should_compact = (
                len(pending_ids) >= delta_atom_threshold
                or pending_tokens >= delta_token_threshold
                or token_count(materialized_parts) >= description_budget
            )
            if not should_compact:
                if not await active_snapshot_is_current():
                    continue
                logger.info(
                    "Summary pending: %s | covered=%d, delta=%d",
                    owner_id,
                    len(covered_ids),
                    len(pending_ids),
                )
                return state(materialized, checkpoint)

            compaction_count = int(checkpoint["incremental_compaction_count"])
            do_full_rebuild = compaction_count + 1 >= rebuild_interval
            reason = (
                "periodic_full_rebuild" if do_full_rebuild else "delta_threshold"
            )
            summary_inputs = (
                [atom.presentation() for atom in current_atoms]
                if do_full_rebuild
                else materialized_parts
            )
            try:
                compacted, llm_was_used, lineage_valid = await summarize(
                    summary_inputs,
                    cache_identity={
                        "summary_schema": prompt_version,
                        "mode": "rebuild" if do_full_rebuild else "delta",
                        "reason": reason,
                        "previous_revision": expected_revision,
                        "previous_checkpoint_hash": hashlib.sha256(
                            checkpoint_summary.encode("utf-8")
                        ).hexdigest(),
                        "pending_atom_ids": pending_ids,
                        "active_atom_ids": active_ids if do_full_rebuild else [],
                    },
                )
            except Exception:
                logger.exception(
                    "LLMmrg(%s) failed: %s | delta=%d",
                    "rebuild" if do_full_rebuild else "delta",
                    owner_id,
                    len(pending_ids),
                )
                if not await active_snapshot_is_current():
                    continue
                return state(materialized, checkpoint)
            if not await active_snapshot_is_current():
                continue
            if not lineage_valid:
                failed_state = await record_lineage_failure(
                    checkpoint=checkpoint,
                    expected_revision=expected_revision,
                    mode="rebuild" if do_full_rebuild else "delta",
                    visible_description=(
                        raw_active_description() if do_full_rebuild else materialized
                    ),
                )
                if failed_state is not None:
                    return failed_state
                continue
            compacted_at = utc_now().isoformat()
            full_active_tokens = token_count(
                [atom.presentation() for atom in current_atoms]
            )
            input_tokens = token_count(summary_inputs)
            metrics = updated_metrics(
                checkpoint,
                mode="rebuild" if do_full_rebuild else "delta",
                input_tokens=input_tokens,
                saved_tokens=(
                    0 if do_full_rebuild else max(0, full_active_tokens - input_tokens)
                ),
                llm_was_used=llm_was_used,
            )
            updated = await self.sqlite.compare_and_set_owner_summary_checkpoint(
                owner_id,
                expected_revision=expected_revision,
                checkpoint_summary=compacted,
                covered_atom_ids=active_ids,
                pending_atom_ids=(),
                prompt_version=prompt_version,
                model_identity=model_identity,
                incremental_compaction_count=(
                    0 if do_full_rebuild else compaction_count + 1
                ),
                last_compacted_at=compacted_at,
                reason=reason,
                metrics=metrics,
            )
            if updated:
                action = "LLMmrg" if llm_was_used else "Summary checkpointed"
                logger.info(
                    "%s(%s): %s | checkpoint_revision=%d, delta=%d, "
                    "input_tokens=%d, saved_tokens=%d",
                    action,
                    "rebuild" if do_full_rebuild else "delta",
                    owner_id,
                    expected_revision,
                    len(pending_ids),
                    metrics["last_input_tokens"],
                    metrics["last_saved_tokens"],
                )
                return state(
                    compacted,
                    {
                        "summary_revision": expected_revision + 1,
                        "covered_atom_ids": active_ids,
                        "pending_atom_ids": [],
                        "incremental_compaction_count": (
                            0 if do_full_rebuild else compaction_count + 1
                        ),
                        "last_reason": reason,
                        "last_compacted_at": compacted_at,
                        "metrics": metrics,
                    },
                )

        atoms = await self.sqlite.list_owner_atoms(owner_id)
        current_atoms = [atom for atom in atoms if atom.expired_at is None]
        return state(raw_active_description(), None)

    async def _owner_description(
        self,
        rag: Any,
        *,
        owner_id: str,
        description_type: str,
        description_name: str,
    ) -> tuple[str, list[AtomRecord]]:
        """Compatibility wrapper for callers that only need text and Atoms."""

        result = await self._owner_description_state(
            rag,
            owner_id=owner_id,
            description_type=description_type,
            description_name=description_name,
        )
        return result.description, result.atoms

    async def _projection_description_state(
        self,
        rag: Any,
        *,
        owner_id: str,
        description_type: str,
        description_name: str,
    ) -> _OwnerDescriptionState:
        """Read a current checkpoint snapshot without exposing its revision to KG."""

        state = await self._owner_description_state(
            rag,
            owner_id=owner_id,
            description_type=description_type,
            description_name=description_name,
        )
        for _ in range(3):
            checkpoint = await self.sqlite.get_owner_summary_checkpoint(owner_id)
            current_revision = (
                int(checkpoint["summary_revision"])
                if checkpoint is not None
                else None
            )
            if current_revision == state.checkpoint_revision:
                return state
            state = await self._owner_description_state(
                rag,
                owner_id=owner_id,
                description_type=description_type,
                description_name=description_name,
            )
        return state

    async def _project_entity_record(
        self,
        rag: Any,
        entity: EntityRecord,
    ) -> None:
        graph = rag.chunk_entity_relation_graph
        summary_state = await self._projection_description_state(
            rag,
            owner_id=entity.id,
            description_type="entity",
            description_name=entity.canonical_name,
        )
        description = summary_state.description
        atoms = summary_state.atoms
        provenance = await self.sqlite.get_owner_provenance(entity.id)
        source_id = GRAPH_FIELD_SEP.join(provenance["source_ids"])
        file_path = GRAPH_FIELD_SEP.join(provenance["file_paths"]) or "unknown_source"
        existing = await graph.get_node(entity.canonical_name) or {}
        node = {
            **existing,
            "entity_id": entity.canonical_name,
            "magi_entity_id": entity.id,
            "entity_type": entity.entity_type or existing.get("entity_type", "UNKNOWN"),
            "description": description,
            "source_id": source_id or existing.get("source_id", ""),
            "file_path": file_path,
            "atom_ids": [atom.id for atom in atoms],
        }
        await graph.upsert_node(entity.canonical_name, node_data=node)
        if rag.entities_vdb is not None:
            global_config = rag._build_global_config()
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
                        "entity_type": node["entity_type"],
                        "source_id": node["source_id"],
                        "file_path": node["file_path"],
                    }
                }
            )
        await self.sqlite.set_owner_materialization(
            entity.id,
            atom_ids=[atom.id for atom in atoms],
            summary_cited=description,
        )

    async def _project_relation_record(
        self,
        rag: Any,
        relation: RelationRecord,
    ) -> None:
        graph = rag.chunk_entity_relation_graph
        for entity_id in (relation.entity_a_id, relation.entity_b_id):
            entity = await self.sqlite.get_entity(entity_id)
            if entity is not None and await graph.get_node(entity.canonical_name) is None:
                await self._project_entity_record(rag, entity)

        source, target = sorted(
            (relation.entity_a_name, relation.entity_b_name)
        )
        summary_state = await self._projection_description_state(
            rag,
            owner_id=relation.id,
            description_type="relation",
            description_name=f"{source} -> {target}",
        )
        description = summary_state.description
        atoms = summary_state.atoms
        provenance = await self.sqlite.get_owner_provenance(relation.id)
        source_id = GRAPH_FIELD_SEP.join(provenance["source_ids"])
        file_path = GRAPH_FIELD_SEP.join(provenance["file_paths"]) or "unknown_source"
        existing = await graph.get_edge(source, target) or {}
        keywords = GRAPH_FIELD_SEP.join(relation.keywords)
        edge = {
            **existing,
            "src_id": source,
            "tgt_id": target,
            "magi_relation_id": relation.id,
            "description": description,
            "keywords": keywords or existing.get("keywords", ""),
            "weight": existing.get("weight", 1.0),
            "source_id": source_id or existing.get("source_id", ""),
            "file_path": file_path,
            "atom_ids": [atom.id for atom in atoms],
        }
        await graph.upsert_edge(source, target, edge_data=edge)
        if rag.relationships_vdb is not None:
            relation_id = compute_mdhash_id(source + target, prefix="rel-")
            reverse_id = compute_mdhash_id(target + source, prefix="rel-")
            await rag.relationships_vdb.delete([relation_id, reverse_id])
            global_config = rag._build_global_config()
            content = _truncate_vdb_content(
                f"{edge['keywords']}\t{source}\n{target}\n{description}",
                global_config,
                f"relationship:{source}-{target}",
            )
            await rag.relationships_vdb.upsert(
                {
                    relation_id: {
                        "src_id": source,
                        "tgt_id": target,
                        "source_id": edge["source_id"],
                        "content": content,
                        "keywords": edge["keywords"],
                        "description": description,
                        "weight": edge["weight"],
                        "file_path": edge["file_path"],
                    }
                }
            )
        await self.sqlite.set_owner_materialization(
            relation.id,
            atom_ids=[atom.id for atom in atoms],
            summary_cited=description,
        )

    async def _project_owner(self, rag: Any, task: dict[str, Any]) -> None:
        owner_id = str(task["owner_id"])
        snapshot = task.get("owner_snapshot") or {}
        if str(task.get("owner_kind")) == "relation":
            relation = await self.sqlite.get_relation(owner_id)
            if relation is not None:
                await self._project_relation_record(rag, relation)
                return
            source = snapshot.get("source")
            target = snapshot.get("target")
            if source and target:
                source, target = sorted((str(source), str(target)))
                if rag.relationships_vdb is not None:
                    await rag.relationships_vdb.delete(
                        [
                            compute_mdhash_id(source + target, prefix="rel-"),
                            compute_mdhash_id(target + source, prefix="rel-"),
                        ]
                    )
                await rag.chunk_entity_relation_graph.remove_edges([(source, target)])
            return

        entity = await self.sqlite.get_entity(owner_id)
        if entity is not None:
            await self._project_entity_record(rag, entity)
            return
        canonical_name = snapshot.get("canonical_name")
        if canonical_name:
            canonical_name = str(canonical_name)
            if rag.entities_vdb is not None:
                await rag.entities_vdb.delete_entity(canonical_name)
            await rag.chunk_entity_relation_graph.delete_node(canonical_name)

    @staticmethod
    async def _flush_projection_storages(rag: Any) -> None:
        for storage in (
            rag.chunk_entity_relation_graph,
            rag.entities_vdb,
            rag.relationships_vdb,
        ):
            callback = getattr(storage, "index_done_callback", None)
            if callable(callback):
                await callback()

    def register_episodes(self, episodes: Sequence[Episode]) -> None:
        for episode in episodes:
            self._episode_contexts[episode.id] = {
                "episode_id": episode.id,
                "reference_at": episode.effective_reference_at.isoformat(),
                "kind": episode.kind.value,
                "source_uri": episode.source_uri,
                "metadata": dict(episode.metadata),
            }

    def unregister_episodes(self, episode_ids: Sequence[str]) -> None:
        """Discard transient contexts for inputs rejected before admission."""

        for episode_id in episode_ids:
            self._episode_contexts.pop(episode_id, None)

    async def stage_episode(self, episode: Episode) -> None:
        await self.sqlite.put_episode(episode)
        self.register_episodes([episode])

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
            context = self._episode_contexts.get(doc_id, {})
            episode = Episode(
                id=doc_id,
                content=content,
                kind=EpisodeKind(context.get("kind", EpisodeKind.DOCUMENT.value)),
                reference_at=(
                    _parse_optional_time(context.get("reference_at"))
                    or _parse_optional_time(reference_at)
                    or utc_now()
                ),
                source_uri=context.get("source_uri") or file_path,
                metadata=context.get("metadata") or {},
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
        """Delete evidence and durably converge graph/vector projections."""

        async with self._projection_lock:
            result = await self.sqlite.backup_and_delete_episode(doc_id)
            if result is None:
                return None
            tasks = await self.sqlite.list_projection_tasks(
                owner_ids=list(result["affected_owner_ids"]),
                ready_only=False,
                limit=max(1, len(result["affected_owner_ids"])),
            )
            await self._apply_projection_tasks_locked(
                rag,
                tasks,
                raise_on_error=True,
            )
        return str(result["backup_id"])

    async def clear_memory(self, *, rag: Any | None = None) -> list[str]:
        self._episode_contexts.clear()
        async with self._projection_lock:
            backup_ids = await self.sqlite.backup_and_clear()
            if rag is not None:
                tasks = await self.sqlite.list_projection_tasks(
                    ready_only=False,
                    limit=100_000,
                )
                await self._apply_projection_tasks_locked(
                    rag,
                    tasks,
                    raise_on_error=True,
                )
            return backup_ids

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
        all_edges: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> tuple[dict[str, EntityRecord], dict[str, np.ndarray]]:
        names = list(all_nodes)
        incident_relation_texts: dict[str, list[str]] = defaultdict(list)
        for (source, target), records in (all_edges or {}).items():
            texts = [
                str(record.get("description") or "").strip()
                for record in records
                if str(record.get("description") or "").strip()
            ]
            incident_relation_texts[source].extend(texts)
            incident_relation_texts[target].extend(texts)
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
            entity_atom_texts = [
                str(item.get("description") or "").strip()
                for item in all_nodes[name]
                if item.get("magi_endpoint_only") is not True
                and str(item.get("description") or "").strip()
            ]
            resolution_texts = entity_atom_texts or incident_relation_texts[name]
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
                    # A relation-only entity still needs semantic context for
                    # disambiguation.  Supplying its incident Relationship Atom
                    # text prevents an exact-name candidate from being selected
                    # solely because the endpoint container owns no Entity Atom.
                    atom_texts=tuple(dict.fromkeys(resolution_texts)),
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

    def _in_batch_candidates(
        self,
        *,
        prior: Sequence[tuple[AtomRecord, np.ndarray]],
        vector: np.ndarray,
    ) -> tuple[CandidateMatch, ...]:
        """Return a bounded, owner-local view of earlier Atoms in this Episode."""

        if len(prior) <= self.atom_full_context_limit:
            selected = [(atom, 1.0, "in_batch_owner_full_context") for atom, _ in prior]
        else:
            vector_norm = float(np.linalg.norm(vector))
            scored: list[tuple[AtomRecord, float, str]] = []
            for atom, candidate_vector in prior:
                denominator = vector_norm * float(np.linalg.norm(candidate_vector))
                score = (
                    float(np.dot(vector, candidate_vector) / denominator)
                    if denominator
                    else 0.0
                )
                if score >= self.atom_similarity_threshold:
                    scored.append((atom, score, "in_batch_owner_embedding"))
            scored.sort(key=lambda item: item[1], reverse=True)
            selected = scored[: self.atom_candidate_limit]
            selected_ids = {atom.id for atom, _, _ in selected}
            for atom, _ in prior[-4:]:
                if atom.id not in selected_ids:
                    selected.append((atom, 0.0, "in_batch_recent"))
                    selected_ids.add(atom.id)
        return tuple(
            CandidateMatch(
                object_id=atom.id,
                score=score,
                text=atom.presentation(),
                match_kind=match_kind,
            )
            for atom, score, match_kind in selected
        )

    @staticmethod
    def _validate_in_batch_resolution(
        *,
        atom: AtomRecord,
        resolution: AtomClassification | None,
        allowed_targets: set[str],
        atoms_by_id: dict[str, AtomRecord],
    ) -> AtomClassification:
        if not isinstance(resolution, AtomClassification) or not isinstance(
            resolution.decision, AtomDecision
        ):
            return AtomClassification(
                decision=AtomDecision.INDEPENDENT,
                reason="missing or invalid in-batch decision",
            )
        if resolution.decision is AtomDecision.INDEPENDENT:
            return replace(resolution, matched_atom_id=None)
        matched_id = resolution.matched_atom_id
        matched = atoms_by_id.get(matched_id or "")
        if (
            matched_id not in allowed_targets
            or matched is None
            or matched.owner_id != atom.owner_id
        ):
            return AtomClassification(
                decision=AtomDecision.INDEPENDENT,
                confidence=0.0,
                reason="invalid or cross-owner in-batch target",
            )
        return resolution

    async def _resolve_in_batch_atoms(
        self,
        *,
        provider: MemoryDecisionProvider,
        atom_inputs: Sequence[
            tuple[str, Any, dict[str, Any], AtomRecord, AtomEvidence]
        ],
        vectors: Sequence[np.ndarray],
        recent_episodes: Sequence[dict[str, Any]],
    ) -> tuple[
        list[int],
        dict[str, list[AtomEvidence]],
        dict[str, AtomClassification],
    ]:
        """Collapse batch duplicates and classify later owner-local Atoms.

        Input order is the stable representative policy.  Every semantic
        request may target only an earlier Atom from the same owner, preventing
        cycles and cross-owner matches while still allowing all five decisions.
        """

        grouped: dict[str, list[int]] = defaultdict(list)
        atoms_by_id = {item[3].id: item[3] for item in atom_inputs}
        vector_by_id = {
            item[3].id: vector for item, vector in zip(atom_inputs, vectors)
        }
        for index, item in enumerate(atom_inputs):
            grouped[item[3].owner_id].append(index)

        unique_indices: list[int] = []
        evidence_by_representative: dict[str, list[AtomEvidence]] = {}
        for indices in grouped.values():
            exact_representatives: dict[tuple[str, datetime | None], str] = {}
            for index in indices:
                atom = atom_inputs[index][3]
                evidence = atom_inputs[index][4]
                exact_key = (atom.normalized_content, atom.invalid_at)
                representative_id = exact_representatives.get(exact_key)
                if representative_id is not None:
                    evidence_by_representative[representative_id].append(evidence)
                    continue
                exact_representatives[exact_key] = atom.id
                unique_indices.append(index)
                evidence_by_representative[atom.id] = [evidence]
        unique_indices.sort()

        requests: list[AtomResolutionRequest] = []
        allowed_by_atom: dict[str, set[str]] = {}
        prior_by_owner: dict[str, list[tuple[AtomRecord, np.ndarray]]] = defaultdict(
            list
        )
        owner_summaries: dict[str, str | None] = {}
        for index in unique_indices:
            atom = atom_inputs[index][3]
            prior = prior_by_owner[atom.owner_id]
            if prior:
                candidates = self._in_batch_candidates(
                    prior=prior,
                    vector=vector_by_id[atom.id],
                )
                if candidates:
                    if atom.owner_id not in owner_summaries:
                        owner_summaries[atom.owner_id] = (
                            await self.sqlite.get_owner_summary(atom.owner_id)
                        )
                    requests.append(
                        AtomResolutionRequest(
                            atom=atom,
                            candidates=candidates,
                            owner_summary=owner_summaries[atom.owner_id],
                        )
                    )
                    allowed_by_atom[atom.id] = {
                        candidate.object_id for candidate in candidates
                    }
            prior.append((atom, vector_by_id[atom.id]))

        resolutions: dict[str, AtomClassification] = {}
        if requests:
            try:
                resolutions = await provider.resolve_atoms(
                    requests=requests,
                    recent_episodes=recent_episodes,
                )
            except Exception as exc:
                logger.warning("In-batch Atom resolution failed safely: %s", exc)

        canonical_id = {atom_id: atom_id for atom_id in atoms_by_id}
        final_indices: list[int] = []
        evolution_by_representative: dict[str, AtomClassification] = {}
        for index in unique_indices:
            atom = atom_inputs[index][3]
            allowed_targets = allowed_by_atom.get(atom.id)
            if not allowed_targets:
                final_indices.append(index)
                continue
            resolution = self._validate_in_batch_resolution(
                atom=atom,
                resolution=resolutions.get(atom.id),
                allowed_targets=allowed_targets,
                atoms_by_id=atoms_by_id,
            )
            matched_id = resolution.matched_atom_id
            if matched_id is not None:
                matched_id = canonical_id.get(matched_id, matched_id)
                resolution = replace(resolution, matched_atom_id=matched_id)
            if (
                resolution.decision is AtomDecision.DUPLICATE
                and matched_id is not None
            ):
                canonical_id[atom.id] = matched_id
                evidence_by_representative[matched_id].extend(
                    evidence_by_representative.pop(atom.id)
                )
                continue
            final_indices.append(index)
            if resolution.decision is not AtomDecision.INDEPENDENT:
                evolution_by_representative[atom.id] = resolution

        return (
            final_indices,
            evidence_by_representative,
            evolution_by_representative,
        )

    async def _apply_atom_evolution(
        self,
        *,
        source: AtomRecord,
        target: AtomRecord,
        resolution: AtomClassification,
    ) -> None:
        """Apply one validated evolution between two already-persisted Atoms."""

        decision = resolution.decision
        if (
            source.id == target.id
            or source.owner_id != target.owner_id
            or decision in (AtomDecision.INDEPENDENT, AtomDecision.DUPLICATE)
        ):
            return
        await self.sqlite.add_atom_evolution(
            source.id,
            target.id,
            decision.name,
            metadata={
                "confidence": resolution.confidence,
                "reason": resolution.reason,
            },
        )
        system_time = utc_now()
        if decision is AtomDecision.REFINEMENT:
            await self.sqlite.expire_atom(target.id, expired_at=system_time)
        elif decision is AtomDecision.TEMPORAL_SUCCESSOR:
            transition = resolution.target_invalid_at or source.valid_at
            if transition is not None and (
                target.valid_at is None or transition >= target.valid_at
            ):
                await self.sqlite.set_atom_invalid_at(
                    target.id,
                    invalid_at=transition,
                )
            await self.sqlite.expire_atom(target.id, expired_at=system_time)
        elif decision is AtomDecision.CONTRADICTION and resolution.supersedes_target:
            transition = resolution.target_invalid_at
            if transition is not None and (
                target.valid_at is None or transition >= target.valid_at
            ):
                await self.sqlite.set_atom_invalid_at(
                    target.id,
                    invalid_at=transition,
                )
            await self.sqlite.expire_atom(target.id, expired_at=system_time)

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

        await self._apply_atom_evolution(
            source=stored,
            target=matched,
            resolution=replace(resolution, decision=decision),
        )
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
            async with self._projection_lock:
                try:
                    await self._commit_serialized(chunk_results, context)
                except Exception:
                    self.notify_projection_worker()
                    raise

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
                "MAGI relationships require extracted entity containers "
                f"for every endpoint; missing {sorted(missing_endpoints)!r}"
            )

        entities, _ = await self._resolve_entities(
            engine=context.rag,
            all_nodes=all_nodes,
            all_edges=all_edges,
        )
        atom_inputs: list[
            tuple[str, Any, dict[str, Any], AtomRecord, AtomEvidence]
        ] = []

        for extracted_name, records in all_nodes.items():
            entity = entities[extracted_name]
            for record_index, record in enumerate(records):
                if record.get("magi_endpoint_only") is True:
                    continue
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
                        (
                            f"entity:{normalize_name(extracted_name)}:"
                            f"{record.get('source_id', '')}:{record_index}"
                        ),
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
                        (
                            f"relation:{normalize_name(source_name)}:"
                            f"{normalize_name(target_name)}:"
                            f"{record.get('source_id', '')}:{record_index}"
                        ),
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
        provider = self.decision_provider or LLMMemoryDecisionProvider(context.rag)
        recent_episodes = await self.sqlite.recent_episode_context(limit=4)
        (
            representative_indices,
            evidence_by_representative,
            in_batch_evolutions,
        ) = await self._resolve_in_batch_atoms(
            provider=provider,
            atom_inputs=atom_inputs,
            vectors=vectors,
            recent_episodes=recent_episodes,
        )

        resolution_requests: list[AtomResolutionRequest] = []
        atom_resolutions: dict[str, AtomClassification] = {}
        for index in representative_indices:
            item = atom_inputs[index]
            vector = vectors[index]
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

        if resolution_requests:
            try:
                atom_resolutions.update(
                    await provider.resolve_atoms(
                        requests=resolution_requests,
                        recent_episodes=recent_episodes,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Stored-history Atom resolution failed safely: %s",
                    exc,
                )
        for request in resolution_requests:
            resolution = atom_resolutions.get(request.atom.id)
            if not isinstance(resolution, AtomClassification) or not isinstance(
                resolution.decision, AtomDecision
            ):
                continue
            allowed_targets = {
                candidate.object_id for candidate in request.candidates
            }
            if resolution.decision is AtomDecision.INDEPENDENT:
                atom_resolutions[request.atom.id] = replace(
                    resolution,
                    matched_atom_id=None,
                )
            elif resolution.matched_atom_id not in allowed_targets:
                atom_resolutions[request.atom.id] = AtomClassification(
                    decision=AtomDecision.INDEPENDENT,
                    confidence=0.0,
                    reason="invalid stored-history target",
                )

        stored_by_representative: dict[str, AtomRecord] = {}
        for index in representative_indices:
            item = atom_inputs[index]
            vector = vectors[index]
            atom = item[3]
            evidences = evidence_by_representative[atom.id]
            stored = await self._apply_atom_resolution(
                engine=context.rag,
                atom=atom,
                vector=vector,
                evidence=evidences[0],
                resolution=atom_resolutions.get(
                    atom.id,
                    AtomClassification(
                        decision=AtomDecision.INDEPENDENT,
                        reason="missing stored-history batch decision",
                    ),
                ),
            )
            for evidence in evidences[1:]:
                stored = await self.sqlite.add_atom_evidence(
                    stored.id,
                    replace(evidence, atom_id=stored.id),
                )
            stored_by_representative[atom.id] = stored

        for atom_id, resolution in in_batch_evolutions.items():
            source = stored_by_representative[atom_id]
            target = stored_by_representative.get(resolution.matched_atom_id or "")
            if target is not None:
                await self._apply_atom_evolution(
                    source=source,
                    target=target,
                    resolution=resolution,
                )
        projected_nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        projected_edges: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        touched_nodes: dict[str, str] = {}
        touched_edges: dict[tuple[str, str], str] = {}

        for extracted_name, records in all_nodes.items():
            if not records or any(
                record.get("magi_endpoint_only") is not True for record in records
            ):
                continue
            entity = entities[extracted_name]
            template = records[0]
            projected_nodes[entity.canonical_name].append(
                {
                    **template,
                    "entity_name": entity.canonical_name,
                    "entity_type": entity.entity_type
                    or template.get("entity_type")
                    or "UNKNOWN",
                    "description": "",
                    "magi_endpoint_only": True,
                }
            )
            touched_nodes[entity.canonical_name] = entity.id

        for index in representative_indices:
            item = atom_inputs[index]
            kind, owner, record, atom, evidence = item
            stored = stored_by_representative[atom.id]
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

        touched_owner_ids = [*touched_nodes.values(), *touched_edges.values()]
        projection_tasks = await self.sqlite.list_projection_tasks(
            owner_ids=touched_owner_ids,
            ready_only=False,
            limit=max(1, len(touched_owner_ids)),
        )

        # Materialize one checkpoint+delta description per touched owner. The
        # SQLite Atom layer remains authoritative; graph merge receives one
        # already-compacted projection row and therefore performs no second
        # full-history LLM merge.
        for canonical_name, entity_id in touched_nodes.items():
            templates = projected_nodes[canonical_name]
            template = next(
                (
                    item
                    for item in templates
                    if item.get("magi_endpoint_only") is not True
                ),
                templates[0],
            )
            summary_state = await self._projection_description_state(
                context.rag,
                owner_id=entity_id,
                description_type="Entity",
                description_name=canonical_name,
            )
            description = summary_state.description
            if description:
                projected_nodes[canonical_name] = [
                    {
                        **template,
                        "description": description,
                        "magi_atom_order": 0,
                        "magi_endpoint_only": False,
                    }
                ]
            else:
                projected_nodes[canonical_name] = [
                    {
                        **template,
                        "description": "",
                        "magi_endpoint_only": True,
                    }
                ]
        for pair, relation_id in touched_edges.items():
            template = projected_edges[pair][0]
            summary_state = await self._projection_description_state(
                context.rag,
                owner_id=relation_id,
                description_type="Relation",
                description_name=f"{pair[0]} -> {pair[1]}",
            )
            description = summary_state.description
            projected_edges[pair] = [
                {
                    **template,
                    "description": description,
                    "magi_atom_order": 0,
                }
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
            await self.sqlite.set_owner_materialization(
                entity_id,
                atom_ids=node["atom_ids"],
                summary_cited=str(node.get("description") or ""),
            )
        for pair, relation_id in touched_edges.items():
            edge = await graph.get_edge(*pair) or {}
            atoms = await self.sqlite.list_owner_atoms(relation_id)
            edge["magi_relation_id"] = relation_id
            edge["atom_ids"] = [atom.id for atom in atoms]
            await graph.upsert_edge(pair[0], pair[1], edge_data=edge)
            await self.sqlite.set_owner_materialization(
                relation_id,
                atom_ids=edge["atom_ids"],
                summary_cited=str(edge.get("description") or ""),
            )

        # Buffered vector stores become durable before the outbox revision is
        # acknowledged.  A crash before this point leaves the owner pending and
        # startup reconciliation safely reapplies its latest SQLite state.
        await self._flush_projection_storages(context.rag)
        for task in projection_tasks:
            await self.sqlite.mark_projection_applied(
                str(task["owner_id"]),
                int(task["target_revision"]),
            )

        self._episode_contexts.pop(episode.id, None)
