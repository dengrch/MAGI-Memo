"""Borrowed Neo4j capability exposed by a LightRAG instance."""

from __future__ import annotations

from typing import Any


class Neo4jBackend:
    """Expose graph capabilities without owning a second driver lifecycle."""

    def __init__(self, graph_storage: Any) -> None:
        self.raw = graph_storage
        self._initialized = False

    @property
    def is_neo4j(self) -> bool:
        return type(self.raw).__name__ == "Neo4JStorage"

    async def initialize(self) -> None:
        # LightRAG owns and initializes this storage. This facade only validates
        # that the graph capability is present after LightRAG startup.
        if self.raw is None:
            raise RuntimeError("LightRAG did not provide a graph storage")
        if not self.is_neo4j:
            raise RuntimeError(
                "MAGI Core requires Neo4JStorage as its graph backend"
            )
        self._initialized = True

    async def finalize(self) -> None:
        # The driver is finalized by LightRAG exactly once.
        self._initialized = False

    async def get_knowledge_graph(
        self,
        node_label: str,
        *,
        max_depth: int = 3,
        max_nodes: int | None = None,
    ) -> Any:
        if not self._initialized:
            raise RuntimeError("Neo4j backend is not initialized")
        return await self.raw.get_knowledge_graph(node_label, max_depth, max_nodes)
