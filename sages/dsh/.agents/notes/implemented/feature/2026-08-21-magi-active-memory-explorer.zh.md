# Agent Note: 将 MAGI 主动记忆探索隔离到单个 subagent

Status: implemented

[English](2026-08-21-magi-active-memory-explorer.md) | 中文

## Problem

普通 MAGI mix 召回可以找到较强的直接匹配，却可能错过藏在初始激活实体之外的有用关系。若由父 agent 迭代展开图谱，每轮候选描述和证据响应都会污染其持久上下文。若把循环移入 MAGI Core，则检索决策会成为记忆服务的一部分，失去本功能希望研究的 agent 主导行为。

探索状态还需要形成可由未来 MCP 客户端携带的稳定客户端约定。早期被拒绝的候选必须能在后续轮次重新考虑；空展开也绝不能含糊地表示实体缺失、结果截断或存储失败。

## Decision

本地 `magi-memo` Cordis 插件新增 `explore` 记忆模式和复合工具 `magi_memory_explore`。复合工具先执行一次普通 mix 召回，派生初始实体名 `frontier`、结构化 `visit` 和语义 `findings`，再通过已配置的 `ctx.subagents` 提供方启动恰好一个一次性 subagent。插件通过 `inject` 依赖 subagent 服务；默认提供方为 `spawn`。

Web 记忆模式包在 `/memory` 弹窗与会话输入区选择器中公开同一套四值词汇（`auto`、`manual`、`explore`、`off`）。中英文词典都为新选项提供标签，因此客户端不会维护比宿主 projection 更小的模式集合。

child 只接收压缩后的初始 mix 状态以及 `magi_memory_expand` 和 `magi_memory_evidence`；原始 mix 响应不会再与解析后的种子重复注入。它只维护两个语义累积器：`visit` 保存已选择的实体名与无序关系端点对，`findings` 保存已选择的描述和简短证据结论；没有 notes 的 findings owner 会被省略。不存在 `seen`、`deferred`、`expanded`、path 或服务端探索 session。只有邻居实体与关系端点对都已存在于 `visit` 时才过滤候选，因此早期拒绝不会变成永久黑名单。

每次展开或读取证据前，child 自主选择其认为最有潜力的 frontier 实体或 owner。提示词不规定数量上限，但不鼓励仅仅因为实体已在 `visit` 中就进行宽泛展开；所有未选择实体仍可在后续重新考虑。为便于调试，简短的工具使用叙述继续被允许。expand 描述在模型可见前移除 Atom 谱系 id 与结构化状态／时间注记，同时保留语义正文和 keywords；evidence 工具仍是无损查看这些字段的位置，findings 只保存简短语义结论，不复制证据载荷。

正常终止条件是：`（当前展开被明确标为 exhausted，或当前候选一个都没选）AND 同一轮没有从历史候选补选任何内容`。frontier 缺失、截断、工具错误、取消和 token 上限都是异常结果，而不是 exhausted。复合工具始终对 child 运行执行 dispose；若没有有效的结构化 child 结果，则保留初始 `findings`。`visit` 始终是 child 内部状态，不再重复出现在面向父级的结果中。

child 提示词包含精确的 `structured_output` 必填参数骨架。最终只返回 `findings.entities`、`findings.relations` 与 `stop_reason`，这些字段必须始终存在，空数组也要保留；`notes` 只能位于实体或关系 finding owner 内。child 仍维护 `visit` 并将其传给 `expand`，但最终结构化输出省略它，避免重复返回已由 findings 表达的身份列表。该说明紧贴运行时 schema，使长时间探索后的参数错误重试也能得到明确纠正。

每个一次性 child 都获得包含规范化查询摘要和 UTC 启动时间的持久标签。目录刻意保留已完成的一次性历史以供检查，因此数量仍会增长，但重复的 MAGI 运行不再共享无法辨识的固定标签。

MAGI Core 的公开探索接口使用实体名和无序端点名寻址，并在内部解析与校验稳定实体／关系 id。dsh 客户端会在模型可见之前，从 mix 召回、展开和证据结果中移除这些稳定 id。`expand` 返回每个 frontier 的完成、截断、缺失名称与计数，并只在完整且未截断的一跳读取没有得到任何未访问候选时把顶层 `exhausted` 设为 true。`evidence` 按名称解析 owner，并返回其 Atom 证据和演化历史。

## Verification

无密钥主动探索测试固定了压缩种子构造、原始 recall 重复项的省略、frontier／evidence 自主聚焦、简短调试叙述、隔离提示词中仅返回 findings 的精确结构化输出骨架与终止语义、查询／时间 child 标签、一次且仅一次的 subagent 启动、工具限制、异常时仅保留 findings 与无条件 dispose。输出 schema 由 dsh 的可移植 schema 校验器检查；该 schema 子集无法表达的二元端点数量约束在插件结果边界检查。客户端测试固定了两个 HTTP 请求、稳定 id 隐藏以及仅针对 expand 的证据元数据过滤。Web 选择器测试固定了四个可见选项和选择 `explore` 的行为。MAGI 测试固定了 visit 过滤、真实 exhausted、显式缺失／截断状态、图读取快速失败、无序关系解析以及严格路由载荷。

## Alternatives considered

**由父 agent 运行循环。** 否决，因为重复的展开和证据结果会留在父级上下文中，干扰最终回答任务。

**在 MAGI Core 内运行循环。** 否决，因为 Core 应执行存储与去重语义，而不应通过另一个内部 LLM 循环作相关性决策。

**持久化服务端探索 session 或额外的拒绝候选状态。** 否决，因为显式 `visit` 已足够支持插件和未来 MCP 客户端，而被拒绝的候选必须能从历史上下文重新考虑。

**向 child 暴露稳定 id。** 否决，因为名称才是固定的模型可见表示。稳定 id 只用于 Core 解析和一致性校验。

## Consequences

父级只接收紧凑的 `findings` 与完成状态元数据，`visit` 则作为迭代去重状态留在 child 内部。child 仍保留重新考虑早期候选所需的上下文。协议格式保持无状态并可移植到 MCP，真实图边界也能与部分失败由机器明确区分。

v1 刻意不提供多 subagent 聚合、path 可视化、Atom 混合检索、时间过滤、社区隔离或并发图修改。这些能力需要单独决策，并且不改变当前的单 child 协议。
