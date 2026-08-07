#!/usr/bin/env python3
"""Live smoke test for the retained MAGI Core engine surface."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
ARTIFACTS = ROOT / "mgc-test" / "artifacts" / "live-core-test"
WORKING_DIR = ARTIFACTS / "ragstore"
WORKSPACE = "magi_memo_core_test"
SAMPLES = ROOT / "mgc-test" / "inputs" / "__parsed__"
ENV_FILE = ROOT / ".env"

load_dotenv(ENV_FILE, override=False)
sys.path.insert(0, str(SOURCE_ROOT))

from magi_core import LightRAG, QueryParam  # noqa: E402
from magi_core.base import DocStatus  # noqa: E402
from magi_core.llm.openai import openai_complete_if_cache, openai_embed  # noqa: E402
from magi_core.utils import EmbeddingFunc  # noqa: E402


console = Console()
logging.getLogger("magi_core").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("nano-vectordb").setLevel(logging.WARNING)
_llm_gate = asyncio.Lock()
_last_llm_call = 0.0


def env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} in {ENV_FILE}")
    return value


def env_json(name: str) -> dict[str, Any]:
    value = os.getenv(name, "").strip()
    return json.loads(value) if value else {}


async def llm_model(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict[str, Any]] | None = None,
    keyword_extraction: bool = False,
    **kwargs: Any,
) -> str:
    global _last_llm_call
    kwargs.pop("model", None)
    kwargs.setdefault("extra_body", env_json("OPENAI_LLM_EXTRA_BODY"))
    kwargs.setdefault("max_tokens", int(os.getenv("OPENAI_LLM_MAX_TOKENS", "4096")))
    async with _llm_gate:
        interval = float(os.getenv("CORE_TEST_LLM_INTERVAL", "2"))
        await asyncio.sleep(max(0.0, interval - (time.monotonic() - _last_llm_call)))
        retries = int(os.getenv("CORE_TEST_LLM_RETRIES", "5"))
        for attempt in range(1, retries + 1):
            try:
                return await openai_complete_if_cache(
                    env("LLM_MODEL"),
                    prompt,
                    system_prompt=system_prompt,
                    history_messages=history_messages or [],
                    keyword_extraction=keyword_extraction,
                    api_key=env("LLM_BINDING_API_KEY"),
                    base_url=env("LLM_BINDING_HOST"),
                    timeout=int(os.getenv("LLM_TIMEOUT", "300")),
                    **kwargs,
                )
            except Exception as exc:
                retryable = any(
                    marker in repr(exc)
                    for marker in ("RateLimitError", "RetryError", "1302", "1305")
                )
                if not retryable or attempt == retries:
                    raise
                delay = min(30, attempt * 5)
                console.print(
                    f"[yellow]↻ LLM provider busy; retry cycle "
                    f"{attempt + 1}/{retries} in {delay}s[/yellow]"
                )
                await asyncio.sleep(delay)
            finally:
                _last_llm_call = time.monotonic()


async def embedding_model(
    texts: list[str], max_token_size: int | None = None
) -> Any:
    return await openai_embed.func(
        texts,
        model=env("EMBEDDING_MODEL"),
        api_key=env("EMBEDDING_BINDING_API_KEY"),
        base_url=env("EMBEDDING_BINDING_HOST"),
        max_token_size=max_token_size,
    )


def build_rag() -> LightRAG:
    embedding = EmbeddingFunc(
        embedding_dim=int(env("EMBEDDING_DIM")),
        max_token_size=int(os.getenv("EMBEDDING_TOKEN_LIMIT", "8192")),
        send_dimensions=False,
        model_name=env("EMBEDDING_MODEL"),
        func=embedding_model,
    )
    return LightRAG(
        working_dir=str(WORKING_DIR),
        workspace=WORKSPACE,
        llm_model_func=llm_model,
        llm_model_name=env("LLM_MODEL"),
        llm_model_max_async=1,
        embedding_func=embedding,
        embedding_func_max_async=int(os.getenv("EMBEDDING_FUNC_MAX_ASYNC", "4")),
        embedding_batch_num=int(os.getenv("EMBEDDING_BATCH_NUM", "16")),
        kv_storage=env("LIGHTRAG_KV_STORAGE"),
        vector_storage=env("LIGHTRAG_VECTOR_STORAGE"),
        graph_storage=env("LIGHTRAG_GRAPH_STORAGE"),
        doc_status_storage=env("LIGHTRAG_DOC_STATUS_STORAGE"),
        max_parallel_insert=1,
        addon_params={"language": os.getenv("SUMMARY_LANGUAGE", "Chinese")},
    )


@dataclass
class Result:
    name: str
    status: str
    seconds: float
    detail: str


class Evaluation:
    def __init__(self) -> None:
        self.results: list[Result] = []

    async def run(
        self,
        name: str,
        call: Callable[[], Awaitable[Any]],
        check: Callable[[Any], bool] = bool,
        detail: Callable[[Any], str] | None = None,
    ) -> Any:
        start = time.perf_counter()
        try:
            value = await call()
            if not check(value):
                raise AssertionError("result validation failed")
            elapsed = time.perf_counter() - start
            message = detail(value) if detail else summarize(value)
            self.results.append(Result(name, "PASS", elapsed, message))
            console.print(f"[green]✓[/green] {name} [dim]{elapsed:.2f}s[/dim]")
            return value
        except Exception as exc:
            elapsed = time.perf_counter() - start
            self.results.append(Result(name, "FAIL", elapsed, str(exc)))
            console.print(f"[red]✗[/red] {name} [dim]{elapsed:.2f}s[/dim] — {exc}")
            return None

    def finish(self) -> None:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        payload = {
            "workspace": WORKSPACE,
            "llm": os.getenv("LLM_MODEL"),
            "embedding": os.getenv("EMBEDDING_MODEL"),
            "results": [asdict(item) for item in self.results],
        }
        (ARTIFACTS / "core_test_results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        table = Table(title="LightRAG Core Evaluation")
        table.add_column("Result", width=7)
        table.add_column("Interface")
        table.add_column("Time", justify="right")
        table.add_column("Detail", overflow="fold")
        for item in self.results:
            color = "green" if item.status == "PASS" else "red"
            table.add_row(
                f"[{color}]{item.status}[/{color}]",
                item.name,
                f"{item.seconds:.2f}s",
                item.detail,
            )
        console.print()
        console.print(table)
        passed = sum(item.status == "PASS" for item in self.results)
        console.print(
            f"\n[bold]{passed}/{len(self.results)} passed[/bold] · "
            f"report: {ARTIFACTS / 'core_test_results.json'}"
        )


def summarize(value: Any) -> str:
    if value is None:
        return "completed"
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, str):
        return value.replace("\n", " ")[:100]
    if isinstance(value, dict):
        return f"{len(value)} item(s): {', '.join(map(str, list(value)[:5]))}"
    if isinstance(value, (list, tuple, set)):
        return f"{len(value)} item(s)"
    return str(value)[:100]


async def reset_test_workspace(rag: LightRAG) -> None:
    if not WORKSPACE.endswith("_core_test"):
        raise RuntimeError("Refusing to reset a non-test workspace")
    storages = [
        rag.text_chunks,
        rag.full_docs,
        rag.full_entities,
        rag.full_relations,
        rag.entity_chunks,
        rag.relation_chunks,
        rag.entities_vdb,
        rag.relationships_vdb,
        rag.chunks_vdb,
        rag.chunk_entity_relation_graph,
        rag.llm_response_cache,
        rag.doc_status,
    ]
    for storage in storages:
        result = await storage.drop()
        if result.get("status") != "success":
            raise RuntimeError(result.get("message", "storage reset failed"))


def query_counts(value: dict[str, Any]) -> str:
    data = value.get("data", {})
    return (
        f"entities={len(data.get('entities', []))}, "
        f"relations={len(data.get('relationships', []))}, "
        f"chunks={len(data.get('chunks', []))}"
    )


async def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    samples = [SAMPLES / name for name in ("asdf.txt", "qwer.txt", "zxcv.txt")]
    missing = [str(path) for path in samples if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing sample files: {missing}")

    evaluation = Evaluation()
    rag = build_rag()
    console.rule("[bold cyan]LightRAG core · live interface evaluation")
    console.print(
        f"workspace=[cyan]{WORKSPACE}[/cyan]  "
        f"llm=[cyan]{env('LLM_MODEL')}[/cyan]  "
        f"embedding=[cyan]{env('EMBEDDING_MODEL')}[/cyan]\n"
    )

    try:
        await evaluation.run(
            "initialize_storages",
            rag.initialize_storages,
            lambda _: True,
            lambda _: "Neo4j + NanoVectorDB + JSON stores",
        )
        await evaluation.run(
            "workspace reset",
            lambda: reset_test_workspace(rag),
            lambda _: True,
            lambda _: WORKSPACE,
        )
        await evaluation.run(
            "check_and_migrate_data",
            rag.check_and_migrate_data,
            lambda _: True,
            lambda _: "migration check completed",
        )

        console.rule("[bold]Document ingestion")
        doc_ids = ["doc-core-asdf", "doc-core-qwer", "doc-core-zxcv"]
        track_id = await evaluation.run(
            "ainsert",
            lambda: rag.ainsert(
                [path.read_text(encoding="utf-8") for path in samples],
                ids=doc_ids,
                file_paths=[path.name for path in samples],
            ),
            lambda value: isinstance(value, str) and bool(value),
        )
        await evaluation.run(
            "aget_docs_by_track_id",
            lambda: rag.aget_docs_by_track_id(track_id),
            lambda value: len(value) == 3,
        )
        await evaluation.run(
            "aget_docs_by_ids",
            lambda: rag.aget_docs_by_ids(doc_ids),
            lambda value: len(value) == 3
            and all(
                (item.get("status") if isinstance(item, dict) else item.status)
                in (DocStatus.PROCESSED, DocStatus.PROCESSED.value)
                for item in value.values()
            ),
        )
        await evaluation.run(
            "get_docs_by_status + get_processing_status",
            lambda: status_snapshot(rag),
            lambda value: value["processed"] >= 3,
            lambda value: f"processed={value['processed']}",
        )

        custom_doc = "doc-core-custom-chunks"
        await evaluation.run(
            "ainsert_custom_chunks",
            lambda: rag.ainsert_custom_chunks(
                "MAGI Core 由三个协作单元组成，并通过 Blackboard 交换事件。",
                [
                    "MAGI Core 由三个协作单元组成。",
                    "协作单元通过 Blackboard 交换 MQP 事件。",
                ],
                custom_doc,
            ),
            lambda _: True,
            lambda _: custom_doc,
        )
        await evaluation.run(
            "ainsert_custom_kg",
            lambda: rag.ainsert_custom_kg(custom_kg_fixture()),
            lambda _: True,
            lambda _: "2 entities + 1 relation + 1 chunk",
        )

        console.rule("[bold]Graph and retrieval")
        await evaluation.run(
            "get_graph_labels",
            rag.get_graph_labels,
            lambda value: "林晓" in value and "Radiohead" in value,
            lambda value: f"{len(value)} labels",
        )
        await evaluation.run(
            "get_knowledge_graph",
            lambda: rag.get_knowledge_graph("林晓", max_depth=2, max_nodes=50),
            lambda value: len(value.nodes) > 0,
            lambda value: f"nodes={len(value.nodes)}, edges={len(value.edges)}",
        )
        await evaluation.run(
            "get_entity_info",
            lambda: rag.get_entity_info("CORE_KG_A"),
            lambda value: value.get("graph_data") is not None,
        )
        await evaluation.run(
            "get_relation_info",
            lambda: rag.get_relation_info("CORE_KG_A", "CORE_KG_B"),
            lambda value: value.get("graph_data") is not None,
        )

        for mode in ("naive", "local", "global", "hybrid", "mix"):
            await evaluation.run(
                f"aquery_data ({mode})",
                lambda mode=mode: rag.aquery_data(
                    "Radiohead、杭州和南京分别有哪些人物、作品或活动？",
                    QueryParam(mode=mode, enable_rerank=False, top_k=20, chunk_top_k=10),
                ),
                lambda value: value.get("status") == "success"
                and any(value.get("data", {}).get(key) for key in ("entities", "relationships", "chunks")),
                query_counts,
            )

        await evaluation.run(
            "aquery",
            lambda: rag.aquery(
                "王工现在在哪里担任什么角色？",
                QueryParam(mode="hybrid", enable_rerank=False),
            ),
            lambda value: isinstance(value, str)
            and "上海" in value
            and "系统架构师" in value,
            lambda value: f"{len(value)} chars; key facts matched",
        )
        await evaluation.run(
            "aquery_llm",
            lambda: rag.aquery_llm(
                "华星机器人计划向哪所大学捐赠什么？",
                QueryParam(mode="mix", enable_rerank=False),
            ),
            lambda value: query_llm_contains(value, "复旦大学", "人形机器人"),
            lambda value: f"{len(value['llm_response']['content'])} chars; key facts matched",
        )

        console.rule("[bold]Graph mutations")
        await evaluation.run(
            "acreate_entity",
            lambda: create_crud_entities(rag),
            lambda value: all(value),
            lambda _: "CORE_CRUD_A + CORE_CRUD_B",
        )
        await evaluation.run(
            "acreate_relation",
            lambda: rag.acreate_relation(
                "CORE_CRUD_A",
                "CORE_CRUD_B",
                {
                    "description": "A connects to B in the core test.",
                    "keywords": "core,test",
                    "source_id": "core-test",
                },
            ),
            lambda value: value.get("graph_data") is not None,
        )
        await evaluation.run(
            "aedit_entity",
            lambda: rag.aedit_entity(
                "CORE_CRUD_A",
                {
                    "entity_name": "CORE_CRUD_A_EDITED",
                    "description": "Renamed entity used by the core interface test.",
                },
            ),
            lambda value: value.get("entity_name") == "CORE_CRUD_A_EDITED",
        )
        await evaluation.run(
            "aedit_relation",
            lambda: rag.aedit_relation(
                "CORE_CRUD_A_EDITED",
                "CORE_CRUD_B",
                {"description": "The relation was updated successfully."},
            ),
            lambda value: "updated" in value["graph_data"]["description"],
        )
        await evaluation.run(
            "amerge_entities",
            lambda: create_and_merge_aliases(rag),
            lambda value: value.get("entity_name") == "CORE_CANONICAL",
        )
        await evaluation.run(
            "adelete_by_relation",
            lambda: rag.adelete_by_relation("CORE_CRUD_A_EDITED", "CORE_CRUD_B"),
            lambda value: value.status == "success",
        )
        await evaluation.run(
            "adelete_by_entity",
            lambda: delete_entities(rag, ["CORE_CRUD_A_EDITED", "CORE_CRUD_B", "CORE_CANONICAL"]),
            lambda value: all(item.status == "success" for item in value),
            lambda value: f"{len(value)} entities deleted",
        )

        console.rule("[bold]Export, recovery and cleanup")
        export_path = ARTIFACTS / "core_graph_export.md"
        await evaluation.run(
            "aexport_data",
            lambda: rag.aexport_data(str(export_path), file_format="md"),
            lambda _: export_path.is_file() and export_path.stat().st_size > 0,
            lambda _: str(export_path),
        )
        await evaluation.run(
            "arollback_failed_custom_chunk_patches",
            rag.arollback_failed_custom_chunk_patches,
            lambda value: isinstance(value, dict),
        )
        await evaluation.run(
            "adelete_by_doc_id",
            lambda: rag.adelete_by_doc_id(custom_doc, delete_llm_cache=True),
            lambda value: value.status == "success",
        )
        await evaluation.run(
            "role config update + queue status",
            lambda: control_plane_snapshot(rag),
            lambda value: value["query_max_async"] == 1
            and all(key in value for key in ("roles", "llm", "embedding", "rerank")),
            lambda _: "dynamic role update + LLM/embedding/rerank queues",
        )
        await evaluation.run(
            "aclear_cache",
            rag.aclear_cache,
            lambda _: True,
            lambda _: "LLM cache cleared",
        )
    finally:
        await evaluation.run(
            "finalize_storages",
            rag.finalize_storages,
            lambda _: True,
            lambda _: "all storage handles finalized",
        )
        evaluation.finish()

    return 0 if all(item.status == "PASS" for item in evaluation.results) else 1


async def status_snapshot(rag: LightRAG) -> dict[str, int]:
    processed = await rag.get_docs_by_status(DocStatus.PROCESSED)
    counts = await rag.get_processing_status()
    return {"processed": len(processed), **counts}


def custom_kg_fixture() -> dict[str, Any]:
    return {
        "chunks": [{"content": "CORE_KG_A 与 CORE_KG_B 通过测试关系相连。", "source_id": "core-kg"}],
        "entities": [
            {"entity_name": "CORE_KG_A", "entity_type": "TEST", "description": "Core KG test entity A.", "source_id": "core-kg"},
            {"entity_name": "CORE_KG_B", "entity_type": "TEST", "description": "Core KG test entity B.", "source_id": "core-kg"},
        ],
        "relationships": [
            {"src_id": "CORE_KG_A", "tgt_id": "CORE_KG_B", "description": "A deterministic core test relation.", "keywords": "core,test", "source_id": "core-kg"}
        ],
    }


def query_llm_contains(value: dict[str, Any], *needles: str) -> bool:
    content = value.get("llm_response", {}).get("content") or ""
    return value.get("status") == "success" and all(item in content for item in needles)


async def create_crud_entities(rag: LightRAG) -> list[dict[str, Any]]:
    return [
        await rag.acreate_entity("CORE_CRUD_A", {"entity_type": "TEST", "description": "Core CRUD entity A."}),
        await rag.acreate_entity("CORE_CRUD_B", {"entity_type": "TEST", "description": "Core CRUD entity B."}),
    ]


async def create_and_merge_aliases(rag: LightRAG) -> dict[str, Any]:
    await rag.acreate_entity("CORE_ALIAS_A", {"entity_type": "TEST", "description": "First alias."})
    await rag.acreate_entity("CORE_ALIAS_B", {"entity_type": "TEST", "description": "Second alias."})
    return await rag.amerge_entities(
        ["CORE_ALIAS_A", "CORE_ALIAS_B"],
        "CORE_CANONICAL",
        merge_strategy={"description": "concatenate", "entity_type": "keep_first"},
    )


async def delete_entities(rag: LightRAG, names: list[str]) -> list[Any]:
    return [await rag.adelete_by_entity(name) for name in names]


async def control_plane_snapshot(rag: LightRAG) -> dict[str, Any]:
    await rag.aupdate_llm_role_config("query", max_async=1)
    await rag.wait_for_retired_llm_queues()
    query_config = rag.get_llm_role_config("query")
    return {
        "roles": rag.get_llm_role_config(),
        "query_max_async": query_config["max_async"],
        "llm": await rag.get_llm_queue_status(),
        "embedding": await rag.get_embedding_queue_status(),
        "rerank": await rag.get_rerank_queue_status(),
    }


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
