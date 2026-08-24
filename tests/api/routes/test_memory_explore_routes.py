import importlib
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

# API auth/config parses argv at import time; isolate it from pytest's paths.
_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
memory_routes = importlib.import_module("magi_core.api.routers.memory_routes")
sys.argv = _original_argv


class _Explorer:
    def __init__(self) -> None:
        self.expand_call = None
        self.evidence_call = None

    async def expand(self, **kwargs):
        self.expand_call = kwargs
        return {
            "status": "complete",
            "exhausted": True,
            "truncated": False,
            "missing_frontier": [],
            "available_count": 0,
            "returned_count": 0,
            "results": [],
        }

    async def evidence(self, owners):
        self.evidence_call = owners
        return {"status": "complete", "missing_owners": [], "owners": []}


def test_memory_explore_routes_forward_only_explicit_visit_and_name_owners(monkeypatch):
    explorer = _Explorer()
    monkeypatch.setattr(
        memory_routes,
        "MemoryExplorer",
        lambda _graph, _memory_db: explorer,
    )
    app = FastAPI()
    app.include_router(
        memory_routes.create_memory_routes(
            SimpleNamespace(chunk_entity_relation_graph=object()),
            SimpleNamespace(),
        )
    )
    client = TestClient(app)

    expand_response = client.post(
        "/memory/explore/expand",
        json={
            "frontier": ["Alice"],
            "visit": {
                "entities": ["Alice"],
                "relations": [["Bob", "Alice"]],
            },
            "max_candidates_per_frontier": 12,
        },
    )
    assert expand_response.status_code == 200
    assert expand_response.json()["exhausted"] is True
    assert explorer.expand_call == {
        "frontier": ["Alice"],
        "visit_entities": ["Alice"],
        "visit_relations": [("Bob", "Alice")],
        "max_candidates_per_frontier": 12,
    }

    evidence_response = client.post(
        "/memory/explore/evidence",
        json={
            "owners": [
                {"entity": "Alice"},
                {"relation": ["Bob", "Alice"]},
            ]
        },
    )
    assert evidence_response.status_code == 200
    assert explorer.evidence_call == [
        {"entity": "Alice"},
        {"relation": ("Bob", "Alice")},
    ]


def test_memory_explore_request_rejects_ambiguous_owner_and_extra_state(monkeypatch):
    explorer = _Explorer()
    monkeypatch.setattr(
        memory_routes,
        "MemoryExplorer",
        lambda _graph, _memory_db: explorer,
    )
    app = FastAPI()
    app.include_router(
        memory_routes.create_memory_routes(
            SimpleNamespace(chunk_entity_relation_graph=object()),
            SimpleNamespace(),
        )
    )
    client = TestClient(app)

    ambiguous = client.post(
        "/memory/explore/evidence",
        json={"owners": [{"entity": "Alice", "relation": ["Alice", "Bob"]}]},
    )
    assert ambiguous.status_code == 422

    extra_state = client.post(
        "/memory/explore/expand",
        json={
            "frontier": ["Alice"],
            "visit": {"entities": [], "relations": [], "seen": ["Bob"]},
        },
    )
    assert extra_state.status_code == 422
