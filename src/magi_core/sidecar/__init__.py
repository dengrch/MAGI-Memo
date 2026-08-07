"""LightRAG Sidecar writer infrastructure.

Spec: ``docs/LightRAGSidecarFormat-zh.md``.

This package owns the *single executable specification* of the LightRAG Sidecar
file format. Parser engines (native / mineru / docling) hand it an
``IRDoc`` (intermediate representation) describing the document; the writer
emits the spec-compliant ``*.parsed/`` directory.

See :func:`magi_core.sidecar.writer.write_sidecar` for the entry point.
"""

from typing import TYPE_CHECKING

from magi_core.sidecar.ir import (
    AssetSpec,
    IRBlock,
    IRDoc,
    IRDrawing,
    IREquation,
    IRPosition,
    IRTable,
)
from magi_core.sidecar.writer import write_sidecar

if TYPE_CHECKING:
    from magi_core.sidecar.backfill import backfill_chunk_sidecars

__all__ = [
    "AssetSpec",
    "IRBlock",
    "IRDoc",
    "IRDrawing",
    "IREquation",
    "IRPosition",
    "IRTable",
    "backfill_chunk_sidecars",
    "write_sidecar",
]


def __getattr__(name: str):
    # Lazily expose ``backfill_chunk_sidecars`` so that merely importing
    # ``magi_core.sidecar`` (for the IR/writer exports) does not pull in
    # ``magi_core.sidecar.backfill`` -> ``magi_core.exceptions`` -> ``httpx``.
    # ``httpx`` only ships with the ``api`` extra, so an eager import would
    # break core installs that just need the writer.
    if name == "backfill_chunk_sidecars":
        from magi_core.sidecar.backfill import backfill_chunk_sidecars

        return backfill_chunk_sidecars
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
