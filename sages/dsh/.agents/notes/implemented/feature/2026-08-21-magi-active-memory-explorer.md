# Agent Note: Isolate MAGI active memory exploration in one subagent

Status: implemented

English | [中文](2026-08-21-magi-active-memory-explorer.zh.md)

## Problem

Ordinary MAGI mix recall can retrieve strong direct matches while missing a useful relation hidden beyond the initially activated entities. Letting the parent agent iteratively expand the graph would pollute its durable context with every candidate description and evidence response. Moving the loop into MAGI Core would instead make retrieval decisions part of the memory server and remove the agent-directed behavior the feature is intended to study.

The exploration state also needs a stable client contract that can later be carried by an MCP client. Rejected candidates must remain eligible in later rounds, and an empty expansion must never ambiguously mean a missing entity, a truncated result, or a storage failure.

## Decision

The local `magi-memo` Cordis plugin adds an `explore` memory mode and a composite `magi_memory_explore` tool. The composite performs one ordinary mix recall, derives initial entity-name `frontier`, structured `visit`, and semantic `findings`, then starts exactly one one-shot subagent through the configured `ctx.subagents` provider. The plugin requires the subagent service through `inject`; `spawn` is the default provider.

The Web memory-mode package exposes the same shared four-value vocabulary (`auto`, `manual`, `explore`, `off`) in both the `/memory` popup and the conversation composer's selector. Its English and Chinese dictionaries label the new option, so the client never maintains a smaller mode set than the host projection.

The child receives only a compact initial mix state plus `magi_memory_expand` and `magi_memory_evidence`; the raw mix response is not duplicated beside its parsed seeds. It maintains exactly two semantic accumulators: `visit` contains selected entity names and unordered relation endpoint pairs, while `findings` contains selected descriptions and short evidence conclusions. Empty findings owners are omitted. There is no `seen`, `deferred`, `expanded`, path, or server-side exploration session. A candidate is filtered only when both its neighbor entity and relation pair are already in `visit`, so an earlier rejection does not become a permanent blacklist.

Before each expansion or evidence read, the child chooses whichever frontier entities or owners it judges most promising. The prompt imposes no numeric cap, but discourages broad expansion merely because an entity is already in `visit`; every unchosen entity remains eligible for later reconsideration. Concise tool-use narration remains allowed for debugging. Expand descriptions remove Atom lineage ids and structured status/time annotations before model exposure, while retaining semantic prose and keywords. The evidence tool remains the lossless place to inspect those fields, and findings keep only concise semantic conclusions rather than copied evidence payloads.

The normal stop condition is `(current expansion is explicitly exhausted or no current candidate is selected) AND no historical candidate is added in the same round`. Missing frontiers, truncation, tool errors, cancellation, and token limits are abnormal outcomes, not exhaustion. The composite always disposes the child run and preserves the initial `findings` when no valid structured child result is available. `visit` remains internal child state and is never repeated in the parent-facing result.

The child prompt includes the exact required `structured_output` argument skeleton. Only `findings.entities`, `findings.relations`, and `stop_reason` are returned, and they must always be present, with empty arrays retained; `notes` exists only inside an entity or relation finding owner. The child continues to maintain and send `visit` to `expand`, but omits it from final structured output to avoid duplicating identities already represented by findings. This mirrors the runtime schema closely enough to make invalid-argument retries actionable after a long exploration.

Every one-shot child receives a durable label containing a normalized query preview and UTC start timestamp. The catalog intentionally keeps completed one-shot histories for inspection, so their count still grows, but repeated MAGI runs no longer share an indistinguishable fixed label.

MAGI Core addresses public exploration by entity names and unordered endpoint names. It resolves and validates stable entity and relation ids internally. The dsh client removes those stable ids from mix recall, expansion, and evidence results before model exposure. `expand` reports per-frontier completion, truncation, missing names, counts, and a top-level `exhausted` flag that is true only after a complete untruncated one-hop read yields no unvisited candidate. `evidence` resolves owners by name and returns their Atom evidence and evolution history.

## Verification

The keyless active-explorer tests pin compact seed construction, omission of the raw recall duplicate, autonomous frontier/evidence prioritization, concise debugging narration, the findings-only structured-output skeleton and stopping semantics in the isolated prompt, query/timestamp child labels, one-and-only-one subagent start, tool restriction, findings-only abnormal-result preservation, and unconditional disposal. The output schema is checked with dsh's portable-schema validator; binary endpoint cardinality, which that schema subset cannot express, is checked at the plugin result boundary. Client tests pin the two HTTP requests, stable-id hiding, and expand-only evidence-metadata filtering. The Web selector test pins all four visible options and selecting `explore`. MAGI tests pin visit filtering, true exhaustion, explicit missing/truncated states, fail-loud graph reads, unordered relation resolution, and strict route payloads.

## Alternatives considered

**Run the loop in the parent agent.** Rejected because repeated expansion and evidence results would remain in the parent context and obscure the final answer task.

**Run the loop inside MAGI Core.** Rejected because Core should enforce storage and de-duplication semantics, not make relevance decisions with another internal LLM loop.

**Persist server-side exploration sessions or extra rejected-candidate state.** Rejected because explicit `visit` is sufficient for both the plugin and a future MCP client, while rejected candidates must remain reconsiderable from historical context.

**Expose stable ids to the child.** Rejected because names are the fixed model-facing representation. Stable ids remain useful only for Core resolution and consistency validation.

## Consequences

The parent receives only compact `findings` plus completion metadata, while `visit` stays inside the child as iterative de-duplication state. The child retains the context needed to reconsider earlier candidates. The wire contract is stateless and portable to MCP, and true graph exhaustion is machine-distinguishable from partial failure.

The v1 implementation intentionally does not provide multi-subagent aggregation, path visualization, Atom hybrid retrieval, temporal filtering, community isolation, or concurrent graph mutation. Those capabilities require separate decisions and do not change this single-child protocol.
