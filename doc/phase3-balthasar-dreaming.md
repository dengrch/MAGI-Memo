# BALTHASAR Dreaming v1：社区重建、报告与快照发布

> 实现状态：可用；对应当前 `src/magi_core/memory/dreaming.py`、Neo4j GDS 实现、REST API 与 Balthasar WebUI。

## 1. 定位与边界

Dreaming 是 BALTHASAR 负责的离线记忆反思能力。它不参与每条 Episode 的严格动态写入，也不替代 Atom 裁决；它在显式触发的离线运行中观察已经物化的实体关系图，将局部连接提升为社区主题和社区报告，供全局浏览、后续检索及更高层 Reflect 使用。

当前 v1 已实现：

- 对一个 workspace 的 Neo4j 语义图执行全量 Leiden 社区发现；
- 排除单节点分区，并为真实社区生成与 GDS 临时编号无关的稳定 ID；
- 使用 `dream` 角色模型为每个社区生成 `community_name` 和 `report`；
- 记录每个社区及整次运行的 prompt、completion、total token 和调用次数；
- 先准备 SQLite 快照，再原子替换 Neo4j 当前社区投影，最后发布 SQLite 快照；
- 新写入节点可以临时挂到最近的已发布社区，等待下次全量 Dreaming 重新裁定；
- 在 WebUI 中手动启动、查看状态、社区报告及两种图视图。

当前 v1 不包含：

- 定时或阈值触发的自动 Dreaming；
- 在超大社区内做分层子社区、连续 reduce 或重要子图覆盖；
- 让社区报告反向修改 Atom、冲突状态或其他事实记录；
- 自动更新 `global.md`、`user.md`、LLM Wiki 等更高层长期记忆；
- 多进程服务器中的跨 worker 作业所有权。

这些能力属于后续 Reflect / production update management，而不是当前实现的隐含行为。

## 2. 端到端流程

```text
POST /dreaming/runs
        │
        ▼
SQLite 预占 workspace 唯一运行槽（queued）
        │
        ▼
Neo4j GDS 临时图 + Leiden stream（projecting）
        │
        ▼
排除 singleton，按成员集合生成稳定 community ID
        │
        ▼
逐社区构造实体/内部关系上下文并生成名称与报告（reporting）
        │
        ▼
SQLite 原子写入 prepared snapshot、memberships、reports（staging）
        │
        ▼
Neo4j 单事务替换节点归属与 DreamCommunity 报告（publishing）
        │
        ▼
SQLite 将快照标为 published，运行标为 succeeded（complete）
```

一次运行的阶段依次为 `queued → projecting → reporting → staging → publishing → complete`；异常进入 `failed`。每个 workspace 同时只允许一个 `running` 的全量重建。

SQLite 与 Neo4j 之间没有分布式事务。当前发布协议通过“SQLite 先完整准备、Neo4j 单事务替换、SQLite 最后确认”避免暴露半份分区；如果 Neo4j 已发布但 SQLite 最终确认失败，runner 会补偿性恢复上一个已发布图快照。服务重启时，遗留的 running run 和 prepared snapshot 会被标记失败。

## 3. 社区算法

### 3.1 当前图投影

当前实现使用 Neo4j Graph Data Science 的 `gds.leiden.stream`：

- 节点：当前 workspace label 下具有 `entity_id` 的实体；
- 边：语义图中的 `DIRECTED` 关系；
- 投影方向：`UNDIRECTED`；
- 聚合：`SINGLE`；
- 权重：当前不传 relationship property，因此是无权 Leiden；
- 可调参数：`random_seed`、`gamma`、`theta`、`max_levels`、`concurrency`。

临时 GDS named graph 每次运行使用唯一名称，并在成功或失败后删除。GDS 返回的局部 `communityId` 不会直接成为持久标识。

### 3.2 稳定 ID 与 singleton

Leiden 会为断开的单节点产生一成员分区。MAGI Memo 不把一个实体称为社区，因此成员数小于 2 的分区在发布前被排除，节点保持未归属。

真实社区的稳定 ID 由排序后的完整成员实体 ID 集合做 SHA-256 生成：

```text
community-<16 hex chars>
```

这使 ID 不依赖某次运行内的 GDS 数字编号。同一成员集合会得到同一 ID；成员集合变化则代表一个新社区身份。

### 3.3 新节点的临时归属

图写入完成后，Neo4j backend 只对尚无 `dream_community_id` 的新节点执行临时归属：在 1–3 跳内寻找已经有社区的邻居，依次按最短距离、支持邻居数量和 community ID 选择一个社区，并将状态写为 `provisional`。

这个步骤是在线便利投影：失败不会中断严格记忆写入；它也不会改写上一个 SQLite 完整快照。下一次全量 Dreaming 会清除所有当前图归属，并重新发布 `stable` 结果。

## 4. 社区报告与成本记录

每个社区的模型上下文只包含：

- 社区内实体的名称、类型和当前 description；
- 两端都属于该社区的关系及其 keywords、description。

当前每个报告最多输入 80 个成员和 160 条内部关系。不同社区并发生成报告；这是社区间并发，不是超大社区内部的分层聚合。模型必须返回包含非空 `name` 与 `report` 的 JSON 对象。实现兼容少量常见外层包装及 `community_name`、`title`、`summary` 等字段别名；验证失败时会追加严格提示重试一次，仍失败则整次 Dreaming 不发布。

token 使用优先读取 provider 上报值；provider 未返回 usage 时使用字符数估算并将来源标为 `estimated`。以下指标同时保存到社区报告、run 和 snapshot：

- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `llm_call_count`
- `token_usage_source`（`provider`、`estimated` 或聚合后的 `mixed`）

Dreaming 使用独立的 `dream` / `DREAM_*` 角色路由。WebUI Runtime Model 设置可以热更新该角色所用 provider、host、model 等配置，不需要重启服务。

## 5. 存储模型

SQLite 保存可审计的运行与历史快照：

- `dreaming_runs`：状态、阶段、配置、计数、token 与错误；
- `dreaming_snapshots`：算法、版本、完整分区元数据和发布状态；
- `dreaming_memberships`：某个快照内的实体—社区映射；
- `dreaming_community_reports`：社区名称、报告、规模及调用成本。

Neo4j 保存当前服务投影：

- 实体节点：`dream_community_id`、`dream_community_name`、`dream_membership_status`、`dream_snapshot_id`、`dream_published_at`；
- `DreamCommunity` 节点：当前快照的社区名称、报告、成员规模和 token 指标。

普通图谱 API 默认只向实体属性暴露 `community_id` 和 `community_name`，内部 Dreaming 字段仅在显式请求内部视图时返回。社区报告是派生信息；Episode、Atom 与 Evidence 仍是事实及证据的权威来源。

## 6. REST API 与 WebUI

| 接口 | 作用 |
|---|---|
| `GET /dreaming/status` | 返回 active run、latest run 和最近 published snapshot。 |
| `GET /dreaming/communities` | 返回最近快照的社区名称、报告、规模及成本。 |
| `POST /dreaming/runs` | 以默认或受限 Leiden 参数启动一次异步全量重建，成功预占后返回 `202`。 |

手动 Dreaming 当前只支持单进程 server；Gunicorn 模式会返回 `409`，直到跨 worker ownership 和调度器落地。

Balthasar WebUI 提供：

- **Members**：保留原语义图结构，以颜色区分社区，未归属节点使用中性色；
- **Communities**：把社区压缩为按成员数缩放的大节点，并显示相关节点；
- 右下角社区图例、节点/社区详情、社区名称与报告；
- 搜索栏下方的 Dreaming 入口、运行状态、阶段、成功/失败信息与 token 成本；
- 显式刷新。图数据在页面切换间缓存，不以状态轮询反复触发布局动画。

## 7. 失败语义与已验证不变量

当前测试覆盖的关键契约包括：

- 社区 ID 对 GDS 临时编号稳定，singleton 不发布；
- 一个 workspace 不能同时启动两次全量 Dreaming；
- GDS 或报告生成失败时不覆盖当前图快照；
- SQLite 最终发布失败时恢复上一份 Neo4j 快照；
- 非标准但可识别的报告 JSON 会被归一化，无效输出只重试一次；
- provider token 和估算 token 都能落库并经 API 返回；
- 服务重启能够清理中断的运行状态。

## 8. 下一步

v1 先解决“获得一份完整、稳定、可观察、可回滚的顶层社区视图”。下一阶段应分别设计，而不是塞进当前 runner：

1. 自动触发：定时、图变化阈值、空闲窗口、预算上限与失败退避；
2. 大社区覆盖：子社区只作为计算分片，维护连续 report/reduce 状态，并结合高重要度局部子图；
3. 增量报告：区分 provisional 变更、结构显著变化和必须全量重建的条件；
4. Evidence 血缘：让报告中的抽象结论可以下钻到 Atom 和原始证据；
5. 高层写回：为 `global.md`、`user.md`、LLM Wiki 等目标增加审核、版本、diff、回滚和权限边界。

其中第 5 项具有修改长期记忆的权限，必须建立独立的 production update management，不能把社区报告直接当成可写事实。
