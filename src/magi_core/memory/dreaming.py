"""BALTHASAR offline community reconstruction and snapshot publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from magi_core.utils import TokenTracker, logger, tolerant_load_json_dict

MIN_COMMUNITY_SIZE = 2
REPORT_MEMBER_LIMIT = 80
REPORT_RELATION_LIMIT = 160
DREAM_REPORT_MAX_ATTEMPTS = 2

DREAM_REPORT_SYSTEM_PROMPT = """You are BALTHASAR, MAGI's offline memory reflection system.
Given one graph community, identify its shared subject and explain the important connections.
Return exactly one JSON object with two string fields:
- name: a concise, specific community name, at most 80 characters
- report: a cohesive factual synthesis of the community and its meaningful relationships
Do not invent facts that are absent from the supplied graph context."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_report_payload(payload: dict[str, Any]) -> tuple[str, str]:
    """Normalize the small set of JSON shapes returned by chat providers."""

    candidates = [payload]
    for key in ("result", "data", "community", "report"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)

    name = ""
    report = ""
    for candidate in candidates:
        if not name:
            for key in ("name", "community_name", "title", "topic"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    name = value.strip()[:80]
                    break
        if not report:
            for key in (
                "report",
                "community_report",
                "summary",
                "content",
                "description",
            ):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    report = value.strip()
                    break
        if name and report:
            break
    return name, report


def canonicalize_communities(
    raw_assignments: Mapping[str, int | str],
) -> dict[str, str]:
    """Return stable IDs for actual communities, excluding singleton partitions.

    Leiden returns every projected node, including disconnected nodes as
    one-member partitions.  Those nodes remain valid graph entities, but a
    single entity is not a community and must stay unassigned after publish.
    """

    members: dict[str, list[str]] = {}
    for entity_id, raw_community_id in raw_assignments.items():
        members.setdefault(str(raw_community_id), []).append(str(entity_id))

    stable_ids: dict[str, str] = {}
    for raw_community_id, entity_ids in members.items():
        if len(entity_ids) < MIN_COMMUNITY_SIZE:
            continue
        identity = "\x1f".join(sorted(entity_ids))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        stable_ids[raw_community_id] = f"community-{digest}"
    return {
        str(entity_id): stable_ids[str(raw_community_id)]
        for entity_id, raw_community_id in raw_assignments.items()
        if str(raw_community_id) in stable_ids
    }


class DreamingRunner:
    """Coordinate GDS computation, SQLite staging, and atomic graph publish."""

    def __init__(self, graph: Any, sqlite: Any, llm_func: Any | None = None) -> None:
        self.graph = graph
        self.sqlite = sqlite
        self.llm_func = llm_func

    @staticmethod
    def ensure_supported(graph: Any) -> None:
        if not callable(getattr(graph, "compute_leiden_communities", None)):
            raise RuntimeError(
                "Dreaming requires Neo4j graph storage with the GDS plugin"
            )
        if not callable(getattr(graph, "publish_dreaming_memberships", None)):
            raise RuntimeError(
                "The configured graph storage cannot publish Dreaming snapshots"
            )

    async def _restore_previous_snapshot(
        self, previous_snapshot: dict[str, Any] | None
    ) -> None:
        await self.graph.publish_dreaming_memberships(
            snapshot_id=(
                previous_snapshot["snapshot_id"]
                if previous_snapshot is not None
                else ""
            ),
            assignments=(
                previous_snapshot["assignments"]
                if previous_snapshot is not None
                else {}
            ),
            published_at=(
                previous_snapshot["published_at"]
                if previous_snapshot is not None
                else _utc_now()
            ),
            reports=(
                previous_snapshot.get("reports", {})
                if previous_snapshot is not None
                else {}
            ),
        )

    @staticmethod
    def _estimated_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    async def _generate_community_reports(
        self, assignments: dict[str, str]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        if not assignments or self.llm_func is None:
            return {}, {
                "report_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "llm_call_count": 0,
                "token_usage_source": None,
            }
        loader = getattr(self.graph, "get_dreaming_community_contexts", None)
        if not callable(loader):
            raise RuntimeError("Graph storage cannot provide Dreaming report context")
        contexts = await loader(assignments)

        async def generate(community_id: str, context: dict) -> tuple[str, dict]:
            members = list(context.get("members", []))[:REPORT_MEMBER_LIMIT]
            relationships = list(context.get("relationships", []))[
                :REPORT_RELATION_LIMIT
            ]
            prompt = json.dumps(
                {
                    "community_id": community_id,
                    "member_count": len(context.get("members", [])),
                    "members": members,
                    "relationships": relationships,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            tracker = TokenTracker()
            community_name = ""
            report = ""
            estimated_prompt_tokens = 0
            estimated_completion_tokens = 0
            invalid_shapes: list[str] = []
            attempts = 0
            for attempt in range(DREAM_REPORT_MAX_ATTEMPTS):
                attempts += 1
                attempt_prompt = prompt
                if attempt:
                    attempt_prompt += (
                        "\n\nThe previous response could not be validated. Return only "
                        'one JSON object with non-empty string fields "name" and '
                        '"report". Do not add prose or a JSON array.'
                    )
                response = await self.llm_func(
                    attempt_prompt,
                    system_prompt=DREAM_REPORT_SYSTEM_PROMPT,
                    response_format={"type": "json_object"},
                    token_tracker=tracker,
                )
                response_text = str(response)
                estimated_prompt_tokens += self._estimated_tokens(
                    f"{DREAM_REPORT_SYSTEM_PROMPT}\n{attempt_prompt}"
                )
                estimated_completion_tokens += self._estimated_tokens(response_text)
                parsed = tolerant_load_json_dict(response_text)
                community_name, report = _normalize_report_payload(parsed)
                if community_name and report:
                    break
                shape = ",".join(sorted(parsed)) if parsed else "non-object"
                invalid_shapes.append(shape)
                logger.warning(
                    "Dream report response failed validation for %s "
                    "(attempt=%s, shape=%s, chars=%s, sha256=%s)",
                    community_id,
                    attempts,
                    shape,
                    len(response_text),
                    hashlib.sha256(response_text.encode("utf-8")).hexdigest()[:12],
                )
            if not community_name or not report:
                raise RuntimeError(
                    f"Dream report for {community_id} was invalid after "
                    f"{attempts} attempts (shapes: {'; '.join(invalid_shapes)})"
                )
            usage = tracker.get_usage()
            usage_source = "provider"
            if int(usage.get("total_tokens", 0)) <= 0:
                usage = {
                    "prompt_tokens": estimated_prompt_tokens,
                    "completion_tokens": estimated_completion_tokens,
                    "total_tokens": estimated_prompt_tokens
                    + estimated_completion_tokens,
                    "call_count": attempts,
                }
                usage_source = "estimated"
            return community_id, {
                "community_name": community_name,
                "report": report,
                "member_count": len(context.get("members", [])),
                "prompt_tokens": int(usage["prompt_tokens"]),
                "completion_tokens": int(usage["completion_tokens"]),
                "total_tokens": int(usage["total_tokens"]),
                "llm_call_count": int(usage.get("call_count", attempts)),
                "token_usage_source": usage_source,
            }

        generated = await asyncio.gather(
            *(generate(community_id, context) for community_id, context in contexts.items())
        )
        reports = dict(generated)
        sources = {report["token_usage_source"] for report in reports.values()}
        aggregate = {
            "report_count": len(reports),
            "prompt_tokens": sum(report["prompt_tokens"] for report in reports.values()),
            "completion_tokens": sum(
                report["completion_tokens"] for report in reports.values()
            ),
            "total_tokens": sum(report["total_tokens"] for report in reports.values()),
            "llm_call_count": sum(
                report["llm_call_count"] for report in reports.values()
            ),
            "token_usage_source": (
                next(iter(sources)) if len(sources) == 1 else "mixed"
            ),
        }
        return reports, aggregate

    async def run(self, run_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        self.ensure_supported(self.graph)
        snapshot_id = f"dream-snapshot-{uuid4()}"
        previous_snapshot: dict[str, Any] | None = None
        graph_published = False
        try:
            await self.sqlite.update_dreaming_run_phase(run_id, "projecting")
            result = await self.graph.compute_leiden_communities(**dict(config))
            assignments = canonicalize_communities(result["assignments"])
            await self.sqlite.update_dreaming_run_phase(run_id, "reporting")
            reports, usage = await self._generate_community_reports(assignments)
            await self.sqlite.update_dreaming_run_phase(run_id, "staging")
            await self.sqlite.prepare_dreaming_snapshot(
                run_id=run_id,
                snapshot_id=snapshot_id,
                assignments=assignments,
                algorithm="leiden",
                algorithm_version=result.get("algorithm_version"),
                config=config,
                node_count=int(result["node_count"]),
                relationship_count=int(result["relationship_count"]),
                reports=reports,
                usage=usage,
            )
            previous_snapshot = await self.sqlite.get_latest_dreaming_memberships()
            if previous_snapshot is not None and not previous_snapshot.get("reports"):
                report_loader = getattr(
                    self.graph, "get_dreaming_community_reports", None
                )
                if callable(report_loader):
                    previous_reports = await report_loader(
                        previous_snapshot["snapshot_id"]
                    )
                    previous_snapshot["reports"] = {
                        report["community_id"]: report
                        for report in previous_reports
                    }
            published_at = _utc_now()
            await self.graph.publish_dreaming_memberships(
                snapshot_id=snapshot_id,
                assignments=assignments,
                published_at=published_at,
                reports=reports,
            )
            graph_published = True
            await self.sqlite.publish_dreaming_snapshot(run_id, snapshot_id)
            return await self.sqlite.get_dreaming_status()
        except asyncio.CancelledError:
            if graph_published:
                await self._restore_previous_snapshot(previous_snapshot)
            await self.sqlite.fail_dreaming_run(
                run_id, "Dreaming was cancelled during server shutdown"
            )
            raise
        except Exception as exc:
            if graph_published:
                try:
                    await self._restore_previous_snapshot(previous_snapshot)
                except Exception:
                    logger.critical(
                        "Failed to restore the prior Dreaming snapshot after run %s",
                        run_id,
                        exc_info=True,
                    )
            await self.sqlite.fail_dreaming_run(run_id, str(exc))
            logger.exception("BALTHASAR Dreaming run %s failed", run_id)
            raise
