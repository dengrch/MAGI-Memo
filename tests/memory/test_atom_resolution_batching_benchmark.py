from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import tiktoken
from dotenv import load_dotenv

from magi_core.llm.openai import openai_complete_if_cache
from magi_core.memory import (
    AtomRecord,
    AtomResolutionRequest,
    CandidateMatch,
)
from magi_core.memory.decisions import LLMMemoryDecisionProvider


ROOT = Path(__file__).parents[2]
ARTIFACT_PATH = ROOT / "mgc-test/artifacts/atom-resolution-benchmark/result.json"
TOKENIZER = tiktoken.get_encoding("cl100k_base")


class _BenchmarkEngine:
    def __init__(self, role) -> None:
        self._role_func = role

    def _build_global_config(self) -> dict[str, Any]:
        return {"role_llm_funcs": {"deduplicate": self._role_func}}


class _MeasuredRole:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.inflight = 0
        self.max_inflight = 0

    async def __call__(self, prompt: str, **kwargs: Any) -> str:
        started = time.perf_counter()
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            response = await openai_complete_if_cache(
                os.environ["LLM_MODEL"],
                prompt,
                base_url=os.environ["LLM_BINDING_HOST"],
                api_key=os.environ["LLM_BINDING_API_KEY"],
                timeout=180,
                response_format=kwargs.get("response_format"),
            )
        finally:
            self.inflight -= 1
        self.calls.append(
            {
                "seconds": round(time.perf_counter() - started, 3),
                "prompt_tokens": len(TOKENIZER.encode(prompt)),
                "completion_tokens": len(TOKENIZER.encode(response)),
            }
        )
        return response


def _atom(
    atom_id: str,
    owner_id: str,
    content: str,
    *,
    valid_at: str | None = None,
) -> AtomRecord:
    relation_fields = (
        {
            "subject_entity_id": "entity-benchmark-zhouhang",
            "predicate": "member_of",
            "object_entity_id": "entity-benchmark-club",
        }
        if owner_id.startswith("relation-")
        else {}
    )
    return AtomRecord(
        id=atom_id,
        workspace_id="magi_memo_dev",
        owner_id=owner_id,
        content=content,
        valid_at=(
            datetime.fromisoformat(valid_at).astimezone(timezone.utc)
            if valid_at
            else None
        ),
        **relation_fields,
    )


def _candidate(atom: AtomRecord, score: float = 0.9) -> CandidateMatch:
    return CandidateMatch(
        object_id=atom.id,
        score=score,
        text=atom.presentation(),
        match_kind="benchmark_owner_candidate",
    )


def _requests() -> list[AtomResolutionRequest]:
    owner_a = "entity-benchmark-zhouhang"
    owner_b = "entity-benchmark-linxia"
    owner_c = "relation-benchmark-zhouhang-club"
    owner_d = "entity-benchmark-magi"

    a_old = _atom(
        "atom-benchmark-a-old",
        owner_a,
        "周航更关注推理小说中的人物塑造。",
        valid_at="2024-01-01T00:00:00+00:00",
    )
    a_history = _atom(
        "atom-benchmark-a-history",
        owner_a,
        "周航认为推理小说中的诡计最重要。",
        valid_at="2018-01-01T00:00:00+00:00",
    )
    b_member = _atom(
        "atom-benchmark-b-member",
        owner_b,
        "林夏是推理社成员。",
        valid_at="2022-01-01T00:00:00+00:00",
    )
    b_reader = _atom(
        "atom-benchmark-b-reader",
        owner_b,
        "林夏喜欢新本格推理作品。",
    )
    c_member = _atom(
        "atom-benchmark-c-member",
        owner_c,
        "周航是推理社成员。",
        valid_at="2020-01-01T00:00:00+00:00",
    )
    c_event = _atom(
        "atom-benchmark-c-event",
        owner_c,
        "周航参加了推理社组织的岛田庄司专题活动。",
        valid_at="2025-06-01T00:00:00+00:00",
    )
    d_graph = _atom(
        "atom-benchmark-d-graph",
        owner_d,
        "MAGI 使用 Neo4j 保存实体关系图。",
    )
    d_atom = _atom(
        "atom-benchmark-d-atom",
        owner_d,
        "MAGI 将 Atom 内容保存在 SQLite 独立记录层。",
    )

    return [
        AtomResolutionRequest(
            atom=_atom(
                "atom-benchmark-a-new-1",
                owner_a,
                "周航目前更关注推理小说中的人物塑造。",
                valid_at="2024-01-01T00:00:00+00:00",
            ),
            candidates=(_candidate(a_old), _candidate(a_history, 0.75)),
            owner_summary="周航的推理小说审美发生过变化。",
        ),
        AtomResolutionRequest(
            atom=_atom(
                "atom-benchmark-a-new-2",
                owner_a,
                "周航尤其关注推理小说中人物动机的塑造。",
                valid_at="2025-01-01T00:00:00+00:00",
            ),
            candidates=(_candidate(a_old), _candidate(a_history, 0.7)),
            owner_summary="周航的推理小说审美发生过变化。",
        ),
        AtomResolutionRequest(
            atom=_atom(
                "atom-benchmark-b-new-1",
                owner_b,
                "林夏自2022年起一直是推理社成员。",
                valid_at="2022-01-01T00:00:00+00:00",
            ),
            candidates=(_candidate(b_member), _candidate(b_reader, 0.6)),
            owner_summary="林夏长期参与推理社活动。",
        ),
        AtomResolutionRequest(
            atom=_atom(
                "atom-benchmark-b-new-2",
                owner_b,
                "林夏负责推理社专题活动的报名协调。",
                valid_at="2026-08-01T00:00:00+00:00",
            ),
            candidates=(_candidate(b_member, 0.75), _candidate(b_reader, 0.55)),
            owner_summary="林夏长期参与推理社活动。",
        ),
        AtomResolutionRequest(
            atom=_atom(
                "atom-benchmark-c-new-1",
                owner_c,
                "周航于2026年退出推理社。",
                valid_at="2026-03-01T00:00:00+00:00",
            ),
            candidates=(_candidate(c_member), _candidate(c_event, 0.65)),
            owner_summary="周航曾参与推理社及其活动。",
        ),
        AtomResolutionRequest(
            atom=_atom(
                "atom-benchmark-c-new-2",
                owner_c,
                "周航在退出推理社后仍参加该社公开活动。",
                valid_at="2026-04-01T00:00:00+00:00",
            ),
            candidates=(_candidate(c_member, 0.7), _candidate(c_event, 0.85)),
            owner_summary="周航曾参与推理社及其活动。",
        ),
        AtomResolutionRequest(
            atom=_atom(
                "atom-benchmark-d-new-1",
                owner_d,
                "MAGI 使用 Neo4j 属性图保存实体节点和语义关系边。",
            ),
            candidates=(_candidate(d_graph), _candidate(d_atom, 0.6)),
            owner_summary="MAGI 将图谱与 Atom 记录分层存储。",
        ),
        AtomResolutionRequest(
            atom=_atom(
                "atom-benchmark-d-new-2",
                owner_d,
                "MAGI 的 Atom 正文、时间字段和 Evidence 存储在 SQLite 中。",
            ),
            candidates=(_candidate(d_graph, 0.6), _candidate(d_atom)),
            owner_summary="MAGI 将图谱与 Atom 记录分层存储。",
        ),
    ]


RECENT_EPISODES = [
    {
        "episode_id": "episode-benchmark-1",
        "content": "周航最近更关注人物塑造，并表示将退出推理社。",
        "reference_at": "2026-03-01T00:00:00+00:00",
    },
    {
        "episode_id": "episode-benchmark-2",
        "content": "林夏继续负责推理社活动，MAGI 使用分层记忆存储。",
        "reference_at": "2026-08-01T00:00:00+00:00",
    },
]


def _decision_rows(resolutions: dict) -> dict[str, dict[str, Any]]:
    return {
        atom_id: {
            "decision": resolution.decision.value,
            "matched_atom_id": resolution.matched_atom_id,
            "confidence": resolution.confidence,
            "reason": resolution.reason,
        }
        for atom_id, resolution in sorted(resolutions.items())
    }


async def _run_monolithic(requests: list[AtomResolutionRequest]) -> dict[str, Any]:
    role = _MeasuredRole()
    provider = LLMMemoryDecisionProvider(_BenchmarkEngine(role))
    started = time.perf_counter()
    resolutions = await provider.resolve_atoms(
        requests=requests,
        recent_episodes=RECENT_EPISODES,
    )
    return {
        "wall_seconds": round(time.perf_counter() - started, 3),
        "request_count": len(role.calls),
        "max_inflight": role.max_inflight,
        "prompt_tokens": sum(call["prompt_tokens"] for call in role.calls),
        "completion_tokens": sum(call["completion_tokens"] for call in role.calls),
        "calls": role.calls,
        "decisions": _decision_rows(resolutions),
    }


async def _run_owner_parallel(requests: list[AtomResolutionRequest]) -> dict[str, Any]:
    grouped: dict[str, list[AtomResolutionRequest]] = defaultdict(list)
    for request in requests:
        grouped[request.atom.owner_id].append(request)

    role = _MeasuredRole()
    provider = LLMMemoryDecisionProvider(_BenchmarkEngine(role))
    semaphore = asyncio.Semaphore(4)

    async def resolve_group(group: list[AtomResolutionRequest]):
        async with semaphore:
            return await provider.resolve_atoms(
                requests=group,
                recent_episodes=RECENT_EPISODES,
            )

    started = time.perf_counter()
    parts = await asyncio.gather(*(resolve_group(group) for group in grouped.values()))
    resolutions = {
        atom_id: resolution for part in parts for atom_id, resolution in part.items()
    }
    return {
        "wall_seconds": round(time.perf_counter() - started, 3),
        "request_count": len(role.calls),
        "max_inflight": role.max_inflight,
        "prompt_tokens": sum(call["prompt_tokens"] for call in role.calls),
        "completion_tokens": sum(call["completion_tokens"] for call in role.calls),
        "calls": sorted(role.calls, key=lambda call: call["seconds"]),
        "decisions": _decision_rows(resolutions),
    }


@pytest.mark.integration
@pytest.mark.requires_api
@pytest.mark.asyncio
async def test_live_atom_resolution_monolithic_vs_owner_parallel() -> None:
    load_dotenv(ROOT / ".env", override=False)
    required = ("LLM_BINDING_HOST", "LLM_BINDING_API_KEY", "LLM_MODEL")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"benchmark environment is missing: {', '.join(missing)}")

    requests = _requests()
    monolithic = await _run_monolithic(requests)
    owner_parallel = await _run_owner_parallel(requests)
    monolithic_decisions = monolithic["decisions"]
    owner_decisions = owner_parallel["decisions"]
    strict_agreement = sum(
        monolithic_decisions[atom_id]["decision"]
        == owner_decisions[atom_id]["decision"]
        and monolithic_decisions[atom_id]["matched_atom_id"]
        == owner_decisions[atom_id]["matched_atom_id"]
        for atom_id in monolithic_decisions
    )
    decision_agreement = sum(
        monolithic_decisions[atom_id]["decision"]
        == owner_decisions[atom_id]["decision"]
        for atom_id in monolithic_decisions
    )

    def effective_target(row: dict[str, Any]) -> str | None:
        return None if row["decision"] == "independent" else row["matched_atom_id"]

    effective_agreement = sum(
        monolithic_decisions[atom_id]["decision"]
        == owner_decisions[atom_id]["decision"]
        and effective_target(monolithic_decisions[atom_id])
        == effective_target(owner_decisions[atom_id])
        for atom_id in monolithic_decisions
    )
    result = {
        "model": os.environ["LLM_MODEL"],
        "owners": 4,
        "new_atoms": len(requests),
        "candidates_per_atom": 2,
        "recent_episodes": len(RECENT_EPISODES),
        "monolithic": monolithic,
        "owner_parallel": owner_parallel,
        "strict_tuple_agreement_count": strict_agreement,
        "strict_tuple_agreement_rate": strict_agreement / len(requests),
        "decision_label_agreement_count": decision_agreement,
        "decision_label_agreement_rate": decision_agreement / len(requests),
        "effective_action_agreement_count": effective_agreement,
        "effective_action_agreement_rate": effective_agreement / len(requests),
        "wall_speedup": round(
            monolithic["wall_seconds"] / owner_parallel["wall_seconds"], 3
        ),
        "prompt_token_ratio": round(
            owner_parallel["prompt_tokens"] / monolithic["prompt_tokens"], 3
        ),
    }
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\nATOM_RESOLUTION_BENCHMARK_START")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("ATOM_RESOLUTION_BENCHMARK_END")

    assert monolithic["request_count"] == 1
    assert owner_parallel["request_count"] == 4
    assert owner_parallel["max_inflight"] == 4
    assert len(monolithic_decisions) == len(requests)
    assert len(owner_decisions) == len(requests)
