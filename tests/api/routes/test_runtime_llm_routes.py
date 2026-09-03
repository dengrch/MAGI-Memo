from __future__ import annotations

from copy import deepcopy
import importlib
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
runtime_llm_routes = importlib.import_module(
    "magi_core.api.routers.runtime_llm_routes"
)
sys.argv = _original_argv


class _RuntimeRag:
    def __init__(self) -> None:
        self.metadata = {
            "query": {
                "binding": "openai",
                "model": "model-a",
                "host": "https://models.example/v1",
                "api_key": "server-secret",
            }
        }
        self.updated: tuple[str, dict] | None = None

    def get_llm_role_config(self, role: str | None = None):
        def public(name: str):
            metadata = {
                key: value
                for key, value in self.metadata[name].items()
                if key != "api_key"
            }
            return {
                "binding": metadata["binding"],
                "model": metadata["model"],
                "host": metadata["host"],
                "max_async": 4,
                "timeout": 60,
                "metadata": metadata,
            }

        if role is not None:
            if role not in self.metadata:
                raise ValueError(f"Invalid LLM role: {role}")
            return public(role)
        return {name: public(name) for name in self.metadata}

    def _get_llm_role_runtime_metadata(self, role: str):
        if role not in self.metadata:
            raise ValueError(f"Invalid LLM role: {role}")
        return deepcopy(self.metadata[role])

    def update_llm_role_config(self, role: str, **updates):
        if role not in self.metadata:
            raise ValueError(f"Invalid LLM role: {role}")
        self.updated = (role, updates)
        self.metadata[role].update(updates)


def _client(rag: _RuntimeRag, discoverer=None) -> TestClient:
    app = FastAPI()
    app.include_router(
        runtime_llm_routes.create_runtime_llm_routes(
            rag, model_discoverer=discoverer
        )
    )
    return TestClient(app)


def test_runtime_llm_routes_expose_sanitized_role_snapshot():
    client = _client(_RuntimeRag())

    response = client.get("/runtime/llm")

    assert response.status_code == 200
    body = response.json()
    assert body["restart_required"] is False
    assert body["roles"]["query"]["model"] == "model-a"
    assert "api_key" not in str(body)


def test_runtime_llm_role_update_is_hot_and_returns_sanitized_config(monkeypatch):
    monkeypatch.delenv("LIGHTRAG_GUNICORN_MODE", raising=False)
    rag = _RuntimeRag()
    client = _client(rag)

    response = client.patch(
        "/runtime/llm/query",
        json={"model": "model-b", "max_async": 8, "api_key": "new-secret"},
    )

    assert response.status_code == 200
    assert rag.updated == (
        "query",
        {"model": "model-b", "api_key": "new-secret", "max_async": 8},
    )
    assert response.json()["config"]["model"] == "model-b"
    assert "secret" not in str(response.json())


def test_runtime_llm_model_discovery_uses_server_secret_without_returning_it():
    captured = {}

    async def discover(metadata):
        captured.update(metadata)
        return {"supported": True, "models": ["model-a", "model-b"], "message": None}

    client = _client(_RuntimeRag(), discoverer=discover)
    response = client.post("/runtime/llm/models", json={"role": "query"})

    assert response.status_code == 200
    assert captured["api_key"] == "server-secret"
    assert response.json()["models"] == ["model-a", "model-b"]
    assert "server-secret" not in str(response.json())


def test_runtime_llm_hot_update_refuses_uncoordinated_multiworker_mode(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_GUNICORN_MODE", "1")
    client = _client(_RuntimeRag())

    response = client.patch("/runtime/llm/query", json={"model": "model-b"})

    assert response.status_code == 409
    assert "Gunicorn" in response.json()["detail"]
