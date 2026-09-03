"""Visibility rules for Dreaming metadata on the shared graph endpoint."""

import importlib
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magi_core.types import KnowledgeGraph, KnowledgeGraphNode


_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
_graph_routes = importlib.import_module("magi_core.api.routers.graph_routes")
sys.argv = _original_argv

pytestmark = pytest.mark.offline


class _FakeRag:
    async def get_knowledge_graph(self, **_kwargs) -> KnowledgeGraph:
        return KnowledgeGraph(
            nodes=[
                KnowledgeGraphNode(
                    id="alice",
                    labels=["PERSON"],
                    properties={
                        "entity_id": "Alice",
                        "dream_community_id": "community-1",
                        "dream_community_name": "Alice's collaborators",
                        "dream_membership_status": "stable",
                        "dream_snapshot_id": "snapshot-1",
                        "community_status": "stable",
                        "communityId": "legacy-id",
                        "membership_status": "stable",
                    },
                )
            ]
        )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(_graph_routes.create_graph_routes(_FakeRag()))
    return TestClient(app)


def test_shared_graph_exposes_only_public_community_fields() -> None:
    response = _client().get("/graphs", params={"label": "*"})

    assert response.status_code == 200
    properties = response.json()["nodes"][0]["properties"]
    assert properties == {
        "entity_id": "Alice",
        "community_id": "community-1",
        "community_name": "Alice's collaborators",
    }


def test_balthasar_can_request_internal_dreaming_fields() -> None:
    response = _client().get(
        "/graphs",
        params={"label": "*", "include_dreaming_internal": "true"},
    )

    assert response.status_code == 200
    properties = response.json()["nodes"][0]["properties"]
    assert properties["dream_community_id"] == "community-1"
    assert properties["dream_membership_status"] == "stable"
    assert "community_id" not in properties
