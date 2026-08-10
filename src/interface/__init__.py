"""Public interfaces of MAGI Core."""

from interface.api import APIState, MagiAPI, MagiHandle
from magi_core.backend import IndexResult
from magi_core.memory import (
    Episode,
    EpisodeKind,
    ExtractedAtom,
    ExtractedEntity,
    ExtractedMemory,
    ExtractedRelation,
)
from magi_runtime import RuntimeState, WorkspaceRecord

__all__ = [
    "APIState",
    "Episode",
    "EpisodeKind",
    "ExtractedAtom",
    "ExtractedEntity",
    "ExtractedMemory",
    "ExtractedRelation",
    "IndexResult",
    "MagiAPI",
    "MagiHandle",
    "RuntimeState",
    "WorkspaceRecord",
]
