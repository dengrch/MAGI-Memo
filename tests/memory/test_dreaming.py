from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import EntityRecord
from magi_core.memory.dreaming import DreamingRunner, canonicalize_communities


def test_community_ids_depend_on_members_not_gds_local_ids() -> None:
    first = canonicalize_communities({"Alice": 4, "Bob": 4, "Carol": 9})
    second = canonicalize_communities({"Alice": 81, "Bob": 81, "Carol": 2})

    assert first == second
    assert first["Alice"] == first["Bob"]
    assert "Carol" not in first


def test_singleton_partitions_are_not_communities() -> None:
    assignments = canonicalize_communities(
        {"Alice": 1, "Bob": 1, "Isolated A": 2, "Isolated B": 3}
    )

    assert set(assignments) == {"Alice", "Bob"}
    assert len(set(assignments.values())) == 1


def test_sqlite_dreaming_snapshot_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = SQLiteBackend(tmp_path / "memory.sqlite3", "workspace-a")
        await backend.initialize()
        await backend.put_entity(
            EntityRecord(
                id="entity-alice",
                workspace_id="workspace-a",
                canonical_name="Alice",
            )
        )
        await backend.put_entity(
            EntityRecord(
                id="entity-bob",
                workspace_id="workspace-a",
                canonical_name="Bob",
            )
        )
        run = await backend.start_dreaming_run({"random_seed": 19})
        with pytest.raises(RuntimeError, match="already active"):
            await backend.start_dreaming_run({"random_seed": 20})

        await backend.update_dreaming_run_phase(run["run_id"], "projecting")
        await backend.prepare_dreaming_snapshot(
            run_id=run["run_id"],
            snapshot_id="snapshot-a",
            assignments={"Alice": "community-a", "Bob": "community-a"},
            algorithm="leiden",
            algorithm_version="2.13.0",
            config={"random_seed": 19},
            node_count=2,
            relationship_count=1,
            reports={
                "community-a": {
                    "community_name": "Alice and Bob",
                    "report": "Alice and Bob are connected.",
                    "member_count": 2,
                }
            },
        )
        staged = await backend.get_dreaming_status()
        assert staged["active_run"]["phase"] == "publishing"
        assert staged["latest_snapshot"] is None

        await backend.publish_dreaming_snapshot(run["run_id"], "snapshot-a")
        published = await backend.get_dreaming_status()
        assert published["active_run"] is None
        assert published["latest_run"]["status"] == "succeeded"
        assert published["latest_snapshot"]["community_count"] == 1
        communities = await backend.list_communities()
        assert communities["items"][0]["community_name"] == "Alice and Bob"
        detail = await backend.get_community_memory_view("community-a")
        assert detail["report"] == "Alice and Bob are connected."
        assert len(detail["members"]) == 2
        assert detail["members"][0]["entity_id"] == "entity-alice"
        assert detail["members"][0]["resolved"] is True
        await backend.finalize()

    asyncio.run(scenario())


class _FakeSQLite:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def update_dreaming_run_phase(self, run_id: str, phase: str) -> None:
        self.events.append(phase)

    async def prepare_dreaming_snapshot(self, **kwargs) -> None:
        self.events.append("prepared")
        self.assignments = kwargs["assignments"]
        self.usage = kwargs.get("usage", {})

    async def publish_dreaming_snapshot(self, run_id: str, snapshot_id: str) -> None:
        self.events.append("published")

    async def fail_dreaming_run(self, run_id: str, error: str) -> None:
        self.events.append(f"failed:{error}")

    async def get_dreaming_status(self):
        return {"latest_run": {"status": "succeeded"}}

    async def get_latest_dreaming_memberships(self):
        return None


class _FakeGraph:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.published: dict[str, str] | None = None
        self.publications: list[dict[str, str]] = []

    async def compute_leiden_communities(self, **config):
        if self.fail:
            raise RuntimeError("gds unavailable")
        return {
            "assignments": {"Alice": 10, "Bob": 10, "Carol": 4},
            "node_count": 3,
            "relationship_count": 2,
            "algorithm_version": "2.13.0",
        }

    async def publish_dreaming_memberships(self, **kwargs) -> None:
        self.published = kwargs["assignments"]
        self.reports = kwargs.get("reports", {})
        self.publications.append(kwargs["assignments"])

    async def get_dreaming_community_contexts(self, assignments):
        community_id = assignments["Alice"]
        return {
            community_id: {
                "members": [
                    {"name": "Alice", "type": "person", "description": "A"},
                    {"name": "Bob", "type": "person", "description": "B"},
                ],
                "relationships": [
                    {
                        "source": "Alice",
                        "target": "Bob",
                        "keywords": ["knows"],
                        "description": "Alice knows Bob",
                    }
                ],
            }
        }


def test_runner_stages_then_publishes_non_singleton_communities() -> None:
    async def scenario() -> None:
        graph = _FakeGraph()
        sqlite = _FakeSQLite()
        result = await DreamingRunner(graph, sqlite).run("run-a", {"random_seed": 19})

        assert result["latest_run"]["status"] == "succeeded"
        assert sqlite.events == [
            "projecting",
            "reporting",
            "staging",
            "prepared",
            "published",
        ]
        assert graph.published == sqlite.assignments
        assert graph.published["Alice"] == graph.published["Bob"]
        assert "Carol" not in graph.published

    asyncio.run(scenario())


def test_runner_generates_reports_and_records_provider_token_usage() -> None:
    async def dream_llm(prompt, **kwargs):
        kwargs["token_tracker"].add_usage(
            {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
        )
        return '{"name":"Alice and Bob","report":"Alice knows Bob."}'

    async def scenario() -> None:
        graph = _FakeGraph()
        sqlite = _FakeSQLite()
        await DreamingRunner(graph, sqlite, llm_func=dream_llm).run(
            "run-a", {"random_seed": 19}
        )

        assert sqlite.events == [
            "projecting",
            "reporting",
            "staging",
            "prepared",
            "published",
        ]
        assert sqlite.usage == {
            "report_count": 1,
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "llm_call_count": 1,
            "token_usage_source": "provider",
        }
        report = next(iter(graph.reports.values()))
        assert report["community_name"] == "Alice and Bob"
        assert report["report"] == "Alice knows Bob."

    asyncio.run(scenario())


def test_runner_retries_and_normalizes_provider_report_shape() -> None:
    responses = iter(
        [
            '{"community_name":"Alice and Bob"}',
            '{"result":{"title":"Alice and Bob","summary":"Alice knows Bob."}}',
        ]
    )

    async def dream_llm(prompt, **kwargs):
        kwargs["token_tracker"].add_usage(
            {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        )
        return next(responses)

    async def scenario() -> None:
        graph = _FakeGraph()
        sqlite = _FakeSQLite()
        await DreamingRunner(graph, sqlite, llm_func=dream_llm).run(
            "run-a", {"random_seed": 19}
        )

        assert sqlite.usage["llm_call_count"] == 2
        assert sqlite.usage["total_tokens"] == 240
        report = next(iter(graph.reports.values()))
        assert report["community_name"] == "Alice and Bob"
        assert report["report"] == "Alice knows Bob."
        assert report["llm_call_count"] == 2

    asyncio.run(scenario())


def test_runner_does_not_publish_when_gds_computation_fails() -> None:
    async def scenario() -> None:
        graph = _FakeGraph(fail=True)
        sqlite = _FakeSQLite()
        with pytest.raises(RuntimeError, match="gds unavailable"):
            await DreamingRunner(graph, sqlite).run("run-a", {"random_seed": 19})

        assert graph.published is None
        assert sqlite.events == ["projecting", "failed:gds unavailable"]

    asyncio.run(scenario())


def test_runner_restores_previous_partition_when_sqlite_publish_fails() -> None:
    class FinalizationFailureSQLite(_FakeSQLite):
        async def get_latest_dreaming_memberships(self):
            return {
                "snapshot_id": "previous",
                "published_at": "earlier",
                "assignments": {"Alice": "old-community"},
            }

        async def publish_dreaming_snapshot(
            self, run_id: str, snapshot_id: str
        ) -> None:
            raise RuntimeError("sqlite finalization failed")

    async def scenario() -> None:
        graph = _FakeGraph()
        sqlite = FinalizationFailureSQLite()
        with pytest.raises(RuntimeError, match="sqlite finalization failed"):
            await DreamingRunner(graph, sqlite).run("run-a", {"random_seed": 19})

        assert len(graph.publications) == 2
        assert graph.publications[-1] == {"Alice": "old-community"}
        assert sqlite.events[-1] == "failed:sqlite finalization failed"

    asyncio.run(scenario())
