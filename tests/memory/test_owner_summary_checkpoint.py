from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

import magi_core.memory.adapter as adapter_module
from magi_core.backend.sqlite import SQLiteBackend
from magi_core.memory import AtomEvidence, AtomRecord, EntityRecord, Episode
from magi_core.memory.adapter import MagiKnowledgeAdapter
from magi_core.utils import Tokenizer, TokenizerInterface


class _CharacterTokenizer(TokenizerInterface):
    def encode(self, content: str):
        return [ord(character) for character in content]

    def decode(self, tokens):
        return "".join(chr(token) for token in tokens)


class _SummaryRole:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.fail_next = False
        self.drop_lineage_next = False
        self.delay = 0.0

    async def __call__(self, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated summary failure")
        if self.drop_lineage_next:
            self.drop_lineage_next = False
            return "Summary without Atom lineage"
        tags = list(dict.fromkeys(re.findall(r"\[atom-[A-Za-z0-9_-]+\]", prompt)))
        return "Compacted owner summary " + " ".join(tags)


class _Rag:
    def __init__(self, role: _SummaryRole) -> None:
        self.role = role
        self.llm_response_cache = None
        self.model = "summary-model-a"
        self.delta_atoms = 2
        self.rebuild_interval = 100
        self.summary_context_size = 100_000

    def _build_global_config(self) -> dict[str, Any]:
        return {
            "role_llm_funcs": {"extract": self.role},
            "llm_cache_identities": {
                "extract": {"binding": "test", "model": self.model}
            },
            "llm_model_name": self.model,
            "addon_params": {},
            "_resolved_summary_language": "English",
            "summary_length_recommended": 1000,
            "summary_context_size": self.summary_context_size,
            "summary_max_tokens": 100_000,
            "force_llm_summary_on_merge": 3,
            "tokenizer": Tokenizer("characters", _CharacterTokenizer()),
            "magi_summary_delta_atom_threshold": self.delta_atoms,
            "magi_summary_delta_token_threshold": 100_000,
            "magi_summary_description_token_budget": 100_000,
            "magi_summary_full_rebuild_interval": self.rebuild_interval,
        }


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}

    async def get_node(self, name: str) -> dict[str, Any] | None:
        return self.nodes.get(name)

    async def upsert_node(self, name: str, node_data: dict[str, Any]) -> None:
        self.nodes[name] = dict(node_data)


async def _setup(tmp_path: Path) -> tuple[SQLiteBackend, MagiKnowledgeAdapter, EntityRecord]:
    backend = SQLiteBackend(tmp_path / "memory.sqlite3", "workspace-a")
    await backend.initialize()
    entity = await backend.put_entity(
        EntityRecord(
            id="entity-alice",
            workspace_id="workspace-a",
            canonical_name="Alice",
        )
    )
    return backend, MagiKnowledgeAdapter(backend), entity


async def _add_atom(
    backend: SQLiteBackend,
    entity: EntityRecord,
    index: int,
) -> AtomRecord:
    episode = Episode(
        id=f"episode-summary-{index}",
        content=f"Owner fact number {index}.",
    )
    await backend.put_episode(episode)
    atom = AtomRecord(
        id=f"atom-summary-{index}",
        workspace_id="workspace-a",
        owner_id=entity.id,
        content=f"Owner fact number {index}.",
        valid_at=None,
    )
    await backend.put_atom(
        atom,
        AtomEvidence(
            atom_id=atom.id,
            episode_id=episode.id,
            extraction_revision=f"chunk-{index}",
        ),
    )
    return atom


async def _describe(
    adapter: MagiKnowledgeAdapter,
    rag: _Rag,
    owner_id: str,
) -> str:
    description, _ = await adapter._owner_description(
        rag,
        owner_id=owner_id,
        description_type="Entity",
        description_name="Alice",
    )
    return description


@pytest.mark.asyncio
async def test_checkpoint_reuses_evidence_only_updates_and_compacts_pending_delta(
    tmp_path: Path,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    first = await _add_atom(backend, entity, 1)

    initial = await _describe(adapter, rag, entity.id)
    assert first.id in initial
    assert role.prompts == []
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [first.id]
    assert checkpoint["pending_atom_ids"] == []

    supporting_episode = Episode(
        id="episode-summary-support",
        content="The first fact was observed again.",
    )
    await backend.put_episode(supporting_episode)
    await backend.add_atom_evidence(
        first.id,
        AtomEvidence(
            atom_id=first.id,
            episode_id=supporting_episode.id,
            extraction_revision="chunk-support",
        ),
    )
    reused = await _describe(adapter, rag, entity.id)
    assert reused == initial
    assert role.prompts == []

    second = await _add_atom(backend, entity, 2)
    pending = await _describe(adapter, rag, entity.id)
    assert first.id in pending and second.id in pending
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [first.id]
    assert checkpoint["pending_atom_ids"] == [second.id]
    assert role.prompts == []

    third = await _add_atom(backend, entity, 3)
    compacted = await _describe(adapter, rag, entity.id)
    assert len(role.prompts) == 1
    assert all(atom_id in compacted for atom_id in (first.id, second.id, third.id))
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [first.id, second.id, third.id]
    assert checkpoint["pending_atom_ids"] == []
    assert checkpoint["incremental_compaction_count"] == 1
    assert checkpoint["metrics"]["delta_compaction_count"] == 1
    assert checkpoint["metrics"]["delta_llm_call_count"] == 1
    assert checkpoint["metrics"]["last_input_tokens"] > 0
    assert checkpoint["metrics"]["last_lineage_valid"] is True
    await backend.finalize()


@pytest.mark.asyncio
async def test_pending_delta_is_immediately_visible_in_graph_projection(
    tmp_path: Path,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    rag.chunk_entity_relation_graph = _Graph()
    rag.entities_vdb = None
    first = await _add_atom(backend, entity, 1)
    await adapter._project_entity_record(rag, entity)
    second = await _add_atom(backend, entity, 2)
    await adapter._project_entity_record(rag, entity)

    node = rag.chunk_entity_relation_graph.nodes["Alice"]
    assert first.id in node["description"]
    assert second.id in node["description"]
    assert not any(key.startswith("magi_summary_") for key in node)
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint is not None
    assert len(checkpoint["covered_atom_ids"]) == 1
    assert len(checkpoint["pending_atom_ids"]) == 1
    assert checkpoint["metrics"]["rebuild_compaction_count"] == 1
    assert checkpoint["metrics"]["total_input_tokens"] > 0
    assert role.prompts == []
    await backend.finalize()


@pytest.mark.asyncio
async def test_failed_delta_stays_pending_then_retries_and_removal_rebuilds(
    tmp_path: Path,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    first = await _add_atom(backend, entity, 1)
    await _describe(adapter, rag, entity.id)
    second = await _add_atom(backend, entity, 2)
    third = await _add_atom(backend, entity, 3)

    role.fail_next = True
    degraded = await _describe(adapter, rag, entity.id)
    assert all(atom_id in degraded for atom_id in (first.id, second.id, third.id))
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [first.id]
    assert checkpoint["pending_atom_ids"] == [second.id, third.id]

    recovered = await _describe(adapter, rag, entity.id)
    assert all(atom_id in recovered for atom_id in (first.id, second.id, third.id))
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["pending_atom_ids"] == []

    fourth = await _add_atom(backend, entity, 4)
    await backend.expire_atom(first.id, expired_at=first.created_at)
    role.fail_next = True
    degraded_rebuild = await _describe(adapter, rag, entity.id)
    assert first.id not in degraded_rebuild
    assert all(atom_id in degraded_rebuild for atom_id in (second.id, third.id, fourth.id))
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert first.id in checkpoint["covered_atom_ids"]

    rebuilt = await _describe(adapter, rag, entity.id)
    assert first.id not in rebuilt
    assert all(atom_id in rebuilt for atom_id in (second.id, third.id, fourth.id))
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [second.id, third.id, fourth.id]
    assert checkpoint["last_reason"] == "rebuild:covered_atom_removed"

    rag.model = "summary-model-b"
    role.fail_next = True
    identity_failure = await _describe(adapter, rag, entity.id)
    assert all(
        atom_id in identity_failure for atom_id in (second.id, third.id, fourth.id)
    )
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert "summary-model-a" in checkpoint["model_identity"]

    await _describe(adapter, rag, entity.id)
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert "summary-model-b" in checkpoint["model_identity"]
    assert checkpoint["last_reason"] == "rebuild:summary_identity_changed"
    await backend.finalize()


@pytest.mark.asyncio
async def test_concurrent_checkpoint_compaction_uses_revision_cas_without_lost_delta(
    tmp_path: Path,
) -> None:
    backend, adapter_a, entity = await _setup(tmp_path)
    adapter_b = MagiKnowledgeAdapter(backend)
    role = _SummaryRole()
    role.delay = 0.02
    rag = _Rag(role)
    atoms = [await _add_atom(backend, entity, 1)]
    await _describe(adapter_a, rag, entity.id)
    atoms.extend(
        [await _add_atom(backend, entity, index) for index in range(2, 4)]
    )

    results = await asyncio.gather(
        _describe(adapter_a, rag, entity.id),
        _describe(adapter_b, rag, entity.id),
    )
    for description in results:
        assert all(atom.id in description for atom in atoms)
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [atom.id for atom in atoms]
    assert checkpoint["pending_atom_ids"] == []
    assert checkpoint["summary_revision"] >= 2
    await backend.finalize()


@pytest.mark.asyncio
async def test_projection_does_not_label_stale_text_with_concurrent_new_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    rag.chunk_entity_relation_graph = _Graph()
    rag.entities_vdb = None
    await _add_atom(backend, entity, 1)
    original_compare = backend.compare_and_set_owner_summary_checkpoint
    advanced = False

    async def racing_compare(owner_id: str, **kwargs: Any) -> bool:
        nonlocal advanced
        updated = await original_compare(owner_id, **kwargs)
        if updated and kwargs["expected_revision"] is None and not advanced:
            advanced = True
            checkpoint = await backend.get_owner_summary_checkpoint(owner_id)
            assert checkpoint is not None
            concurrently_updated = await original_compare(
                owner_id,
                expected_revision=checkpoint["summary_revision"],
                checkpoint_summary="newer concurrent checkpoint",
                covered_atom_ids=checkpoint["covered_atom_ids"],
                pending_atom_ids=checkpoint["pending_atom_ids"],
                prompt_version=checkpoint["prompt_version"],
                model_identity=checkpoint["model_identity"],
                incremental_compaction_count=checkpoint[
                    "incremental_compaction_count"
                ],
                last_compacted_at=checkpoint["last_compacted_at"],
                reason="concurrent_writer",
            )
            assert concurrently_updated
        return updated

    monkeypatch.setattr(
        backend,
        "compare_and_set_owner_summary_checkpoint",
        racing_compare,
    )
    rag.chunk_entity_relation_graph.nodes["Alice"] = {
        "description": "newer concurrent checkpoint",
    }

    await adapter._project_entity_record(rag, entity)

    node = rag.chunk_entity_relation_graph.nodes["Alice"]
    assert node["description"] == "newer concurrent checkpoint"
    assert not any(key.startswith("magi_summary_") for key in node)
    await backend.finalize()


@pytest.mark.asyncio
async def test_delta_retry_uses_stable_revision_and_pending_cache_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    await _add_atom(backend, entity, 1)
    await _describe(adapter, rag, entity.id)

    cache_identities: list[dict[str, Any]] = []
    original_summary = adapter_module._handle_entity_relation_summary

    async def capturing_summary(*args: Any, **kwargs: Any):
        cache_identities.append(dict(kwargs["summary_cache_identity"]))
        return await original_summary(*args, **kwargs)

    monkeypatch.setattr(
        adapter_module,
        "_handle_entity_relation_summary",
        capturing_summary,
    )
    await _add_atom(backend, entity, 2)
    await _add_atom(backend, entity, 3)
    role.fail_next = True
    await _describe(adapter, rag, entity.id)
    await _describe(adapter, rag, entity.id)

    assert cache_identities[0] == cache_identities[1]
    assert cache_identities[0]["mode"] == "delta"
    assert cache_identities[0]["summary_schema"] == (
        "magi-owner-summary-checkpoint-v1"
    )
    assert cache_identities[0]["pending_atom_ids"] == [
        "atom-summary-2",
        "atom-summary-3",
    ]
    assert cache_identities[0]["previous_revision"] == 2
    assert len(cache_identities[0]["previous_checkpoint_hash"]) == 64

    await _add_atom(backend, entity, 4)
    await _add_atom(backend, entity, 5)
    await _describe(adapter, rag, entity.id)
    assert cache_identities[2] != cache_identities[1]
    assert cache_identities[2]["pending_atom_ids"] == [
        "atom-summary-4",
        "atom-summary-5",
    ]
    assert cache_identities[2]["previous_revision"] > (
        cache_identities[1]["previous_revision"]
    )
    await backend.finalize()


@pytest.mark.asyncio
async def test_lineage_failure_keeps_delta_pending_until_valid_retry(
    tmp_path: Path,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    first = await _add_atom(backend, entity, 1)
    await _describe(adapter, rag, entity.id)
    second = await _add_atom(backend, entity, 2)
    third = await _add_atom(backend, entity, 3)

    role.drop_lineage_next = True
    visible = await _describe(adapter, rag, entity.id)
    assert all(atom.id in visible for atom in (first, second, third))
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [first.id]
    assert checkpoint["pending_atom_ids"] == [second.id, third.id]
    assert checkpoint["last_reason"] == "lineage_validation_failed:delta"
    assert checkpoint["metrics"]["last_lineage_valid"] is False

    compacted = await _describe(adapter, rag, entity.id)
    assert all(atom.id in compacted for atom in (first, second, third))
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [first.id, second.id, third.id]
    assert checkpoint["pending_atom_ids"] == []
    assert checkpoint["metrics"]["last_lineage_valid"] is True
    await backend.finalize()


@pytest.mark.asyncio
async def test_repeated_delta_compaction_does_not_resend_old_atom_prose(
    tmp_path: Path,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    first = await _add_atom(backend, entity, 1)
    await _describe(adapter, rag, entity.id)

    for start in (2, 4, 6):
        await _add_atom(backend, entity, start)
        await _add_atom(backend, entity, start + 1)
        await _describe(adapter, rag, entity.id)

    assert len(role.prompts) == 3
    assert first.content in role.prompts[0]
    assert first.content not in role.prompts[1]
    assert first.content not in role.prompts[2]
    for prompt_index, start in enumerate((2, 4, 6)):
        assert f"Owner fact number {start}." in role.prompts[prompt_index]
        assert f"Owner fact number {start + 1}." in role.prompts[prompt_index]
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert len(checkpoint["covered_atom_ids"]) == 7
    assert checkpoint["pending_atom_ids"] == []
    assert checkpoint["metrics"]["delta_compaction_count"] == 3
    assert checkpoint["metrics"]["delta_llm_call_count"] == 3
    assert checkpoint["metrics"]["total_saved_tokens"] > 0
    await backend.finalize()


@pytest.mark.asyncio
async def test_large_delta_map_reduce_covers_every_atom_without_truncation(
    tmp_path: Path,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    rag.delta_atoms = 5
    rag.summary_context_size = 180
    atoms = [await _add_atom(backend, entity, 1)]
    await _describe(adapter, rag, entity.id)
    atoms.extend(
        [await _add_atom(backend, entity, index) for index in range(2, 8)]
    )

    description = await _describe(adapter, rag, entity.id)
    assert all(atom.id in description for atom in atoms)
    assert len(role.prompts) > 1
    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [atom.id for atom in atoms]
    assert checkpoint["pending_atom_ids"] == []
    await backend.finalize()


@pytest.mark.asyncio
async def test_periodic_threshold_forces_full_rebuild_and_resets_counter(
    tmp_path: Path,
) -> None:
    backend, adapter, entity = await _setup(tmp_path)
    role = _SummaryRole()
    rag = _Rag(role)
    rag.rebuild_interval = 2
    atoms = [await _add_atom(backend, entity, 1)]
    await _describe(adapter, rag, entity.id)
    for start in (2, 4):
        atoms.extend(
            [
                await _add_atom(backend, entity, start),
                await _add_atom(backend, entity, start + 1),
            ]
        )
        await _describe(adapter, rag, entity.id)

    checkpoint = await backend.get_owner_summary_checkpoint(entity.id)
    assert checkpoint["covered_atom_ids"] == [atom.id for atom in atoms]
    assert checkpoint["incremental_compaction_count"] == 0
    assert checkpoint["last_reason"] == "periodic_full_rebuild"
    await backend.finalize()
