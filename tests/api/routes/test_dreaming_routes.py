from __future__ import annotations

import importlib
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
dreaming_routes = importlib.import_module("magi_core.api.routers.dreaming_routes")
sys.argv = _original_argv


class _Graph:
    async def compute_leiden_communities(self, **config):
        return {}

    async def publish_dreaming_memberships(self, **kwargs):
        return None

    async def get_dreaming_community_reports(self, snapshot_id=None):
        return [
            {
                "community_id": "community-a",
                "community_name": "Test community",
                "report": "A report",
            }
        ]


class _Rag:
    chunk_entity_relation_graph = _Graph()


class _MemoryDB:
    def __init__(self) -> None:
        self.started = False

    async def get_dreaming_status(self):
        return {
            "workspace_id": "workspace-a",
            "active_run": None,
            "latest_run": None,
            "latest_snapshot": None,
        }

    async def start_dreaming_run(self, config):
        self.started = True
        return {
            "run_id": "run-a",
            "workspace_id": "workspace-a",
            "status": "running",
            "phase": "queued",
            "config": config,
            "created_at": "now",
            "started_at": "now",
        }


class _Runner:
    def __init__(self, graph, sqlite) -> None:
        self.sqlite = sqlite

    @staticmethod
    def ensure_supported(graph) -> None:
        return None

    async def run(self, run_id, config):
        return None


def _client(memory_db: _MemoryDB) -> TestClient:
    app = FastAPI()
    app.state.background_tasks = set()
    app.include_router(
        dreaming_routes.create_dreaming_routes(
            _Rag(), memory_db, runner_factory=_Runner
        )
    )
    return TestClient(app)


def test_dreaming_status_is_workspace_scoped() -> None:
    response = _client(_MemoryDB()).get("/dreaming/status")

    assert response.status_code == 200
    assert response.json()["workspace_id"] == "workspace-a"


def test_manual_dreaming_returns_accepted_run() -> None:
    memory_db = _MemoryDB()
    response = _client(memory_db).post("/dreaming/runs", json={})

    assert response.status_code == 202
    assert response.json()["phase"] == "queued"
    assert memory_db.started is True


def test_dreaming_community_reports_come_from_graph_storage() -> None:
    response = _client(_MemoryDB()).get("/dreaming/communities")

    assert response.status_code == 200
    assert response.json()["items"][0]["community_name"] == "Test community"
