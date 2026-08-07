from typing import TYPE_CHECKING, Any

from ._version import __version__ as __version__
from .memory import (
    AtomClassification,
    AtomDecision,
    AtomEvidence,
    AtomMemory,
    AtomRecord,
    EntityRecord,
    Episode,
    EpisodeKind,
    EpisodeStatus,
    RelationRecord,
    TemporalStatus,
)
from .workspace import WorkspaceLayout

__all__ = [
    "MagiCore",
    "LightRAG",
    "QueryParam",
    "RoleLLMConfig",
    "RoleSpec",
    "ROLES",
    "Episode",
    "EpisodeKind",
    "EpisodeStatus",
    "AtomClassification",
    "AtomDecision",
    "AtomEvidence",
    "AtomMemory",
    "AtomRecord",
    "EntityRecord",
    "RelationRecord",
    "TemporalStatus",
    "WorkspaceLayout",
    "__version__",
]

if TYPE_CHECKING:
    from .magi_core import (
        LightRAG as LightRAG,
        MagiCore as MagiCore,
        QueryParam as QueryParam,
        ROLES as ROLES,
        RoleLLMConfig as RoleLLMConfig,
        RoleSpec as RoleSpec,
    )


_LAZY_EXPORTS = {
    "MagiCore",
    "LightRAG",
    "QueryParam",
    "RoleLLMConfig",
    "RoleSpec",
    "ROLES",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        from .magi_core import (
            LightRAG,
            MagiCore,
            QueryParam,
            RoleLLMConfig,
            RoleSpec,
            ROLES,
        )

        values = {
            "MagiCore": MagiCore,
            "LightRAG": LightRAG,
            "QueryParam": QueryParam,
            "RoleLLMConfig": RoleLLMConfig,
            "RoleSpec": RoleSpec,
            "ROLES": ROLES,
        }
        value = values[name]
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__author__ = "MAGI Memo contributors; based on HKUDS LightRAG"
