from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from magi_core.ingestion import KnowledgeCommitContext
from magi_core.magi_core import MagiCore


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[list, KnowledgeCommitContext]] = []

    async def commit(
        self,
        chunk_results: list,
        context: KnowledgeCommitContext,
    ) -> None:
        self.calls.append((chunk_results, context))


class LifecycleRecordingAdapter(RecordingAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.lifecycle: list[tuple[str, str, object]] = []

    async def prepare_episode(self, **kwargs) -> None:
        self.lifecycle.append(("prepare", kwargs["doc_id"], kwargs["content"]))

    async def complete_episode(self, doc_id: str, *, track_id=None) -> None:
        self.lifecycle.append(("complete", doc_id, track_id))

    async def fail_episode(self, doc_id: str, *, error: str) -> None:
        self.lifecycle.append(("fail", doc_id, error))


class MagiCoreIngestionSeamTests(unittest.TestCase):
    def _bare_rag(self) -> MagiCore:
        rag = object.__new__(MagiCore)
        rag._knowledge_ingestion_adapter = None
        rag.chunk_entity_relation_graph = object()
        rag.entities_vdb = object()
        rag.relationships_vdb = object()
        rag.llm_response_cache = object()
        rag.entity_chunks = object()
        rag.relation_chunks = object()
        rag._build_global_config = lambda: {"workspace": "test"}
        return rag

    def test_registered_adapter_receives_operation_context(self) -> None:
        async def scenario() -> None:
            rag = self._bare_rag()
            adapter = RecordingAdapter()
            rag.register_knowledge_ingestion_adapter(adapter)
            results = [({"A": []}, {})]

            await rag._commit_extracted_knowledge(
                chunk_results=results,
                full_entities_storage="entities",
                full_relations_storage="relations",
                doc_id="doc-1",
                pipeline_status={"busy": True},
                pipeline_status_lock="lock",
                current_file_number=2,
                total_files=3,
                file_path="episode.txt",
            )

            self.assertEqual(len(adapter.calls), 1)
            actual_results, context = adapter.calls[0]
            self.assertIs(actual_results, results)
            self.assertIs(context.rag, rag)
            self.assertEqual(context.doc_id, "doc-1")
            self.assertEqual(context.file_path, "episode.txt")
            self.assertEqual(context.current_file_number, 2)

        asyncio.run(scenario())

    def test_no_adapter_preserves_default_merge(self) -> None:
        async def scenario() -> None:
            rag = self._bare_rag()
            merge = AsyncMock()
            with patch("magi_core.magi_core.merge_nodes_and_edges", merge):
                await rag._commit_extracted_knowledge(
                    chunk_results=[({}, {})],
                    full_entities_storage=None,
                    full_relations_storage=None,
                    doc_id="doc-default",
                    pipeline_status={},
                    pipeline_status_lock=SimpleNamespace(),
                    file_path="default.txt",
                )

            merge.assert_awaited_once()
            kwargs = merge.await_args.kwargs
            self.assertEqual(kwargs["doc_id"], "doc-default")
            self.assertIs(kwargs["knowledge_graph_inst"], rag.chunk_entity_relation_graph)
            self.assertEqual(kwargs["global_config"], {"workspace": "test"})

        asyncio.run(scenario())

    def test_episode_lifecycle_hooks_are_forwarded(self) -> None:
        async def scenario() -> None:
            rag = self._bare_rag()
            adapter = LifecycleRecordingAdapter()
            rag.register_knowledge_ingestion_adapter(adapter)

            await rag._prepare_knowledge_episode(
                doc_id="episode-1",
                content="Alice joined MAGI.",
                file_path="episode.txt",
                reference_at="2026-08-05T00:00:00+00:00",
            )
            await rag._complete_knowledge_episode(
                "episode-1", track_id="track-1"
            )
            await rag._fail_knowledge_episode("episode-2", error="failed")

            self.assertEqual(
                adapter.lifecycle,
                [
                    ("prepare", "episode-1", "Alice joined MAGI."),
                    ("complete", "episode-1", "track-1"),
                    ("fail", "episode-2", "failed"),
                ],
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
