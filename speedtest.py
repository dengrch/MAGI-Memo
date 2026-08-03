#!/usr/bin/env python3
"""Benchmark GLM models with LightRAG's real entity-extraction prompt."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from openai import AsyncOpenAI
from rich.console import Console
from rich.table import Table


ROOT = Path(__file__).resolve().parent
LIGHTRAG = ROOT / "LightRAG"
DEFAULT_INPUT = LIGHTRAG / "inputs/magi_memo_dev/__parsed__/qwer.txt"
sys.path.insert(0, str(LIGHTRAG))

from lightrag.constants import (  # noqa: E402
    DEFAULT_MAX_EXTRACTION_ENTITIES,
    DEFAULT_MAX_EXTRACTION_RECORDS,
)
from lightrag.prompt import PROMPTS, get_default_entity_extraction_prompt_profile  # noqa: E402


console = Console()


@dataclass(frozen=True)
class Target:
    provider: str
    model: str
    base_url: str
    api_key: str


@dataclass
class Result:
    target: Target
    mode: str
    run: int
    ttft: float | None = None
    text_ttft: float | None = None
    total: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    text_chars: int = 0
    reasoning_chars: int = 0
    entities: int | None = None
    relations: int | None = None
    error: str | None = None


def required(config: dict[str, Any], name: str, source: Path) -> str:
    value = str(config.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{source} 缺少 {name}")
    return value


def build_targets(selected: set[str]) -> list[Target]:
    official_file = LIGHTRAG / ".env"
    company_file = LIGHTRAG / ".env 0"
    official = dotenv_values(official_file)
    company = dotenv_values(company_file)
    definitions = [
        ("公司", "GLM-5", company, company_file),
        ("公司", "GLM-5.1", company, company_file),
        ("公司", "GLM-5.2", company, company_file),
        ("智谱", "glm-4.7-flash", official, official_file),
    ]
    return [
        Target(
            provider=provider,
            model=model,
            base_url=required(config, "LLM_BINDING_HOST", source),
            api_key=required(config, "LLM_BINDING_API_KEY", source),
        )
        for provider, model, config, source in definitions
        if not selected or model.lower() in selected
    ]


def build_messages(input_path: Path) -> list[dict[str, str]]:
    text = input_path.read_text(encoding="utf-8")
    env = dotenv_values(LIGHTRAG / ".env")
    profile = get_default_entity_extraction_prompt_profile()
    context = {
        "entity_types_guidance": profile["entity_types_guidance"],
        "examples": "\n".join(profile["entity_extraction_json_examples"]),
        "language": env.get("SUMMARY_LANGUAGE") or "Chinese",
        "max_total_records": int(
            env.get("MAX_EXTRACTION_RECORDS") or DEFAULT_MAX_EXTRACTION_RECORDS
        ),
        "max_entity_records": int(
            env.get("MAX_EXTRACTION_ENTITIES") or DEFAULT_MAX_EXTRACTION_ENTITIES
        ),
    }
    system = PROMPTS["entity_extraction_json_system_prompt"].format(**context)
    user = PROMPTS["entity_extraction_json_user_prompt"].format(
        **context, input_text=text, heading_context_block=""
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def usage_value(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None)
    return int(value) if value is not None else None


async def benchmark(
    target: Target,
    mode: str,
    run: int,
    messages: list[dict[str, str]],
    timeout: float,
    max_tokens: int,
) -> Result:
    result = Result(target=target, mode=mode, run=run)
    client = AsyncOpenAI(
        api_key=target.api_key,
        base_url=target.base_url,
        timeout=timeout,
    )
    request: dict[str, Any] = {
        "model": target.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if mode == "disabled":
        request["extra_body"] = {"thinking": {"type": "disabled"}}

    started = time.perf_counter()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage = None
    try:
        stream = await client.chat.completions.create(**request)
        async for chunk in stream:
            now = time.perf_counter()
            if chunk.usage:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = delta.content or ""
            reasoning = getattr(delta, "reasoning_content", None) or ""
            if (text or reasoning) and result.ttft is None:
                result.ttft = now - started
            if text and result.text_ttft is None:
                result.text_ttft = now - started
            text_parts.append(text)
            reasoning_parts.append(reasoning)

        result.total = time.perf_counter() - started
        content = "".join(text_parts)
        reasoning_content = "".join(reasoning_parts)
        result.text_chars = len(content)
        result.reasoning_chars = len(reasoning_content)
        result.prompt_tokens = usage_value(usage, "prompt_tokens")
        result.completion_tokens = usage_value(usage, "completion_tokens")
        details = getattr(usage, "completion_tokens_details", None)
        result.reasoning_tokens = usage_value(details, "reasoning_tokens")
        try:
            parsed = json.loads(content)
            result.entities = len(parsed.get("entities", []))
            result.relations = len(parsed.get("relationships", []))
        except (json.JSONDecodeError, AttributeError):
            pass
    except Exception as exc:
        result.total = time.perf_counter() - started
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        await client.close()
    return result


def seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def tokens(value: int | None) -> str:
    return "-" if value is None else str(value)


def show(results: list[Result]) -> None:
    table = Table(title="LightRAG 实体关系抽取 · GLM 延迟分解")
    for title in (
        "节点",
        "模型",
        "thinking",
        "轮",
        "首输出/s",
        "首正文/s",
        "总时/s",
        "生成tok/s",
        "推理tok",
        "推理字",
        "实体/关系",
    ):
        table.add_column(title, justify="right" if title not in {"节点", "模型"} else "left")

    for item in results:
        if item.error:
            table.add_row(
                item.target.provider,
                item.target.model,
                item.mode,
                str(item.run),
                *["-"] * 6,
                f"[red]{item.error[:70]}[/red]",
            )
            continue
        generation_time = (
            item.total - item.ttft
            if item.total is not None and item.ttft is not None
            else None
        )
        token_rate = (
            item.completion_tokens / generation_time
            if item.completion_tokens is not None and generation_time
            else None
        )
        table.add_row(
            item.target.provider,
            item.target.model,
            item.mode,
            str(item.run),
            seconds(item.ttft),
            seconds(item.text_ttft),
            seconds(item.total),
            "-" if token_rate is None else f"{token_rate:.1f}",
            tokens(item.reasoning_tokens),
            str(item.reasoning_chars),
            (
                f"{item.entities}/{item.relations}"
                if item.entities is not None and item.relations is not None
                else "[red]JSON失败[/red]"
            ),
        )
    console.print(table)
    console.print(
        "\n[dim]首输出 = 网络、排队、prompt prefill 后的第一个正文或 reasoning token；"
        "首正文 − 首输出 = 可观测 thinking 等待；生成 tok/s = completion tokens ÷ 后续流式耗时。[/dim]"
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--thinking", choices=("default", "disabled", "both"), default="both"
    )
    parser.add_argument("--models", nargs="*", default=[])
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-tokens", type=int, default=16000)
    args = parser.parse_args()

    selected = {model.lower() for model in args.models}
    targets = build_targets(selected)
    if not targets:
        raise SystemExit("没有匹配的模型")
    messages = build_messages(args.input)
    modes = ("default", "disabled") if args.thinking == "both" else (args.thinking,)
    console.print(
        f"输入：[cyan]{args.input}[/cyan]  "
        f"system={len(messages[0]['content'])} chars  "
        f"user={len(messages[1]['content'])} chars\n"
    )

    results: list[Result] = []
    for run in range(1, args.runs + 1):
        for target in targets:
            for mode in modes:
                console.print(
                    f"[dim]→ {target.provider} / {target.model} / {mode} / run {run}[/dim]"
                )
                results.append(
                    await benchmark(
                        target,
                        mode,
                        run,
                        messages,
                        args.timeout,
                        args.max_tokens,
                    )
                )
    show(results)


if __name__ == "__main__":
    asyncio.run(main())
