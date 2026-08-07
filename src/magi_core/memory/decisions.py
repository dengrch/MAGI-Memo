"""LLM-backed entity resolution and Atom classification decisions."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

from magi_core.memory.models import (
    AtomClassification,
    AtomDecision,
    AtomRecord,
    AtomResolutionRequest,
    CandidateMatch,
    EntityResolution,
    EntityResolutionRequest,
)
from magi_core.prompt import PROMPTS
from magi_core.utils import logger, tolerant_load_json_dict


class MemoryDecisionProvider(Protocol):
    async def resolve_entities(
        self,
        *,
        requests: Sequence[EntityResolutionRequest],
        recent_episodes: Sequence[dict[str, Any]],
    ) -> dict[str, EntityResolution]: ...

    async def resolve_atoms(
        self,
        *,
        requests: Sequence[AtomResolutionRequest],
        recent_episodes: Sequence[dict[str, Any]],
    ) -> dict[str, AtomClassification]: ...


class LLMMemoryDecisionProvider:
    """Use independently configurable small-model roles for write decisions."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def _role(self, name: str):
        config = self.engine._build_global_config()
        return config["role_llm_funcs"][name]

    async def resolve_entities(
        self,
        *,
        requests: Sequence[EntityResolutionRequest],
        recent_episodes: Sequence[dict[str, Any]],
    ) -> dict[str, EntityResolution]:
        if not requests:
            return {}

        # Candidate generation is the authority for whether an existing
        # identity can be selected. With no candidates there is nothing for an
        # LLM to decide, so accept the extracted canonical name locally. This
        # is especially important for a fresh workspace, where entity
        # resolution would otherwise add a completely redundant model call.
        resolved = {
            request.key: EntityResolution(
                canonical_entity_id=None,
                canonical_name=request.name,
                confidence=1.0,
                reason="no existing candidates",
            )
            for request in requests
            if not request.candidates
        }
        requests_with_candidates = [
            request for request in requests if request.candidates
        ]
        if not requests_with_candidates:
            return resolved

        candidate_ids = {
            request.key: {item.object_id for item in request.candidates}
            for request in requests_with_candidates
        }
        prompt = PROMPTS["magi_entity_resolution_prompt"].format(
            new_entities=json.dumps(
                [asdict(request) for request in requests_with_candidates],
                ensure_ascii=False,
            ),
            recent_episodes=json.dumps(list(recent_episodes), ensure_ascii=False),
        )
        try:
            response = await self._role("resolve")(
                prompt,
                response_format={"type": "json_object"},
            )
            parsed = tolerant_load_json_dict(response)
            rows = parsed.get("resolutions")
            if not isinstance(rows, list):
                rows = []
            by_key = {str(row.get("key")): row for row in rows if isinstance(row, dict)}
            for request in requests_with_candidates:
                row = by_key.get(request.key, {})
                selected = row.get("entity_id")
                if selected not in candidate_ids[request.key]:
                    selected = None
                selected_name = next(
                    (
                        candidate.text.splitlines()[0]
                        for candidate in request.candidates
                        if candidate.object_id == selected
                    ),
                    None,
                )
                if selected is None:
                    proposed = str(row.get("canonical_name") or "").strip()
                    selected_name = proposed or None
                resolved[request.key] = EntityResolution(
                    canonical_entity_id=selected,
                    canonical_name=selected_name,
                    confidence=float(row.get("confidence") or 0.0),
                    reason=str(row.get("reason") or ""),
                )
            return resolved
        except Exception as exc:
            logger.warning("Entity resolution failed safely: %s", exc)
            resolved.update(
                {
                    request.key: EntityResolution(None, None, 0.0, "decision failure")
                    for request in requests_with_candidates
                }
            )
            return resolved

    @staticmethod
    def _atom_payload(atom: AtomRecord) -> dict[str, Any]:
        return {
            "id": atom.id,
            "owner_id": atom.owner_id,
            "owner_type": atom.owner_type,
            "content": atom.content,
            "status": atom.temporal_status().value,
            "valid_at": atom.valid_at.isoformat() if atom.valid_at else None,
            "invalid_at": atom.invalid_at.isoformat() if atom.invalid_at else None,
            "created_at": atom.created_at.isoformat(),
            "expired_at": atom.expired_at.isoformat() if atom.expired_at else None,
            "subject_entity_id": atom.subject_entity_id,
            "predicate": atom.predicate,
            "object_entity_id": atom.object_entity_id,
            "confidence": atom.confidence,
            "importance": atom.importance,
        }

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def resolve_atoms(
        self,
        *,
        requests: Sequence[AtomResolutionRequest],
        recent_episodes: Sequence[dict[str, Any]],
    ) -> dict[str, AtomClassification]:
        resolved = {
            request.atom.id: AtomClassification(
                decision=AtomDecision.INDEPENDENT,
                confidence=1.0,
                reason="no stored candidates",
            )
            for request in requests
            if not request.candidates
        }
        requests_with_candidates = [
            request for request in requests if request.candidates
        ]
        if not requests_with_candidates:
            return resolved

        candidate_table: dict[str, dict[str, Any]] = {}
        allowed_by_atom: dict[str, set[str]] = {}
        request_rows: list[dict[str, Any]] = []
        for request in requests_with_candidates:
            allowed = {candidate.object_id for candidate in request.candidates}
            allowed_by_atom[request.atom.id] = allowed
            for candidate in request.candidates:
                candidate_table.setdefault(
                    candidate.object_id,
                    asdict(candidate),
                )
            request_rows.append(
                {
                    "atom": self._atom_payload(request.atom),
                    "candidate_atom_ids": sorted(allowed),
                    "owner_summary": request.owner_summary,
                }
            )

        prompt = PROMPTS["magi_atom_resolution_prompt"].format(
            new_atom_requests=json.dumps(request_rows, ensure_ascii=False),
            candidate_atoms=json.dumps(
                list(candidate_table.values()), ensure_ascii=False
            ),
            recent_episodes=json.dumps(list(recent_episodes), ensure_ascii=False),
        )
        try:
            response = await self._role("deduplicate")(
                prompt,
                response_format={"type": "json_object"},
            )
            parsed = tolerant_load_json_dict(response)
            rows = parsed.get("decisions")
            if not isinstance(rows, list):
                rows = []
            by_id = {
                str(row.get("new_atom_id")): row
                for row in rows
                if isinstance(row, dict)
            }
            for request in requests_with_candidates:
                atom_id = request.atom.id
                row = by_id.get(atom_id, {})
                try:
                    decision = AtomDecision(str(row.get("decision", "")).lower())
                except ValueError:
                    decision = AtomDecision.INDEPENDENT
                matched = row.get("matched_atom_id")
                if matched not in allowed_by_atom[atom_id]:
                    matched = None
                if decision is not AtomDecision.INDEPENDENT and matched is None:
                    decision = AtomDecision.INDEPENDENT
                supersedes = bool(row.get("supersedes_target", False))
                target_invalid_at = self._parse_time(row.get("target_invalid_at"))
                if decision not in (
                    AtomDecision.TEMPORAL_SUCCESSOR,
                    AtomDecision.CONTRADICTION,
                ):
                    target_invalid_at = None
                if decision is not AtomDecision.CONTRADICTION:
                    supersedes = False
                resolved[atom_id] = AtomClassification(
                    decision=decision,
                    matched_atom_id=matched,
                    target_invalid_at=target_invalid_at,
                    supersedes_target=supersedes,
                    confidence=float(row.get("confidence") or 0.0),
                    reason=str(row.get("reason") or ""),
                )
            return resolved
        except Exception as exc:
            logger.warning("Batch Atom resolution failed safely: %s", exc)
            resolved.update(
                {
                    request.atom.id: AtomClassification(
                        decision=AtomDecision.INDEPENDENT,
                        confidence=0.0,
                        reason="batch decision failure",
                    )
                    for request in requests_with_candidates
                }
            )
            return resolved

    async def classify_atom(
        self,
        *,
        atom: AtomRecord,
        candidates: Sequence[CandidateMatch],
    ) -> AtomClassification:
        """Compatibility wrapper; the write path uses one batch call."""

        return (
            await self.resolve_atoms(
                requests=[
                    AtomResolutionRequest(atom=atom, candidates=tuple(candidates))
                ],
                recent_episodes=[],
            )
        )[atom.id]
