"""Runtime LLM role inspection, model discovery, and hot updates."""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Literal, Optional
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_core.api.utils_api import get_combined_auth_dependency
from magi_core.utils import logger


LLMBinding = Literal[
    "lollms",
    "ollama",
    "openai",
    "openai-ollama",
    "azure_openai",
    "bedrock",
    "gemini",
]
SUPPORTED_LLM_BINDINGS = [
    "ollama",
    "openai",
    "openai-ollama",
    "azure_openai",
    "gemini",
    "bedrock",
    "lollms",
]


class RuntimeRoleUpdate(BaseModel):
    """A session-scoped role update; omitted fields retain their current value."""

    model_config = ConfigDict(extra="forbid")

    binding: LLMBinding | None = None
    model: str | None = Field(default=None, max_length=500)
    host: str | None = Field(default=None, max_length=2_000)
    api_key: str | None = Field(default=None, max_length=10_000)
    max_async: int | None = Field(default=None, ge=1, le=1_024)
    timeout: int | None = Field(default=None, ge=1, le=86_400)

    @field_validator("model", "host", "api_key")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @model_validator(mode="after")
    def require_update(self) -> "RuntimeRoleUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one role setting is required")
        return self


class RuntimeModelDiscoveryRequest(BaseModel):
    """Connection override used only for one model-list request."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=100)
    binding: LLMBinding | None = None
    host: str | None = Field(default=None, max_length=2_000)
    api_key: str | None = Field(default=None, max_length=10_000)

    @field_validator("role", "host", "api_key")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


def _models_url(binding: str, host: str | None) -> str | None:
    if binding == "gemini":
        if not host or host == "DEFAULT_GEMINI_ENDPOINT":
            return "https://generativelanguage.googleapis.com/v1beta/models"
        return urljoin(host.rstrip("/") + "/", "v1beta/models")
    if not host:
        return None
    if binding == "ollama":
        return urljoin(host.rstrip("/") + "/", "api/tags")
    if binding in {"openai", "openai-ollama", "lollms"}:
        return urljoin(host.rstrip("/") + "/", "models")
    return None


async def discover_provider_models(metadata: dict[str, Any]) -> dict[str, Any]:
    """List models for providers with a stable HTTP discovery surface."""

    binding = str(metadata.get("binding") or "")
    host = metadata.get("host")
    url = _models_url(binding, str(host) if host else None)
    if url is None:
        return {
            "supported": False,
            "models": [],
            "message": f"{binding or 'This provider'} does not expose a supported model-list endpoint",
        }

    headers: dict[str, str] = {"Accept": "application/json"}
    api_key = metadata.get("api_key")
    if api_key:
        if binding == "gemini":
            headers["x-goog-api-key"] = str(api_key)
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Runtime model discovery failed for %s: %s", binding, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Unable to list models from the configured {binding} endpoint",
        ) from exc

    if binding == "ollama":
        entries = payload.get("models", []) if isinstance(payload, dict) else []
        models = [entry.get("name") or entry.get("model") for entry in entries]
    elif binding == "gemini":
        entries = payload.get("models", []) if isinstance(payload, dict) else []
        models = [str(entry.get("name", "")).removeprefix("models/") for entry in entries]
    else:
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        models = [entry.get("id") for entry in entries]

    normalized = sorted({str(model) for model in models if model})
    return {"supported": True, "models": normalized, "message": None}


def create_runtime_llm_routes(
    rag: Any,
    api_key: Optional[str] = None,
    *,
    model_discoverer: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    | None = None,
) -> APIRouter:
    """Create authenticated, single-process runtime model-control routes."""

    router = APIRouter(prefix="/runtime/llm", tags=["runtime"])
    combined_auth = get_combined_auth_dependency(api_key)
    protected = [Depends(combined_auth)]
    discover = model_discoverer or discover_provider_models

    @router.get("", dependencies=protected)
    async def get_runtime_llm_roles():
        return {
            "scope": "runtime",
            "restart_required": False,
            "bindings": SUPPORTED_LLM_BINDINGS,
            "roles": rag.get_llm_role_config(),
        }

    @router.patch("/{role}", dependencies=protected)
    async def update_runtime_llm_role(role: str, request: RuntimeRoleUpdate):
        if os.environ.get("LIGHTRAG_GUNICORN_MODE"):
            raise HTTPException(
                status_code=409,
                detail="Runtime LLM switching is not yet coordinated across Gunicorn workers",
            )
        try:
            rag.update_llm_role_config(role, **request.model_dump(exclude_unset=True))
            config = rag.get_llm_role_config(role)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (TypeError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"role": role.strip().lower(), "config": config, "scope": "runtime"}

    @router.post("/models", dependencies=protected)
    async def list_runtime_models(request: RuntimeModelDiscoveryRequest):
        try:
            # This private snapshot is intentionally consumed only inside the
            # trusted server. The response below never returns credentials.
            metadata = rag._get_llm_role_runtime_metadata(request.role)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if request.binding is not None:
            metadata["binding"] = request.binding
        if request.host is not None:
            metadata["host"] = request.host
        if request.api_key is not None:
            metadata["api_key"] = request.api_key
        result = await discover(metadata)
        return {
            "role": request.role.strip().lower(),
            "binding": metadata.get("binding"),
            **result,
        }

    return router
