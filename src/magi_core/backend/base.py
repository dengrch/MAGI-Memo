"""Backend protocols used by the public interface."""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from magi_core.memory import Episode


class RetrievalBackend(Protocol):
    async def initialize(self) -> None: ...

    async def finalize(self) -> None: ...

    async def index(self, episodes: Sequence[Episode]) -> str | None: ...

    async def query(
        self,
        text: str,
        *,
        mode: str,
        query_options: dict[str, Any],
    ) -> Any: ...

