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
        self.describe_call = None
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

    async def describe(self, owners):
        self.describe_call = owners
        return {"status": "complete", "missing_owners": [], "owners": []}


class _TraceDB:
    def __init__(self) -> None:
        self.events = []
        self.trace = None

    async def append_exploration_event(self, **event):
        persisted = {"seq": len(self.events) + 1, **event}
        self.events.append(persisted)
        return persisted

    async def register_exploration_trace(self, exploration_id, query):
        self.trace = {"exploration_id": exploration_id, "query": query}
        return self.trace

    async def list_exploration_traces(self, **_kwargs):
        return [] if self.trace is None else [{**self.trace, "last_seq": len(self.events)}]

    async def get_exploration_events(self, exploration_id, *, after_seq, limit):
        assert exploration_id == "explore-1"
        return [event for event in self.events if event["seq"] > after_seq][:limit]

    async def archive_exploration_trace(self, exploration_id):
        return exploration_id == "explore-1"


class _Graph:
    async def get_nodes_batch(self, node_ids):
        return {
            node_id: {
                "entity_id": node_id,
                "entity_type": "person",
                "description": f"Description for {node_id}",
            }
            for node_id in node_ids
        }


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

    describe_response = client.post(
        "/memory/explore/describe",
        json={
            "owners": [
                {"entity": "Alice"},
                {"relation": ["Bob", "Alice"]},
            ]
        },
    )
    assert describe_response.status_code == 200
    assert explorer.describe_call == [
        {"entity": "Alice"},
        {"relation": ("Bob", "Alice")},
    ]

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


def test_traced_expand_persists_started_before_completed_and_supports_replay(
    monkeypatch,
):
    explorer = _Explorer()
    memory_db = _TraceDB()
    monkeypatch.setattr(
        memory_routes,
        "MemoryExplorer",
        lambda _graph, _memory_db: explorer,
    )
    app = FastAPI()
    app.include_router(
        memory_routes.create_memory_routes(
            SimpleNamespace(chunk_entity_relation_graph=_Graph()),
            memory_db,
        )
    )
    client = TestClient(app)

    registration = client.post(
        "/memory/explore/traces",
        json={
            "exploration_id": "explore-1",
            "query": "Who is Alice?",
            "initial_visit": {
                "entities": ["Alice"],
                "relations": [],
            },
        },
    )
    assert registration.status_code == 200

    response = client.post(
        "/memory/explore/expand",
        json={
            "frontier": ["Alice"],
            "visit": {"entities": ["Alice"], "relations": []},
            "trace": {
                "exploration_id": "explore-1",
                "agent_id": "s0",
                "call_id": "call-1",
                "subquery": "How does Alice connect to the project?",
            },
        },
    )
    assert response.status_code == 200
    assert [event["event_type"] for event in memory_db.events] == [
        "exploration_initialized",
        "expand_started",
        "expand_completed",
    ]
    assert memory_db.events[0]["payload"] == {
        "visit": {
            "entities": ["Alice"],
            "relations": [],
            "entity_metadata": [
                {
                    "name": "Alice",
                    "entity_type": "person",
                    "description": "Description for Alice",
                }
            ],
        },
    }
    assert memory_db.events[1]["payload"]["frontier"] == ["Alice"]
    assert memory_db.events[1]["payload"]["subquery"] == (
        "How does Alice connect to the project?"
    )
    assert memory_db.events[2]["payload"]["result"]["status"] == "complete"

    replay = client.get(
        "/memory/explore/traces/explore-1/events", params={"after_seq": 2}
    )
    assert replay.status_code == 200
    assert replay.json()["last_seq"] == 3
    assert len(replay.json()["events"]) == 1
