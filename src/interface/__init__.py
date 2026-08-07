"""Public interfaces of MAGI Core."""

from interface.core import APIState, MagiCoreAPI, MagiHandle
from magi_core.backend import IndexResult
from magi_core.memory import Episode, EpisodeKind

__all__ = [
    "APIState",
    "Episode",
    "EpisodeKind",
    "IndexResult",
    "MagiCoreAPI",
    "MagiHandle",
]
