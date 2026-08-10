# MAGI Memo 二期第一阶段进度报告

> 历史阶段性文档：本文保留 2026-08-06 时点的进度快照。二期最终状态、验收结果和三期交接项
> 请以 [`phase2-completion-and-phase3-handoff.md`](phase2-completion-and-phase3-handoff.md) 为准。

日期：2026-08-06  
状态：核心写入链路已贯通，尚未达到第一阶段完整验收条件

## 1. 当前结论

方向 B 已落实：Atom 是 SQLite 中的独立记录，Neo4j 保留原有实体—语义关系图，并通过
`atom_ids` 与 Atom 关联；实体与关系的 `description` 由 Atom 文本按写入顺序物化，不把
Atom 节点化。

目前已经能够从 Episode 开始，完成一次模型抽取、实体消歧、Atom/Evidence 持久化、图谱和
向量投影，并继续使用 LightRAG 的检索能力。当前版本适合继续做写入链路验证，但跨存储恢复、
Atom 去重演化、Reflect 和双时间查询仍未完成。

## 2. 已完成

- **工程结构**：LightRAG 核心已演化为 `magi_core`，WebUI 独立在 `src/webui`，测试统一到
  `tests`；核心仍保留原有队列、删除、查询和存储生命周期能力。
- **统一数据模型**：已定义 Episode、Entity、Relation、Atom、AtomEvidence、候选和判定结果；
  Relation 保留稳定 ID、两端实体 ID 与名称、关键词等语义信息。
- **SQLite 记录层**：已实现 Episode、实体别名、关系、Atom、Evidence、Atom Evolution、
  embedding、删除备份等表和迁移；数据库文件为工作区内的 `magi-memory.db`。
- **一对多来源**：一个 Atom 可关联多个 Episode Evidence，`support_count` 按不同 Episode 统计。
- **单次抽取**：Prompt 已改为让模型在同一次调用中直接抽取实体、关系、Atom、别名和双时间，
  不是对旧 LightRAG 抽取结果做事后规范化。
- **实体消歧**：Episode 内先按显式别名归并；再用 SQLite FTS 和名称/别名 embedding 召回候选；
  一次 LLM 调用批量判断本轮所有有候选的实体；随后统一重写关系端点。
- **Atom 批量裁决**：同一次 Episode 写入的全部 Atom 以 owner 隔离候选后，在一次结构化 LLM 调用中
  完成五分类；小 owner 提供全部历史 Atom，大 owner 提供 embedding Top-K、近期 active Atom 与可用
  summary。返回目标必须属于该 Atom 的允许候选集合。
- **Atom 操作语义**：完全相同的 owner 内规范化文本直接复用 Atom 并增加 Evidence；DUPLICATE、
  REFINEMENT、TEMPORAL_SUCCESSOR、CONTRADICTION、INDEPENDENT 均已映射为确定性持久化动作，
  演化关系写入独立 `atom_evolution` 表。
- **严格写入链路**：已贯通
  `Episode → 抽取 → 批量实体消歧 → Atom/Evidence → Entity/Relation → Neo4j/VDB 投影`；
  strict workspace 会阻止严格 MAGI merge 与旧式宽松 merge 混用。
- **图谱物化**：Neo4j 实体节点和语义关系边保存稳定 MAGI ID 与 `atom_ids`；描述由 owner 的
  Atom 重新生成并保留写入顺序，时间状态会进入描述文本。
- **删除适配**：单 Episode 删除前会备份 SQLite 数据，删除 Evidence/孤立 Atom 后会重算受影响
  owner 的图描述和向量；clear、SQLite 备份与恢复基础能力已经接入。
- **接口生命周期**：`MagiAPI` 已提供 `init → open → index/query/extension → close → finalize`，
  支持一个实例上的多个独立句柄。
- **兼容使用**：原 LightRAG 查询模式、Neo4j 图谱页面、上传与队列仍可使用；WebUI 现阶段不再继续
  作为核心研发重点。

## 3. 部分完成

- **双时间系统**：`valid_at/invalid_at` 与 `created_at/expired_at` 已进入模型、SQLite、抽取和描述
  物化；尚缺 query-time 时间点/时间范围过滤的正式接口。
- **删除与回溯**：SQLite 能备份和恢复，删除后能刷新图投影；尚未形成覆盖 SQLite、Neo4j、VDB
  的统一原子回滚与自动故障恢复协议。
- **实体消歧质量**：候选召回、别名向量和批量决策机制已经实现并有单元测试，但阈值、真实数据
  准确率、延迟和小模型选择尚未做系统评估。
- **Projection outbox**：已有表结构和状态统计，但提交链路尚未真正写入 outbox，也没有重试和
  reconcile worker。

## 4. 尚未完成

1. SQLite 已提交、Neo4j/VDB 投影失败时的 outbox 幂等重放和一致性修复。
2. Atom-aware query 与双时间过滤；当前兼容查询主要使用图上的最新物化描述。
3. Reflect：实体/关系引用式 summary、dirty scope、定向重算、community 和 community report。
4. Neo4j GDS 范围聚类的真实插件验收；聚类时不能把 LightRAG `weight` 当语义紧密度。
5. 旧工作区迁移到 strict MAGI 模式的正式工具。
6. 真实 Episode 数据集上的准确率、吞吐、模型调用耗时、候选阈值和故障注入测试。
7. 下一阶段 runtime/API 的共享实例管理、正式句柄并发契约和更完整的生命周期工程。

## 5. 建议的后续顺序

1. 先完成 projection outbox、幂等重放和跨存储故障测试，保证已经贯通的写入链路可恢复。
2. 增加 Atom-aware 双时间查询接口，使现有时间字段真正可用。
3. 用真实 Episode 样本测量批量 Atom 裁决的准确率、上下文规模、调用耗时和候选阈值。
4. 实现 Reflect summary，再开展无权 community 聚类和 report。
5. 第一阶段稳定后，再进入 runtime/API 与多 Agent 共享实例设计。

## 6. 当前验证

- Memory Core 与接口专项回归：`26 passed`。
- 前端回归：`88 passed`。
- 本地工作区已实际跑通文件 Episode 写入，并能在 SQLite、Neo4j 图投影和 WebUI 中查看结果。
- 因 outbox、Atom-aware 双时间查询和 Reflect 尚未完成，当前结论是“核心链路可验收测试”，不是“二期第一阶段完成”。
