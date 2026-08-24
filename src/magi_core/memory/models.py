"""Canonical domain models for the MAGI memory core."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid4, uuid5


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("memory timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def normalize_name(value: str) -> str:
    """Normalize an entity alias for exact and lexical matching."""

    return " ".join(value.casefold().strip().split())


def normalize_atom_text(value: str) -> str:
    """Normalize harmless textual variation without changing fact semantics."""

    return re.sub(r"\s+", " ", value).strip()


def stable_entity_id(workspace_id: str, canonical_name: str) -> str:
    identity = f"magi://{workspace_id}/entity/{normalize_name(canonical_name)}"
    return f"entity-{uuid5(NAMESPACE_URL, identity).hex}"


def stable_relation_id(workspace_id: str, entity_a_id: str, entity_b_id: str) -> str:
    first, second = sorted((entity_a_id, entity_b_id))
    identity = f"magi://{workspace_id}/relation/{first}/{second}"
    return f"relation-{uuid5(NAMESPACE_URL, identity).hex}"


def stable_entity_name_embedding_id(entity_id: str, name: str) -> str:
    digest = hashlib.sha256(normalize_name(name).encode("utf-8")).hexdigest()
    return f"{entity_id}:name:{digest[:20]}"


def stable_atom_id(
    workspace_id: str,
    episode_id: str,
    owner_id: str,
    extraction_key: str,
) -> str:
    identity = (
        f"magi://{workspace_id}/episode/{episode_id}/atom/{owner_id}/{extraction_key}"
    )
    return f"atom-{uuid5(NAMESPACE_URL, identity).hex}"


class EpisodeKind(str, Enum):
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    MULTIMODAL_TEXT = "multimodal_text"


class EpisodeStatus(str, Enum):
    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"


class AtomDecision(str, Enum):
    DUPLICATE = "duplicate"
    REFINEMENT = "refinement"
    TEMPORAL_SUCCESSOR = "temporal_successor"
    CONTRADICTION = "contradiction"
    INDEPENDENT = "independent"


class TemporalStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    INVALID = "invalid"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Episode:
    """One immutable input unit submitted to memory."""

    content: str
    id: str = field(default_factory=lambda: f"episode-{uuid4().hex}")
    kind: EpisodeKind = EpisodeKind.DOCUMENT
    reference_at: datetime | None = None
    source_uri: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("episode id must not be empty")
        if not self.content.strip():
            raise ValueError("episode content must not be empty")
        object.__setattr__(
            self,
            "reference_at",
            ensure_utc(self.reference_at) or utc_now(),
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def effective_reference_at(self) -> datetime:
        return self.reference_at  # type: ignore[return-value]

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        episode_id: str | None = None,
        kind: EpisodeKind = EpisodeKind.DOCUMENT,
        reference_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
        encoding: str = "utf-8",
    ) -> "Episode":
        resolved = Path(path).expanduser().resolve()
        content = resolved.read_text(encoding=encoding)
        return cls(
            id=episode_id or f"episode-{uuid4().hex}",
            kind=kind,
            content=content,
            reference_at=reference_at,
            source_uri=str(resolved),
            metadata=metadata or {},
        )


@dataclass(frozen=True, slots=True)
class EntityRecord:
    id: str
    workspace_id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    entity_type: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    expired_at: datetime | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "expired_at", ensure_utc(self.expired_at))

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.canonical_name)


@dataclass(frozen=True, slots=True)
class RelationRecord:
    id: str
    workspace_id: str
    entity_a_id: str
    entity_b_id: str
    entity_a_name: str
    entity_b_name: str
    keywords: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    expired_at: datetime | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.entity_a_name.strip() or not self.entity_b_name.strip():
            raise ValueError("relation endpoint names must not be empty")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "expired_at", ensure_utc(self.expired_at))


@dataclass(frozen=True, slots=True)
class AtomRecord:
    id: str
    workspace_id: str
    owner_id: str
    content: str
    valid_at: datetime | None
    invalid_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    expired_at: datetime | None = None
    subject_entity_id: str | None = None
    predicate: str | None = None
    object_entity_id: str | None = None
    relation_keywords: tuple[str, ...] = ()
    temporal_text: str | None = None
    temporal_precision: str | None = None
    confidence: float | None = None
    importance: float | None = None
    support_count: int = 0

    def __post_init__(self) -> None:
        if not normalize_atom_text(self.content):
            raise ValueError("atom content must not be empty")
        if not self.owner_id.startswith(("entity-", "relation-")):
            raise ValueError("atom owner_id must identify an entity or relation")
        valid_at = ensure_utc(self.valid_at)
        invalid_at = ensure_utc(self.invalid_at)
        created_at = ensure_utc(self.created_at)
        expired_at = ensure_utc(self.expired_at)
        if valid_at and invalid_at and invalid_at < valid_at:
            raise ValueError("invalid_at must not be earlier than valid_at")
        if self.is_relation_atom and (
            not self.subject_entity_id or not self.object_entity_id
        ):
            raise ValueError("relation atoms require subject and object entity ids")
        if not self.is_relation_atom and (
            self.subject_entity_id or self.object_entity_id
        ):
            raise ValueError("entity atoms must not carry relation endpoints")
        object.__setattr__(self, "valid_at", valid_at)
        object.__setattr__(self, "invalid_at", invalid_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expired_at", expired_at)

    @property
    def normalized_content(self) -> str:
        return normalize_atom_text(self.content)

    @property
    def normalized_hash(self) -> str:
        return hashlib.sha256(self.normalized_content.encode("utf-8")).hexdigest()

    @property
    def is_relation_atom(self) -> bool:
        return self.owner_id.startswith("relation-")

    @property
    def owner_type(self) -> str:
        return "relation" if self.is_relation_atom else "entity"

    @property
    def fingerprint(self) -> str:
        """Unique stored occurrence fingerprint.

        Phase one deliberately preserves repeated observations in insertion
        order.  ``semantic_fingerprint`` remains available for a future
        deduplication policy, while this value is unique per Atom occurrence.
        """

        return hashlib.sha256(
            f"{self.semantic_fingerprint}\x1f{self.id}".encode("utf-8")
        ).hexdigest()

    @property
    def semantic_fingerprint(self) -> str:
        values = (
            self.workspace_id,
            self.owner_id,
            self.normalized_hash,
            self.valid_at.isoformat() if self.valid_at else "",
            self.invalid_at.isoformat() if self.invalid_at else "",
        )
        return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()

    def temporal_status(self, at: datetime | None = None) -> TemporalStatus:
        point = ensure_utc(at) or utc_now()
        if self.expired_at is not None:
            return TemporalStatus.EXPIRED
        if self.valid_at is not None and point < self.valid_at:
            return TemporalStatus.PENDING
        if self.invalid_at is not None and point >= self.invalid_at:
            return TemporalStatus.INVALID
        return TemporalStatus.ACTIVE

    def presentation(self, at: datetime | None = None, *, cite: bool = True) -> str:
        fields = [f"status={self.temporal_status(at).value}"]
        if self.valid_at is not None:
            fields.append(f"valid_at={self.valid_at.isoformat()}")
        if self.invalid_at is not None:
            fields.append(f"invalid_at={self.invalid_at.isoformat()}")
        prefix = f"[{self.id}] " if cite else ""
        return f"{prefix}[{'; '.join(fields)}] {self.normalized_content}"


@dataclass(frozen=True, slots=True)
class AtomEvidence:
    atom_id: str
    episode_id: str
    quote: str | None = None
    span_start: int = -1
    span_end: int = -1
    extraction_revision: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


@dataclass(frozen=True, slots=True)
class AtomMemory:
    """One Atom together with every Episode evidence row that supports it."""

    atom: AtomRecord
    evidence: tuple[AtomEvidence, ...] = ()

    @property
    def episode_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.episode_id for item in self.evidence))


@dataclass(frozen=True, slots=True)
class CandidateMatch:
    object_id: str
    score: float
    text: str
    match_kind: str


@dataclass(frozen=True, slots=True)
class EntityResolutionRequest:
    key: str
    name: str
    aliases: tuple[str, ...]
    entity_type: str | None
    atom_texts: tuple[str, ...]
    candidates: tuple[CandidateMatch, ...]


@dataclass(frozen=True, slots=True)
class EntityResolution:
    canonical_entity_id: str | None
    canonical_name: str | None
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class AtomClassification:
    decision: AtomDecision
    matched_atom_id: str | None = None
    target_invalid_at: datetime | None = None
    supersedes_target: bool = False
    confidence: float = 0.0
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_invalid_at",
            ensure_utc(self.target_invalid_at),
        )


@dataclass(frozen=True, slots=True)
class AtomResolutionRequest:
    """One new Atom and the only owner-local Atoms it may resolve against."""

    atom: AtomRecord
    candidates: tuple[CandidateMatch, ...]
    owner_summary: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedAtom:
    """One caller-extracted Atom ready for strict MAGI commit."""

    content: str
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    temporal_text: str | None = None
    temporal_precision: str | None = None
    predicate: str | None = None
    confidence: float | None = None
    importance: float | None = None

    def __post_init__(self) -> None:
        if not normalize_atom_text(self.content):
            raise ValueError("extracted Atom content must not be empty")
        object.__setattr__(self, "valid_at", ensure_utc(self.valid_at))
        object.__setattr__(self, "invalid_at", ensure_utc(self.invalid_at))
        if (
            self.valid_at is not None
            and self.invalid_at is not None
            and self.invalid_at < self.valid_at
        ):
            raise ValueError("invalid_at must not be earlier than valid_at")
        for field_name in ("confidence", "importance"):
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between 0 and 1")

    def to_payload(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "valid_at": self.valid_at.isoformat() if self.valid_at else None,
            "invalid_at": self.invalid_at.isoformat() if self.invalid_at else None,
            "temporal_text": self.temporal_text,
            "temporal_precision": self.temporal_precision,
            "predicate": self.predicate,
            "confidence": self.confidence,
            "importance": self.importance,
        }


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    """One extracted entity and the Atoms owned by it.

    ``atoms`` may be empty when the entity is only an endpoint of a valid
    :class:`ExtractedRelation`.  The relation Atom is the factual owner in
    that case; :class:`ExtractedMemory` also synthesizes this container when a
    caller submits only the relation endpoint name.  Callers must not invent an
    entity Atom merely to satisfy a container shape.
    """

    name: str
    atoms: tuple[ExtractedAtom, ...]
    aliases: tuple[str, ...] = ()
    entity_type: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("extracted entity name must not be empty")
        object.__setattr__(
            self,
            "aliases",
            tuple(
                dict.fromkeys(
                    alias.strip()
                    for alias in self.aliases
                    if alias.strip() and normalize_name(alias) != normalize_name(self.name)
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ExtractedRelation:
    """One extracted semantic relation and the Atoms owned by it."""

    source: str
    target: str
    atoms: tuple[ExtractedAtom, ...]
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.target.strip():
            raise ValueError("relation endpoints must not be empty")
        if normalize_name(self.source) == normalize_name(self.target):
            raise ValueError("self relations are not supported by MAGI projection")
        if not self.atoms:
            raise ValueError("each extracted relation must own at least one Atom")
        object.__setattr__(
            self,
            "keywords",
            tuple(dict.fromkeys(item.strip() for item in self.keywords if item.strip())),
        )


@dataclass(frozen=True, slots=True)
class ExtractedMemory:
    """An Episode plus knowledge already extracted by an outer Agent.

    The Episode remains the evidence authority.  This object skips only the
    first extraction LLM call; entity resolution, Atom resolution, temporal
    evolution, projection and durable document bookkeeping remain unchanged.
    """

    episode: Episode
    entities: tuple[ExtractedEntity, ...] = ()
    relations: tuple[ExtractedRelation, ...] = ()

    def __post_init__(self) -> None:
        by_name: dict[str, ExtractedEntity] = {}
        for entity in self.entities:
            key = normalize_name(entity.name)
            if key in by_name:
                raise ValueError(f"duplicate extracted entity name {entity.name!r}")
            by_name[key] = entity

        synthesized: list[ExtractedEntity] = []
        for relation in self.relations:
            for name in (relation.source, relation.target):
                key = normalize_name(name)
                if key in by_name:
                    continue
                entity = ExtractedEntity(name=name, atoms=())
                synthesized.append(entity)
                by_name[key] = entity
        if synthesized:
            object.__setattr__(self, "entities", self.entities + tuple(synthesized))
        if not self.entities:
            raise ValueError(
                "extracted memory must contain an entity or a relationship"
            )
        referenced_entities = {
            normalize_name(name)
            for relation in self.relations
            for name in (relation.source, relation.target)
        }
        unsupported = [
            entity.name
            for entity in self.entities
            if not entity.atoms
            and normalize_name(entity.name) not in referenced_entities
        ]
        if unsupported:
            raise ValueError(
                "entities without Entity Atoms must be endpoints of a valid "
                f"relationship; unsupported {unsupported!r}"
            )

    @classmethod
    def create(
        cls,
        *,
        episode: Episode,
        entities: Sequence[ExtractedEntity] = (),
        relations: Sequence[ExtractedRelation] = (),
    ) -> "ExtractedMemory":
        return cls(episode, tuple(entities), tuple(relations))

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "magi.extracted-memory.v1",
            "entities": [
                {
                    "name": entity.name,
                    "aliases": list(entity.aliases),
                    "entity_type": entity.entity_type,
                    "atoms": [atom.to_payload() for atom in entity.atoms],
                }
                for entity in self.entities
            ],
            "relations": [
                {
                    "source": relation.source,
                    "target": relation.target,
                    "keywords": list(relation.keywords),
                    "atoms": [atom.to_payload() for atom in relation.atoms],
                }
                for relation in self.relations
            ],
        }
