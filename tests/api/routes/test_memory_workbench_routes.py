from __future__ import annotations

import asyncio
import importlib
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import AtomEvidence, AtomRecord, EntityRecord, Episode


_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
memory_routes = importlib.import_module("magi_core.api.routers.memory_routes")
sys.argv = _original_argv


def test_entity_inspection_and_atom_conflict_resolution_routes(tmp_path) -> None:
    async def seed() -> SQLiteBackend:
        backend = SQLiteBackend(tmp_path / "memory.db", "workspace-a")
        await backend.initialize()
        episode = Episode(id="episode-ui", content="Conflicting claims")
        await backend.put_episode(episode)
        first_entity = EntityRecord(
            id="entity-first",
            workspace_id="workspace-a",
            canonical_name="Alex One",
            aliases=("Alex",),
        )
        second_entity = EntityRecord(
            id="entity-second",
            workspace_id="workspace-a",
            canonical_name="Alex Two",
            aliases=("Alex",),
        )
        await backend.put_entity(first_entity)
        await backend.put_entity(second_entity)
        first_atom = AtomRecord(
            id="atom-first",
            workspace_id="workspace-a",
            owner_id=first_entity.id,
            content="Alex prefers tea.",
            valid_at=episode.effective_reference_at,
        )
        second_atom = AtomRecord(
            id="atom-second",
            workspace_id="workspace-a",
            owner_id=first_entity.id,
            content="Alex dislikes tea.",
            valid_at=episode.effective_reference_at,
        )
        await backend.put_atom(
            first_atom,
            AtomEvidence(atom_id=first_atom.id, episode_id=episode.id),
        )
        await backend.put_atom(
            second_atom,
            AtomEvidence(atom_id=second_atom.id, episode_id=episode.id),
        )
        await backend.add_atom_evolution(
            second_atom.id,
            first_atom.id,
            "CONTRADICTION",
        )
        return backend

    backend = asyncio.run(seed())
    app = FastAPI()
    app.include_router(
        memory_routes.create_memory_routes(
            SimpleNamespace(chunk_entity_relation_graph=object()),
            backend,
        )
    )
    client = TestClient(app)

    entities = client.get("/memory/entities", params={"q": "Alex"})
    assert entities.status_code == 200
    assert entities.json()["total"] == 2
    detail = client.get("/memory/entities/entity-first")
    assert detail.status_code == 200
    assert detail.json()["alias_ambiguities"][0]["alias"] == "Alex"
    alias_resolution = client.post(
        "/memory/entities/aliases/resolve",
        json={"alias": "Alex", "winner_entity_id": "entity-first"},
    )
    assert alias_resolution.status_code == 200
    assert alias_resolution.json()["alias_ambiguities"] == []

    atom = client.get("/memory/atoms/atom-second")
    assert atom.status_code == 200
    assert atom.json()["evolutions"][0]["resolved"] is False
    evolved = client.get(
        "/memory/atoms", params={"evolution_type": "any"}
    )
    assert evolved.status_code == 200
    assert evolved.json()["total"] == 2
    assert evolved.json()["items"][0]["evolution_count"] == 1
    resolution = client.post(
        "/memory/atoms/conflicts/resolve",
        json={
            "source_atom_id": "atom-second",
            "target_atom_id": "atom-first",
            "winner_atom_id": "atom-second",
            "note": "Manual review",
        },
    )
    assert resolution.status_code == 200
    assert resolution.json()["retired_atom"]["temporal_status"] == "expired"
    refreshed = client.get("/memory/atoms/atom-second").json()
    assert refreshed["evolutions"][0]["metadata"]["resolution_note"] == (
        "Manual review"
    )
    asyncio.run(backend.finalize())


def test_conflict_resolution_rejects_winner_outside_pair() -> None:
    app = FastAPI()
    app.include_router(
        memory_routes.create_memory_routes(
            SimpleNamespace(chunk_entity_relation_graph=object()),
            SimpleNamespace(),
        )
    )
    response = TestClient(app).post(
        "/memory/atoms/conflicts/resolve",
        json={
            "source_atom_id": "atom-a",
            "target_atom_id": "atom-b",
            "winner_atom_id": "atom-c",
        },
    )
    assert response.status_code == 422
