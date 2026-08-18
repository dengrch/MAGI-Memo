from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from interface import (
    APIState,
    Episode,
    ExtractedAtom,
    ExtractedEntity,
    ExtractedMemory,
    ExtractedRelation,
    MagiAPI,
)
from magi_core.workspace_cleanup import WorkspaceCleanupResult


class Neo4JStorage:
    async def initialize(self) -> None:
        return None

    async def finalize(self) -> None:
        return None


class FakeDocStatus:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def get_by_id(self, doc_id: str) -> dict[str, Any] | None:
        return self.rows.get(doc_id)


class FakeEngine:
    def __init__(self, working_dir: Path, workspace: str) -> None:
        self.working_dir = str(working_dir)
        self.workspace = workspace
        self.chunk_entity_relation_graph = Neo4JStorage()
        self.doc_status = FakeDocStatus()
        self.knowledge_adapter: Any = None
        self.query_started = asyncio.Event()
        self.query_release = asyncio.Event()
        self.extracted: list[ExtractedMemory] = []
        self.accept_extracted = True
        self.finalized = False

    def register_knowledge_ingestion_adapter(self, adapter: Any) -> None:
        self.knowledge_adapter = adapter

    async def initialize_storages(self) -> None:
        return None

    async def check_and_migrate_data(self) -> None:
        return None

    async def finalize_storages(self) -> None:
        self.finalized = True

    async def ainsert(
        self,
        content: list[str],
        *,
        ids: list[str],
        file_paths: list[str],
    ) -> str:
        for text, doc_id, file_path in zip(content, ids, file_paths, strict=True):
            await self.knowledge_adapter.prepare_episode(
                doc_id=doc_id,
                content=text,
                file_path=file_path,
            )
            self.doc_status.rows[doc_id] = {"status": "processed"}
            await self.knowledge_adapter.complete_episode(
                doc_id,
                track_id="track-raw",
            )
        return "track-raw"

    async def aingest_extracted(
        self, memories: list[ExtractedMemory]
    ) -> str:
        self.extracted.extend(memories)
        if not self.accept_extracted:
            return "track-rejected"
        for memory in memories:
            await self.knowledge_adapter.prepare_episode(
                doc_id=memory.episode.id,
                content=memory.episode.content,
                file_path=f"episode-{memory.episode.id}.txt",
                reference_at=memory.episode.effective_reference_at.isoformat(),
            )
            self.doc_status.rows[memory.episode.id] = {"status": "processed"}
            await self.knowledge_adapter.complete_episode(
                memory.episode.id,
                track_id="track-extracted",
            )
        return "track-extracted"

    async def aquery(self, text: str, *, param: Any) -> str:
        self.query_started.set()
        await self.query_release.wait()
        return f"{param.mode}:{text}"

    async def get_processing_status(self) -> dict[str, int]:
        return {"processed": len(self.doc_status.rows)}

    async def aclear_workspace_data(
        self, *, reinitialize_doc_status: bool = False
    ) -> WorkspaceCleanupResult:
        del reinitialize_doc_status
        self.doc_status.rows.clear()
        return WorkspaceCleanupResult(
            episode_backup_ids=(f"episode-{self.workspace}",),
            dropped_storages=(f"FakeStorage:{self.workspace}",),
            errors=(),
        )


@pytest.mark.asyncio
async def test_close_drains_only_its_in_flight_calls() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "mgc-test"
        engines: list[FakeEngine] = []

        def factory(ragstore: Path, workspace: str) -> FakeEngine:
            engine = FakeEngine(ragstore, workspace)
            engines.append(engine)
            return engine

        api = MagiAPI.build(
            workspace=root,
            workspace_id="default",
            core_factory=factory,
        )
        await api.init()
        first = await api.open(owner="agent-a")
        second = await api.open(owner="agent-b")

        query_task = asyncio.create_task(first.search("hello"))
        await engines[0].query_started.wait()
        close_task = asyncio.create_task(first.close())
        await asyncio.sleep(0)
        assert not close_task.done()
        assert second.is_open

        engines[0].query_release.set()
        assert await query_task == "mix:hello"
        await close_task
        assert not first.is_open
        assert second.is_open
        await second.close()
        await api.finalize()
        assert api.state is APIState.FINALIZED


@pytest.mark.asyncio
async def test_workspace_switch_rebuilds_the_single_instance() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "mgc-test"
        engines: list[FakeEngine] = []

        def factory(ragstore: Path, workspace: str) -> FakeEngine:
            engine = FakeEngine(ragstore, workspace)
            engines.append(engine)
            return engine

        api = MagiAPI.build(
            workspace=root,
            workspace_id="default",
            core_factory=factory,
        )
        await api.init()
        handle = await api.open(owner="webui")
        api.create_workspace("Research", workspace_id="research")
        with pytest.raises(RuntimeError, match="every public handle"):
            await api.switch_workspace("research")
        await handle.close()
        await api.switch_workspace("research")
        assert engines[0].finalized
        assert api.runtime.active_workspace_id == "research"
        assert engines[1].workspace == "research"
        assert Path(engines[1].working_dir) == (
            root / "workspaces/research/ragstore"
        ).resolve()
        await api.finalize()

        registry = json.loads((root / "workspaces.json").read_text())
        assert registry["schema"] == 2
        assert registry["active_workspace_id"] == "research"

        restarted_engines: list[FakeEngine] = []

        def restarted_factory(ragstore: Path, workspace: str) -> FakeEngine:
            engine = FakeEngine(ragstore, workspace)
            restarted_engines.append(engine)
            return engine

        restarted = MagiAPI.build(
            workspace_home=root,
            workspace_id="default",
            core_factory=restarted_factory,
        )
        await restarted.init()
        assert restarted.runtime.active_workspace_id == "research"
        assert restarted_engines[0].workspace == "research"
        await restarted.finalize()


def test_workspace_id_is_generated_from_label_and_deduplicated() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "mgc-test"
        api = MagiAPI.build(
            workspace=root,
            workspace_id="default",
            core_factory=lambda ragstore, workspace: FakeEngine(ragstore, workspace),
        )

        first = api.create_workspace("Research Notes")
        second = api.create_workspace("Research Notes")

        assert first.id == "research-notes"
        assert second.id == "research-notes-2"
        assert first.label == second.label == "Research Notes"
        assert first.path.name == "research-notes"
        assert second.path.name == "research-notes-2"


@pytest.mark.asyncio
async def test_delete_inactive_workspace_keeps_active_runtime_ready() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "mgc-test"
        engines: list[FakeEngine] = []

        def factory(ragstore: Path, workspace: str) -> FakeEngine:
            engine = FakeEngine(ragstore, workspace)
            engines.append(engine)
            return engine

        api = MagiAPI.build(
            workspace=root,
            workspace_id="default",
            core_factory=factory,
        )
        await api.init()
        record = api.create_workspace("Research", workspace_id="research")

        result = await api.delete_workspace("research")

        assert result.deleted_workspace_id == "research"
        assert result.active_workspace_id == "default"
        assert result.deleted_episode_count == 1
        assert result.dropped_storage_count == 1
        assert api.runtime.active_workspace_id == "default"
        assert not record.path.exists()
        assert engines[-1].workspace == "research"
        assert engines[-1].finalized
        await api.finalize()


@pytest.mark.asyncio
async def test_extracted_ingest_skips_engine_extraction_entrypoint() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "mgc-test"
        engines: list[FakeEngine] = []

        def factory(ragstore: Path, workspace: str) -> FakeEngine:
            engine = FakeEngine(ragstore, workspace)
            engines.append(engine)
            return engine

        api = MagiAPI.build(
            workspace=root,
            workspace_id="default",
            core_factory=factory,
        )
        await api.init()
        handle = await api.open(owner="agent")
        episode = Episode(
            id="episode-extracted",
            content="Alice joined MAGI and now works with Bob.",
            reference_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        memory = ExtractedMemory.create(
            episode=episode,
            entities=(
                ExtractedEntity(
                    "Alice",
                    (ExtractedAtom("Alice joined MAGI."),),
                    aliases=("A. Example",),
                    entity_type="person",
                ),
                ExtractedEntity(
                    "Bob",
                    (ExtractedAtom("Bob works with Alice."),),
                    entity_type="person",
                ),
            ),
            relations=(
                ExtractedRelation(
                    "Alice",
                    "Bob",
                    (ExtractedAtom("Alice works with Bob."),),
                    keywords=("works with",),
                ),
            ),
        )
        result = await handle.ingest_extracted(memory)
        assert result.track_id == "track-extracted"
        assert engines[0].extracted == [memory]
        stored = await api.runtime.backend.sqlite.get_episode(memory.episode.id)
        assert stored is not None
        assert stored["content"] == episode.content
        await handle.close()
        await api.finalize()


@pytest.mark.asyncio
async def test_rejected_extracted_ingest_does_not_leave_orphan_episode() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "mgc-test"
        engines: list[FakeEngine] = []

        def factory(ragstore: Path, workspace: str) -> FakeEngine:
            engine = FakeEngine(ragstore, workspace)
            engine.accept_extracted = False
            engines.append(engine)
            return engine

        api = MagiAPI.build(
            workspace=root,
            workspace_id="default",
            core_factory=factory,
        )
        await api.init()
        handle = await api.open(owner="agent")
        episode = Episode(content="Alice prefers concise status reports.")
        memory = ExtractedMemory.create(
            episode=episode,
            entities=(
                ExtractedEntity(
                    "Alice",
                    (ExtractedAtom("Alice prefers concise status reports."),),
                ),
            ),
        )

        with pytest.raises(RuntimeError, match=f"{episode.id}: missing"):
            await handle.ingest_extracted(memory)

        assert await api.runtime.backend.sqlite.get_episode(episode.id) is None
        assert (
            api.runtime.backend.engine.memory_adapter.get_extraction_context(episode.id)
            == {}
        )
        await handle.close()
        await api.finalize()
