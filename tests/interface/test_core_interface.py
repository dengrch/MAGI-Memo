from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from interface import APIState, Episode, MagiCoreAPI


class Neo4JStorage:
    async def get_knowledge_graph(
        self, node_label: str, max_depth: int, max_nodes: int | None
    ) -> dict[str, Any]:
        return {"node_label": node_label, "max_depth": max_depth}


class FakeDocStatus:
    async def get_by_id(self, doc_id: str) -> dict[str, str]:
        return {"id": doc_id, "status": "processed"}


class FakeLightRAG:
    def __init__(self, working_dir: Path, workspace: str) -> None:
        self.working_dir = str(working_dir)
        self.workspace = workspace
        self.chunk_entity_relation_graph = Neo4JStorage()
        self.doc_status = FakeDocStatus()
        self.initialized = False
        self.finalized = False
        self.inserted: list[dict[str, Any]] = []
        self.knowledge_adapter: Any = None

    def register_knowledge_ingestion_adapter(self, adapter: Any) -> None:
        self.knowledge_adapter = adapter

    async def initialize_storages(self) -> None:
        self.initialized = True

    async def check_and_migrate_data(self) -> None:
        if not self.initialized:
            raise RuntimeError("not initialized")

    async def finalize_storages(self) -> None:
        self.finalized = True

    async def ainsert(
        self,
        content: list[str],
        *,
        ids: list[str],
        file_paths: list[str],
    ) -> str:
        self.inserted.append(
            {"content": content, "ids": ids, "file_paths": file_paths}
        )
        return "track-test"

    async def aquery(self, text: str, *, param: Any) -> str:
        return f"{param.mode}:{text}"


class FailingLightRAG(FakeLightRAG):
    async def initialize_storages(self) -> None:
        self.initialized = True
        raise RuntimeError("storage initialization failed")


class CoreInterfaceTests(unittest.TestCase):
    def test_init_index_query_and_idempotency(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "workspace-a"
                rag = FakeLightRAG(root / "ragstore", "workspace-a")
                core = MagiCoreAPI.from_lightrag(workspace=root, rag=rag)

                await core.init()
                first_handle = await core.open(owner="agent-a")
                second_handle = await core.open(owner="agent-b")
                episode = Episode(id="episode-1", content="Alice knows Bob.")
                first = await first_handle.index(episode)
                second = await second_handle.index(episode)

                self.assertEqual(first.indexed_count, 1)
                self.assertEqual(first.track_id, "track-test")
                self.assertEqual(second.indexed_count, 0)
                self.assertEqual(second.skipped_count, 1)
                self.assertEqual(len(rag.inserted), 1)
                self.assertTrue((root / "ragstore" / "magi-memory.db").is_file())

                result = await second_handle.query(
                    "Who knows Bob?", mode="local", top_k=5
                )
                self.assertEqual(result, "local:Who knows Bob?")

                await first_handle.close()
                self.assertFalse(first_handle.is_open)
                self.assertTrue(second_handle.is_open)
                await core.finalize()
                self.assertTrue(rag.finalized)
                self.assertFalse(second_handle.is_open)
                self.assertEqual(core.state, APIState.FINALIZED)

        asyncio.run(scenario())

    def test_partial_initialization_cleans_engine_and_adapter(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "workspace-failure"
                rag = FailingLightRAG(root / "ragstore", "workspace-failure")
                core = MagiCoreAPI.from_lightrag(workspace=root, rag=rag)

                with self.assertRaisesRegex(
                    RuntimeError, "storage initialization failed"
                ):
                    await core.init()

                self.assertTrue(rag.finalized)
                self.assertIsNone(rag.knowledge_adapter)
                self.assertEqual(core.state, APIState.NEW)

        asyncio.run(scenario())

    def test_file_episode_and_content_conflict(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "workspace-b"
                source = Path(temporary) / "episode.txt"
                source.write_text("A file-backed episode.", encoding="utf-8")
                rag = FakeLightRAG(root / "ragstore", "workspace-b")
                core = MagiCoreAPI.from_lightrag(workspace=root, rag=rag)
                await core.init()
                handle = await core.open(owner="file-agent")

                result = await handle.index_file(
                    source, episode_id="file-episode"
                )
                self.assertEqual(result.indexed_count, 1)
                self.assertEqual(rag.inserted[0]["file_paths"], [str(source.resolve())])

                with self.assertRaises(ValueError):
                    await handle.index(
                        Episode(id="file-episode", content="Different content")
                    )
                await handle.close()
                await core.finalize()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
