from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import pytest
from dotenv import load_dotenv

from magi_core.constants import (
    DEFAULT_FILE_PATH_MORE_PLACEHOLDER,
    DEFAULT_MAX_FILE_PATHS,
    GRAPH_FIELD_SEP,
    SOURCE_IDS_LIMIT_METHOD_KEEP,
)
from magi_core.kg.neo4j_impl import Neo4JStorage
from magi_core.kg.shared_storage import finalize_share_data, initialize_share_data
from magi_core.llm.openai import openai_complete_if_cache
from magi_core.operate import _merge_nodes_then_upsert
from magi_core.utils import Tokenizer, TokenizerInterface


TEST_ENTITY = "MAGI_SUMMARY_LINEAGE_TEST_ENTITY"
TEST_ATOM_IDS = tuple(f"atom-summary-live-{index:02d}" for index in range(1, 10))


class _CharacterTokenizer(TokenizerInterface):
    def encode(self, content: str):
        return [ord(character) for character in content]

    def decode(self, tokens):
        return "".join(chr(token) for token in tokens)


async def _unused_embedding(texts: list[str]) -> np.ndarray:
    return np.zeros((len(texts), 3), dtype=np.float32)


def _require_live_environment() -> None:
    load_dotenv(Path(__file__).parents[2] / ".env", override=False)
    required = (
        "NEO4J_URI",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "LLM_BINDING_HOST",
        "LLM_BINDING_API_KEY",
        "LLM_MODEL",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"live MAGI summary environment is missing: {', '.join(missing)}")


@pytest.mark.integration
@pytest.mark.requires_db
@pytest.mark.requires_api
@pytest.mark.asyncio
async def test_live_threshold_summary_is_written_to_mgc_test_neo4j() -> None:
    """Trigger a nine-Atom LLM merge and verify the persisted description."""

    _require_live_environment()
    workspace = os.getenv("WORKSPACE", "magi_memo_dev")
    working_dir = Path(os.getenv("WORKING_DIR", "./mgc-test/ragstore"))

    async def complete(
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list[dict] | None = None,
        **kwargs,
    ) -> str:
        kwargs.pop("_priority", None)
        return await openai_complete_if_cache(
            os.environ["LLM_MODEL"],
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            base_url=os.environ["LLM_BINDING_HOST"],
            api_key=os.environ["LLM_BINDING_API_KEY"],
            timeout=120,
            **kwargs,
        )

    config = {
        "workspace": workspace,
        "working_dir": str(working_dir),
        "role_llm_funcs": {"extract": complete},
        "addon_params": {},
        "_resolved_summary_language": "Chinese",
        "summary_length_recommended": 500,
        "summary_context_size": 12_000,
        "summary_max_tokens": 1_200,
        "force_llm_summary_on_merge": 8,
        "magi_projection_replace": True,
        "magi_preserve_atom_order": True,
        "source_ids_limit_method": SOURCE_IDS_LIMIT_METHOD_KEEP,
        "max_source_ids_per_entity": 300,
        "max_file_paths": DEFAULT_MAX_FILE_PATHS,
        "file_path_more_placeholder": DEFAULT_FILE_PATH_MORE_PLACEHOLDER,
        "embedding_token_limit": None,
        "tokenizer": Tokenizer("characters", _CharacterTokenizer()),
    }
    graph = Neo4JStorage(
        namespace="chunk_entity_relation",
        workspace=workspace,
        global_config=config,
        embedding_func=_unused_embedding,
    )
    descriptions = (
        "测试对象于2018年开始研究知识图谱。",
        "测试对象于2019年加入一个人工智能实验室。",
        "测试对象于2020年负责实体消歧研究。",
        "测试对象于2021年开始研究长期记忆系统。",
        "测试对象于2022年完成图数据库原型。",
        "测试对象于2023年将时间信息引入记忆表示。",
        "测试对象于2024年开始关注多智能体协作。",
        "测试对象于2025年完成记忆检索实验。",
        "测试对象于2026年继续改进可追溯的记忆总结。",
    )
    nodes_data = [
        {
            "entity_name": TEST_ENTITY,
            "entity_type": "TEST_SUBJECT",
            "description": (
                f"[{atom_id}] [status=active; "
                f"valid_at={2017 + index}-01-01T00:00:00+00:00] {description}"
            ),
            "atom_id": atom_id,
            "magi_atom_order": index - 1,
            "source_id": "magi-summary-lineage-live-test",
            "file_path": "tests/memory/test_magi_summary_lineage_integration.py",
            "timestamp": index,
        }
        for index, (atom_id, description) in enumerate(
            zip(TEST_ATOM_IDS, descriptions), start=1
        )
    ]

    initialize_share_data()
    await graph.initialize()
    try:
        if await graph.get_node(TEST_ENTITY) is not None:
            await graph.delete_node(TEST_ENTITY)
        merged = await _merge_nodes_then_upsert(
            TEST_ENTITY,
            nodes_data,
            graph,
            entity_vdb=None,
            global_config=config,
        )
        merged["atom_ids"] = list(TEST_ATOM_IDS)
        await graph.upsert_node(TEST_ENTITY, node_data=merged)

        persisted = await graph.get_node(TEST_ENTITY)
        assert persisted is not None
        description = persisted["description"]
        actual_tags = set(re.findall(r"\[atom-[A-Za-z0-9_-]+\]", description))
        expected_tags = {f"[{atom_id}]" for atom_id in TEST_ATOM_IDS}
        assert actual_tags == expected_tags
        assert GRAPH_FIELD_SEP not in description, (
            "LLM summary was rejected and the raw Atom list was retained"
        )
        assert list(persisted["atom_ids"]) == list(TEST_ATOM_IDS)
        print("\nLIVE_NEO4J_DESCRIPTION_START")
        print(description)
        print("LIVE_NEO4J_DESCRIPTION_END")
    finally:
        if os.getenv("LIGHTRAG_KEEP_ARTIFACTS", "false").lower() != "true":
            if await graph.get_node(TEST_ENTITY) is not None:
                await graph.delete_node(TEST_ENTITY)
        await graph.finalize()
        finalize_share_data()
