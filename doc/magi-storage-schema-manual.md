# MAGI Memo 存储 Schema 手册

> 适用代码：当前仓库 `src/magi_core`；SQLite schema 版本：`10`
> 本文描述初始化完成后的实际 schema，包括迁移 DDL 之外由 `SQLiteBackend.initialize()` 自动补齐的字段。

## 1. 存储职责与数据流

MAGI Memo 使用两层存储，二者不是双主模型：

- **SQLite 是记忆事实、证据、身份、演化和投影任务的权威数据源（source of truth）**，也保存 Dreaming 的运行账本与历史派生快照。
- **Neo4j 是面向 LightRAG 查询的可重建检索投影**。节点和边的描述由 SQLite 中当前有效的 Atom 汇总生成；当前 Dreaming 社区归属与报告也作为派生服务投影保存在图中。
- 实体/关系向量库仍沿用 LightRAG；`memory_embeddings` 则是 MAGI 自己用于实体消歧与 Atom 判定的本地向量表，两者用途不同。

核心管线如下：

```text
文档 / 对话 / 已提取 payload
        │
        ▼
 episodes：登记 Episode，状态 pending
        │
        ▼
 实体与关系抽取、关系端点兜底
        │
        ├─ entity_registry / entity_aliases / entity_name_fts
        ├─ relation_registry
        └─ memory_embeddings（实体名与 Atom 判定）
        │
        ▼
 Atom 分类：duplicate / refinement / temporal_successor /
            contradiction / independent
        │
        ├─ atoms
        ├─ atom_evidence
        ├─ atom_evolution
        └─ projection_outbox（与 Atom 变更同一 SQLite 事务）
        │
        ▼
 owner_summary_checkpoints：增量压缩或全量重建摘要
        │
        ▼
 Neo4j + LightRAG VDB：投影 entity / relation + atom_ids
        │
        ▼
 outbox applied；Episode indexed（异常则 failed）
```

删除 Episode 时，系统先写 `memory_deletion_backups`，再撤销证据、清理失去证据的 Atom 和孤立 owner，并通过 outbox 删除或重建投影。恢复操作从备份回灌 SQLite 后再次投影。

Dreaming 不进入上述严格事实提交事务。它从 Neo4j 当前语义图计算社区，在 SQLite 准备一份完整快照，原子替换 Neo4j 当前社区投影后再将快照标记 published；社区报告始终是派生信息，不是 Atom/Evidence 事实源。

## 2. 通用约定

### 2.1 ID 与 workspace

- `episode_id`、`entity_id`、`relation_id`、`atom_id` 是全表主键；通常分别带 `episode-`、`entity-`、`relation-`、`atom-` 前缀。
- `relation_id` 根据 workspace 和排序后的两个稳定实体 ID 确定性生成，因此同一 workspace 的同一无向实体对只有一个 registry 关系。
- Atom ID 根据 workspace、Episode、owner 和抽取位置生成；`fingerprint` 是“语义指纹 + Atom ID”的 occurrence 唯一键，用于幂等保存同一 Atom，并不直接合并跨 Episode 事实。
- 绝大多数业务表直接存 `workspace_id`。`atom_evidence` 和 `atom_evolution` 通过 Atom/Episode 外键间接归属 workspace。
- Neo4j 的 workspace 由节点 label 隔离，不是 MAGI 新增的节点属性。

### 2.2 时间、JSON 与向量

- SQLite 时间字段均为 UTC、带时区的 ISO 8601 `TEXT`；Neo4j 的 LightRAG `created_at` 通常为 Unix 时间戳整数。
- 名称以 `_json` 结尾的字段保存 JSON 文本；读取时由 backend 反序列化。
- `memory_embeddings.vector` 是 float32 向量的二进制 `BLOB`，`dimensions` 用于校验维度。
- SQLite 启用 foreign keys、WAL、`synchronous=NORMAL` 和 30 秒 busy timeout。

### 2.3 三类 revision 不可混用

| 字段 | 所在表 | 实际含义 |
|---|---|---|
| `revision` | `entity_registry`、`relation_registry` | owner 记录的领域版本预留位；不是摘要调用次数，也不是投影任务序号。 |
| `target_revision` | `projection_outbox` | 某 owner 的投影脏版本。每次再次变脏就递增，用来避免旧 worker 把新任务错误标为 applied。 |
| `summary_revision` | `owner_summary_checkpoints` | 摘要 checkpoint 的 CAS 版本。每次 checkpoint 成功变更递增，包括 pending Atom 集合变化；**不等于 LLM merge 次数**。 |

真正的 LLM 调用次数在 `owner_summary_checkpoints.metrics_json` 的 `delta_llm_call_count` 和 `rebuild_llm_call_count` 中。

## 3. SQLite 表总览

| 表 | 类别 | 管线职责 |
|---|---|---|
| `schema_migrations` | 元数据 | 记录已应用 schema 版本。 |
| `episodes` | 输入账本 | 保存一次不可变输入及其处理状态。 |
| `entity_registry` | 稳定身份 | 实体稳定 ID、规范名、类型及最近投影物化状态。 |
| `entity_aliases` | 实体解析 | 一对多别名索引。 |
| `entity_name_fts` | 实体解析 | FTS5 名称候选搜索。 |
| `relation_registry` | 稳定身份 | 稳定实体对对应的关系 owner。 |
| `atoms` | 事实核心 | 保存最小事实单元、时间语义和生命周期。 |
| `atom_evidence` | 证据核心 | 将 Atom 绑定到 Episode 中的具体证据位置。 |
| `atom_evolution` | 演化图 | 保存 Atom 之间的修正、时序继任和矛盾。 |
| `projection_outbox` | 一致性 | 可靠驱动 SQLite → Graph/VDB 投影。 |
| `memory_embeddings` | 判定辅助 | 本地保存实体名和 Atom 的判定向量。 |
| `workspace_settings` | 兼容性护栏 | 保存 workspace 级运行模式。 |
| `memory_deletion_backups` | 可恢复删除 | 保存 Episode 删除前快照。 |
| `owner_summary_checkpoints` | 摘要状态 | 保存增量摘要 checkpoint、覆盖集合和指标。 |
| `dreaming_runs` | 离线作业账本 | 保存 Dreaming 状态、阶段、参数、规模、成本与错误。 |
| `dreaming_snapshots` | 派生快照 | 保存一次完整社区分区的算法元数据与发布状态。 |
| `dreaming_memberships` | 派生快照 | 保存快照内实体到社区的稳定归属。 |
| `dreaming_community_reports` | 派生摘要 | 保存社区名称、报告、规模和模型调用成本。 |

## 4. SQLite 字段手册

### 4.1 `schema_migrations`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `version` | `INTEGER PRIMARY KEY` | 已成功应用的迁移版本；当前最高为 `10`。 |
| `applied_at` | `TEXT NOT NULL` | 该版本首次登记的 UTC 时间。 |

初始化会重复执行幂等 DDL，并通过 `_ensure_column` 修复某些历史构建遗漏的增量列，因此判断实际 schema 不应只看本表版本。

### 4.2 `episodes`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `episode_id` | `TEXT PRIMARY KEY` | 一次输入的稳定标识；文档管线通常沿用文档 ID。 |
| `workspace_id` | `TEXT NOT NULL` | workspace 隔离键。 |
| `kind` | `TEXT NOT NULL` | 输入类型：`document`、`conversation`、`multimodal_text`。 |
| `content` | `TEXT NOT NULL` | Episode 的原始或规范化完整文本。 |
| `content_hash` | `TEXT NOT NULL` | `content` 的 SHA-256，用于完整性和同 ID 冲突检查。 |
| `reference_at` | `TEXT NOT NULL` | 该输入的语义参考时间；相对日期解析以此为锚点。 |
| `source_uri` | `TEXT` | 可选来源路径或 URI，用于溯源。 |
| `metadata_json` | `TEXT NOT NULL DEFAULT '{}'` | 输入元数据，例如文件信息、调用上下文。 |
| `status` | `TEXT NOT NULL` | `pending`、`indexed` 或 `failed`。 |
| `track_id` | `TEXT` | 与外层文档/处理任务关联的追踪 ID。 |
| `error` | `TEXT` | 最近一次失败原因；成功时为空。 |
| `created_at` | `TEXT NOT NULL` | Episode 首次登记时间。 |
| `updated_at` | `TEXT NOT NULL` | 状态、追踪信息或错误最后更新时间。 |

管线先将 Episode 置为 `pending`，SQLite 严格提交和投影完成后标为 `indexed`；异常标为 `failed`。Episode 是证据删除的入口，而不是摘要或图节点。

### 4.3 `entity_registry`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `entity_id` | `TEXT PRIMARY KEY` | MAGI 内部稳定实体 ID；投影为 Neo4j 节点的 `magi_entity_id`。 |
| `workspace_id` | `TEXT NOT NULL` | workspace 隔离键。 |
| `canonical_name` | `TEXT NOT NULL` | 规范显示名，也是当前 Neo4j 节点的 `entity_id`/查找键。 |
| `aliases_json` | `TEXT NOT NULL DEFAULT '[]'` | 别名集合快照；精确检索的规范化行在 `entity_aliases`。 |
| `entity_type` | `TEXT` | 抽取或合并后的实体类型；未知时可为空，投影时回退为 `UNKNOWN`。 |
| `revision` | `INTEGER NOT NULL DEFAULT 0` | 实体 registry 的领域版本；当前不是投影或摘要计数器。 |
| `created_at` | `TEXT NOT NULL` | 稳定实体首次建立时间。 |
| `expired_at` | `TEXT` | 实体逻辑过期时间；空表示 registry owner 仍有效。 |
| `normalized_name` | `TEXT` | 经 trim、空白折叠和 casefold 的规范名，用于实体解析。 |
| `atom_ids_json` | `TEXT NOT NULL DEFAULT '[]'` | 最近一次成功物化到 Graph/VDB 的 Atom ID 快照。 |
| `summary_cited` | `TEXT` | 最近一次成功投影的带 Atom 引用摘要。 |
| `summary_embedding_text` | `TEXT` | 摘要物化/embedding 用文本；当前与投影摘要保持一致。 |

实体解析先查别名/FTS 与名称 embedding；解析到既有实体则复用 `entity_id`，否则创建新 registry 记录。只存在于关系端点的实体也会在抽取归一化阶段合成容器，再进入相同解析流程。

### 4.4 `entity_aliases`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `workspace_id` | `TEXT NOT NULL`，联合主键 | workspace 隔离键。 |
| `entity_id` | `TEXT NOT NULL`，联合主键，FK | 指向 `entity_registry.entity_id`。 |
| `alias` | `TEXT NOT NULL` | 保留原始大小写和显示形式的别名。 |
| `normalized_alias` | `TEXT NOT NULL`，联合主键 | 规范化别名；同一别名允许指向多个候选实体。 |
| `created_at` | `TEXT NOT NULL` | 别名首次登记时间。 |

联合主键为 `(workspace_id, normalized_alias, entity_id)`。更新实体时，本表与 `entity_name_fts` 同步维护；候选排序还可结合关系端点上下文和向量相似度。

### 4.5 `entity_name_fts`

这是使用 `unicode61` tokenizer 的 FTS5 虚表。

| 字段 | 索引方式 | 含义与功能 |
|---|---|---|
| `workspace_id` | `UNINDEXED` | 结果过滤和归属信息。 |
| `entity_id` | `UNINDEXED` | 命中名称对应的稳定实体 ID。 |
| `name` | FTS5 全文索引 | 规范名或别名，用于 lexical candidate search。 |

该表是可重建搜索索引，不是实体真相源。应用应通过 backend 维护，不应直接编辑。

SQLite 还会自动创建以下 FTS5 影子表。为保证物理 schema 清单完整，字段一并列出；字段名和编码属于 SQLite FTS5 内部 ABI，不是 MAGI 可依赖的业务接口。

| 影子表 | 物理字段 | SQLite 内部用途 |
|---|---|---|
| `entity_name_fts_config` | `k`, `v` | FTS 配置键值。 |
| `entity_name_fts_content` | `id`, `c0`, `c1`, `c2` | 外部内容镜像；三列依次对应虚表的 workspace、entity 和 name。 |
| `entity_name_fts_data` | `id`, `block` | 序列化倒排索引块。 |
| `entity_name_fts_docsize` | `id`, `sz` | 文档列长度统计。 |
| `entity_name_fts_idx` | `segid`, `term`, `pgno` | term 到索引段页面的映射。 |

这些表完全由 SQLite 管理，不属于 MAGI 业务 schema，也不应直接查询或修改。

### 4.6 `relation_registry`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `relation_id` | `TEXT PRIMARY KEY` | MAGI 内部稳定关系 ID；投影为 Neo4j 关系的 `magi_relation_id`。 |
| `workspace_id` | `TEXT NOT NULL` | workspace 隔离键。 |
| `entity_a_id` | `TEXT NOT NULL` | 排序后第一个端点的稳定实体 ID。 |
| `entity_b_id` | `TEXT NOT NULL` | 排序后第二个端点的稳定实体 ID。 |
| `revision` | `INTEGER NOT NULL DEFAULT 0` | 关系 registry 的领域版本；不是投影或摘要计数器。 |
| `created_at` | `TEXT NOT NULL` | 关系 owner 首次建立时间。 |
| `expired_at` | `TEXT` | 关系 owner 的逻辑过期时间。 |
| `keywords_json` | `TEXT NOT NULL DEFAULT '[]'` | 合并后的关系关键词。 |
| `entity_a_name` | `TEXT` | A 端规范名的反规范化快照。删除 registry 后仍可据此定位旧图边。 |
| `entity_b_name` | `TEXT` | B 端规范名的反规范化快照。 |
| `atom_ids_json` | `TEXT NOT NULL DEFAULT '[]'` | 最近一次成功物化的关系 Atom ID 快照。 |
| `summary_cited` | `TEXT` | 最近一次成功投影的带引用关系摘要。 |
| `summary_embedding_text` | `TEXT` | 摘要物化/embedding 用文本。 |

另有唯一约束 `(workspace_id, entity_a_id, entity_b_id)`。关系按无向实体对注册；Neo4j 使用 `DIRECTED` 作为兼容 LightRAG 的关系类型名称，但当前 Cypher 以无方向模式 `-[r:DIRECTED]-` 合并和读取。

### 4.7 `atoms`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `atom_id` | `TEXT PRIMARY KEY` | 一个提取事实 occurrence 的稳定 ID。 |
| `workspace_id` | `TEXT NOT NULL` | workspace 隔离键。 |
| `kind` | `TEXT NOT NULL` | `entity_fact` 或 `relation_fact`。 |
| `owner_kind` | `TEXT NOT NULL` | `entity` 或 `relation`，决定投影目标。 |
| `owner_id` | `TEXT NOT NULL` | `entity_registry` 或 `relation_registry` 中的稳定 owner ID。 |
| `content` | `TEXT NOT NULL` | 规范化后的最小事实文本。 |
| `normalized_hash` | `TEXT NOT NULL` | 规范化事实文本的 SHA-256。 |
| `fingerprint` | `TEXT NOT NULL`，workspace 内唯一 | “semantic fingerprint + Atom ID”的 occurrence 指纹，确保同一 Atom 幂等且不同观察可并存；跨 Episode 重复由 Atom 判定流程识别。 |
| `subject_entity_id` | `TEXT` | 关系 Atom 的主语稳定实体 ID；实体 Atom 必须为空。 |
| `predicate` | `TEXT` | 可选结构化谓词。 |
| `object_entity_id` | `TEXT` | 关系 Atom 的宾语稳定实体 ID；实体 Atom 必须为空。 |
| `relation_keywords_json` | `TEXT NOT NULL DEFAULT '[]'` | 关系 Atom 的关键词。 |
| `valid_at` | `TEXT` | 事实在语义世界中开始有效的时间。 |
| `invalid_at` | `TEXT` | 事实在语义世界中结束有效的时间；可由继任事实补齐。 |
| `temporal_text` | `TEXT` | 原文中的时间表达，保留可解释性。 |
| `temporal_precision` | `TEXT` | 时间精度/来源，如 `exact`、`day`、`month`、`year`、`relative`、`inferred_current`。 |
| `created_at` | `TEXT NOT NULL` | Atom 首次写入系统的时间。 |
| `updated_at` | `TEXT` | 证据计数、时间边界或生命周期最近更新时间；旧库初始化时回填为 `created_at`。 |
| `expired_at` | `TEXT` | 系统级失效/被替代时间。不同于事实自身的 `invalid_at`。 |
| `confidence` | `REAL` | 可选抽取置信度，通常为 0–1。 |
| `importance` | `REAL` | 可选重要度，通常为 0–1。 |
| `support_count` | `INTEGER NOT NULL DEFAULT 0` | 支持该 Atom 的不同 Episode 数量。 |

生命周期判断以 `valid_at`、`invalid_at`、`expired_at` 共同决定。重复事实不创建第二个等价 Atom，而是追加 evidence 并重算 `support_count`；修正和时序继任可使旧 Atom 过期，矛盾是否替代旧事实由判定结果决定。

### 4.8 `atom_evidence`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `evidence_id` | `TEXT PRIMARY KEY` | 一条证据 occurrence 的稳定/确定性身份。 |
| `atom_id` | `TEXT NOT NULL`，FK | 指向 `atoms.atom_id`。 |
| `episode_id` | `TEXT NOT NULL`，FK | 指向提供证据的 `episodes.episode_id`。 |
| `quote` | `TEXT` | 支撑事实的原文摘录。 |
| `span_start` | `INTEGER NOT NULL DEFAULT -1` | 摘录在 Episode/片段中的起始字符位置；未知为 `-1`。 |
| `span_end` | `INTEGER NOT NULL DEFAULT -1` | 结束字符位置；未知为 `-1`。 |
| `extraction_revision` | `TEXT` | 抽取来源修订标识，文档管线通常写来源 chunk ID。 |
| `created_at` | `TEXT NOT NULL` | 证据登记时间。 |

v5 起使用独立 `evidence_id`，允许同一 Atom、Episode 中存在多处证据。它是事实可追溯性和 Episode 删除影响计算的权威表。

### 4.9 `atom_evolution`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `source_atom_id` | `TEXT NOT NULL`，联合主键，FK | 新写入的 Atom。 |
| `target_atom_id` | `TEXT NOT NULL`，联合主键，FK | 与之匹配的历史 Atom。 |
| `relation_type` | `TEXT NOT NULL`，联合主键 | `REFINEMENT`、`TEMPORAL_SUCCESSOR` 或 `CONTRADICTION`。 |
| `created_at` | `TEXT NOT NULL` | 演化边建立时间。 |
| `metadata_json` | `TEXT NOT NULL DEFAULT '{}'` | 判定置信度、理由等审计信息。 |

方向固定为“新 Atom → 历史 Atom”。`duplicate` 和 `independent` 不写演化边；前者只追加 evidence，后者创建互不依赖的 Atom。

### 4.10 `projection_outbox`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `operation_id` | `TEXT PRIMARY KEY` | workspace + owner 的确定性任务 ID；每个 owner 保持一条 sticky row。 |
| `workspace_id` | `TEXT NOT NULL` | workspace 隔离键。 |
| `episode_id` | `TEXT`，FK | 触发任务的 Episode，主要用于诊断；删除 Episode 前会解除该关联。 |
| `owner_ids_json` | `TEXT NOT NULL` | 旧版兼容字段；当前任务通常只含一个 owner。 |
| `target_revision` | `INTEGER NOT NULL` | owner 最新脏版本；重复排队时递增。 |
| `status` | `TEXT NOT NULL` | `pending`、`failed` 或 `applied`。 |
| `attempts` | `INTEGER NOT NULL DEFAULT 0` | 当前任务版本的投影尝试次数。 |
| `last_error` | `TEXT` | 最近错误，写入时截断到 4000 字符。 |
| `created_at` | `TEXT NOT NULL` | 该 sticky task 首次创建时间。 |
| `applied_at` | `TEXT` | 当前 revision 成功落到 Graph/VDB 的时间。 |
| `owner_id` | `TEXT` | 当前可执行任务的实体或关系稳定 ID；旧行可空，初始化会删除不可执行旧行。 |
| `owner_kind` | `TEXT` | `entity` 或 `relation`。 |
| `owner_snapshot_json` | `TEXT NOT NULL DEFAULT '{}'` | 删除投影所需定位快照：实体规范名或关系两端名称。 |
| `updated_at` | `TEXT` | 任务最近排队、失败或成功时间。 |
| `next_attempt_at` | `TEXT` | 失败后的下次可重试时间；指数退避上限 60 秒。 |

Atom/owner 变化与 outbox 排队在同一 SQLite 事务中提交。worker 先投影实体再投影关系，Graph 和 VDB 均刷新后，仅在数据库中 `target_revision` 仍等于自己处理的版本时标记 `applied`；否则保留更新后的任务继续处理。启动时会恢复 `pending`/`failed` 任务。

### 4.11 `memory_embeddings`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `workspace_id` | `TEXT NOT NULL`，联合主键 | workspace 隔离键。 |
| `object_kind` | `TEXT NOT NULL`，联合主键 | `entity_name` 或 `atom`。 |
| `object_id` | `TEXT NOT NULL`，联合主键 | 被嵌入对象 ID；别名可使用独立对象 ID。 |
| `owner_id` | `TEXT` | 对象归属：实体名称向量指向稳定实体 ID，Atom 向量指向 Atom owner。 |
| `model_name` | `TEXT NOT NULL`，联合主键 | embedding 模型身份，避免混用不同向量空间。 |
| `dimensions` | `INTEGER NOT NULL` | 向量维度校验。 |
| `vector` | `BLOB NOT NULL` | float32 原始向量字节。 |
| `source_text` | `TEXT NOT NULL` | 生成该向量的文本。 |
| `content_hash` | `TEXT NOT NULL` | `source_text` 的 SHA-256，用于缓存命中/失效。 |
| `updated_at` | `TEXT NOT NULL` | 向量最后更新时间。 |

实体解析会对规范名/别名做 lexical + semantic 候选搜索；Atom 判定只在相关 owner 范围内搜索历史 Atom。此表不参与 LightRAG 的实体/关系/chunk 检索。

### 4.12 `workspace_settings`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `workspace_id` | `TEXT NOT NULL`，联合主键 | workspace 隔离键。 |
| `setting_key` | `TEXT NOT NULL`，联合主键 | 配置名。 |
| `setting_value` | `TEXT NOT NULL` | 配置值。 |
| `updated_at` | `TEXT NOT NULL` | 最后更新时间。 |

当前关键项是 `knowledge_commit_mode=magi_strict`。它用于阻止在已有未注册图数据的 workspace 上直接启用严格 MAGI 提交，避免 LightRAG 直写数据与 MAGI 权威账本混用。

### 4.13 `memory_deletion_backups`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `backup_id` | `TEXT PRIMARY KEY` | 一次删除快照 ID。 |
| `workspace_id` | `TEXT NOT NULL` | workspace 隔离键。 |
| `episode_id` | `TEXT NOT NULL` | 被删除的 Episode。 |
| `payload_json` | `TEXT NOT NULL` | Episode、受影响实体/别名/关系、孤立 Atom、证据、演化边及 embedding 的恢复快照。 |
| `created_at` | `TEXT NOT NULL` | 备份创建时间。 |
| `restored_at` | `TEXT` | 成功恢复时间；同时作为幂等恢复标记。 |

删除只移除因该 Episode 消失而不再有任何 evidence 的 Atom；仍由其他 Episode 支持的 Atom会保留并更新 `support_count`。恢复后相关 owner 会重新进入 outbox。

### 4.14 `owner_summary_checkpoints`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `workspace_id` | `TEXT NOT NULL`，联合主键 | workspace 隔离键。 |
| `owner_id` | `TEXT NOT NULL`，联合主键 | 实体或关系稳定 ID。 |
| `checkpoint_summary` | `TEXT NOT NULL DEFAULT ''` | 最近一次已压缩、带 Atom 引用的 owner 摘要。 |
| `covered_atom_ids_json` | `TEXT NOT NULL DEFAULT '[]'` | 当前 checkpoint 已覆盖的 Atom 集合。 |
| `pending_atom_ids_json` | `TEXT NOT NULL DEFAULT '[]'` | checkpoint 尚未合并、但投影时仍需直接附加的 Atom 集合。 |
| `summary_revision` | `INTEGER NOT NULL DEFAULT 0` | checkpoint CAS 版本；任何成功状态变更都会递增。 |
| `prompt_version` | `TEXT NOT NULL` | 生成 checkpoint 使用的摘要 prompt 版本。 |
| `model_identity` | `TEXT NOT NULL` | 摘要模型/配置身份；改变时触发全量重建。 |
| `incremental_compaction_count` | `INTEGER NOT NULL DEFAULT 0` | 自上次全量重建以来连续增量压缩次数；全量重建后归零。 |
| `last_compacted_at` | `TEXT` | 最近真正执行摘要压缩的时间。 |
| `last_reason` | `TEXT` | 最近状态变化或重建原因。 |
| `metrics_json` | `TEXT NOT NULL DEFAULT '{}'` | 压缩次数、LLM 调用、token 节省和 lineage 校验指标。 |
| `updated_at` | `TEXT NOT NULL` | checkpoint 最近更新时间。 |

常见 `last_reason` 包括：

- `rebuild:missing_checkpoint`：首次创建或 checkpoint 缺失。
- `rebuild:summary_identity_changed`：prompt/model 身份变化。
- `rebuild:covered_atom_removed`：已覆盖 Atom 被删除，必须全量重建。
- `pending_delta_updated`：只更新 pending 集合，尚未压缩。
- `delta_threshold`：pending 达阈值，执行增量压缩。
- `periodic_full_rebuild`：增量达到周期上限，执行全量重建。
- lineage 校验失败也会设置重建原因和 `rebuild_required`。

`metrics_json` 当前使用的键：

| 键 | 含义 |
|---|---|
| `delta_compaction_count` | 成功执行增量压缩的累计次数。 |
| `delta_llm_call_count` | 增量压缩中实际调用 LLM 的累计次数。 |
| `rebuild_compaction_count` | 成功执行全量重建的累计次数。 |
| `rebuild_llm_call_count` | 全量重建中实际调用 LLM 的累计次数。 |
| `last_input_tokens` | 最近一次压缩输入 token 数。 |
| `total_input_tokens` | 累计压缩输入 token 数。 |
| `last_saved_tokens` | 最近一次增量相对全量输入估算节省的 token；重建为 0。 |
| `total_saved_tokens` | 累计估算节省 token。 |
| `last_lineage_valid` | 最近一次摘要中的 Atom 引用是否通过 lineage 校验。 |
| `rebuild_required` | 是否已标记下一次必须全量重建。 |

摘要 checkpoint 只是派生状态，不是 evidence。该表只存在于 SQLite，不再向 Neo4j 写任何 `magi_summary_*` 属性。

### 4.15 `dreaming_runs`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `run_id` | `TEXT PRIMARY KEY` | 一次 Dreaming 作业 ID。 |
| `workspace_id` | `TEXT NOT NULL` | workspace 隔离键；部分唯一索引保证每个 workspace 最多一个 running run。 |
| `status` | `TEXT NOT NULL` | `running`、`succeeded` 或 `failed`。 |
| `phase` | `TEXT NOT NULL` | `queued`、`projecting`、`reporting`、`staging`、`publishing`、`complete` 或 `failed`。 |
| `config_json` | `TEXT NOT NULL DEFAULT '{}'` | Leiden 的 seed、gamma、theta、max levels 和 concurrency。 |
| `snapshot_id` | `TEXT` | 本次作业准备/发布的快照。 |
| `node_count` / `relationship_count` / `community_count` | `INTEGER NOT NULL DEFAULT 0` | 输入图规模及过滤 singleton 后的社区数量。 |
| `report_count` | `INTEGER NOT NULL DEFAULT 0` | 成功生成的社区报告数。 |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | `INTEGER NOT NULL DEFAULT 0` | 本次作业聚合 token 使用。 |
| `llm_call_count` | `INTEGER NOT NULL DEFAULT 0` | 报告生成总调用次数，包含校验重试。 |
| `token_usage_source` | `TEXT` | `provider`、`estimated` 或 `mixed`。 |
| `error` | `TEXT` | 失败原因，最多保留 4000 字符。 |
| `created_at` / `started_at` / `finished_at` | `TEXT` | UTC 作业生命周期时间。 |

初始化时遗留的 `running` 作业会被标记为因进程重启而失败，避免永久占据唯一运行槽。

### 4.16 `dreaming_snapshots`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `snapshot_id` | `TEXT PRIMARY KEY` | 完整社区快照 ID。 |
| `workspace_id` | `TEXT NOT NULL` | workspace 隔离键。 |
| `run_id` | `TEXT NOT NULL UNIQUE`，外键 | 产生该快照的 Dreaming run。 |
| `status` | `TEXT NOT NULL` | `prepared`、`published` 或 `failed`。读取当前分区时只选择 published。 |
| `algorithm` / `algorithm_version` | `TEXT` | 当前为 `leiden` 及实际 GDS 版本。 |
| `config_json` | `TEXT NOT NULL DEFAULT '{}'` | 本次算法配置。 |
| 图规模、社区数、报告数及 token 字段 | 数值 | 与对应 run 一致的快照级审计指标。 |
| `created_at` / `published_at` | `TEXT` | 准备和最终发布的 UTC 时间。 |

### 4.17 `dreaming_memberships`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `snapshot_id` | `TEXT`，联合主键/外键 | 所属完整快照，删除快照时级联删除。 |
| `entity_id` | `TEXT`，联合主键 | Neo4j 语义实体的 `entity_id`。 |
| `community_id` | `TEXT NOT NULL` | 由完整成员集合哈希得到的稳定社区 ID。 |
| `membership_status` | `TEXT NOT NULL DEFAULT 'stable'` | 全量快照当前只写 `stable`；在线 provisional 不回写历史快照。 |

单节点分区不会写入本表。

### 4.18 `dreaming_community_reports`

| 字段 | 类型/约束 | 含义与功能 |
|---|---|---|
| `snapshot_id` / `community_id` | `TEXT`，联合主键 | 报告所属快照和社区。 |
| `community_name` | `TEXT NOT NULL` | `dream` 角色模型生成的简短主题名。 |
| `report` | `TEXT NOT NULL` | 基于社区实体与内部关系生成的综合报告。 |
| `member_count` | `INTEGER NOT NULL DEFAULT 0` | 社区完整成员数量。 |
| token 与调用字段 | 数值/文本 | 该社区报告的 prompt、completion、total、调用数及 usage 来源。 |

该表由 migration 10 引入。读取旧快照而 SQLite 尚无报告时，API 可以从 Neo4j 当前 `DreamCommunity` 节点回填，兼容早期 Neo4j-only 报告。

## 5. SQLite 索引与约束

### 5.1 显式索引

| 索引 | 列 | 用途 |
|---|---|---|
| `idx_episodes_workspace_created` | `workspace_id, created_at` | workspace Episode 时序扫描。 |
| `idx_entity_registry_workspace_name` | `workspace_id, canonical_name` | 规范名查找。 |
| `idx_entity_aliases_entity` | `workspace_id, entity_id` | 按实体回收/更新别名。 |
| `idx_atoms_owner` | `workspace_id, owner_kind, owner_id` | 汇总和投影某 owner 的 Atom。 |
| `idx_atoms_valid_time` | `workspace_id, valid_at, invalid_at` | 时态过滤。 |
| `idx_atom_evidence_atom` | `atom_id` | 计算支持证据和删除影响。 |
| `idx_atom_evidence_episode` | `episode_id` | Episode 删除/恢复。 |
| `idx_projection_outbox_pending` | `workspace_id, status, created_at` | 拉取待处理任务。 |
| `idx_projection_outbox_owner` | `workspace_id, owner_id` | owner 任务去重与更新。 |
| `idx_projection_outbox_retry` | `workspace_id, status, next_attempt_at, updated_at` | 失败任务退避重试。 |
| `idx_memory_embeddings_owner` | `workspace_id, object_kind, owner_id, model_name` | owner 内语义候选搜索。 |
| `idx_memory_deletion_backups_episode` | `workspace_id, episode_id, created_at` | 查找 Episode 删除快照。 |

此外，SQLite 会为主键和 `relation_registry(workspace_id, entity_a_id, entity_b_id)`、`atoms(workspace_id, fingerprint)` 等唯一约束创建自动索引。

### 5.2 外键边界

显式外键存在于：

- `entity_aliases.entity_id → entity_registry.entity_id`
- `atom_evidence.atom_id → atoms.atom_id`
- `atom_evidence.episode_id → episodes.episode_id`
- `atom_evolution.source_atom_id/target_atom_id → atoms.atom_id`
- `projection_outbox.episode_id → episodes.episode_id`

`relation_registry` 的端点、`atoms.owner_id`、`memory_embeddings.owner_id` 和 summary checkpoint owner 由应用层维护，而非 SQLite FK。这样允许删除过程先保留定位快照、按 owner 聚合清理，并处理实体/关系两种多态 owner。

## 6. Neo4j Schema：LightRAG 基线与 MAGI 差异

### 6.1 图结构

- 节点 label：由 workspace 名生成的隔离 label，沿用 LightRAG Neo4j backend。
- 节点匹配键：`entity_id`，其值是实体规范名，不是 SQLite `entity_registry.entity_id`。
- 关系类型：`DIRECTED`，沿用 backend；当前以无方向 Cypher pattern 合并同一实体对。
- Neo4j 不承担 Episode、Evidence、Atom 演化、outbox 或摘要 checkpoint 的权威存储。

### 6.2 实体节点属性

| 属性 | 来源 | 含义 |
|---|---|---|
| `entity_id` | LightRAG 基线 | 节点查找键/规范实体名。 |
| `magi_entity_id` | **MAGI 新增** | SQLite `entity_registry.entity_id` 的稳定回链键；实体改名后仍可定位同一个 registry owner。 |
| `entity_type` | LightRAG 基线 | 实体类型。 |
| `description` | 基线字段，MAGI 改变生成语义 | 当前有效 Atom 生成的带 `[atom-id]` 引用摘要；pending Atom 可直接附加，保证 lineage。 |
| `source_id` | LightRAG 基线 | 支持当前 owner 的来源 chunk/extraction revision，使用图字段分隔符拼接。 |
| `file_path` | LightRAG 基线 | 支持 evidence 对应 Episode 的来源路径集合。 |
| `created_at` | LightRAG 基线/历史数据可有 | LightRAG 写入时间戳；MAGI 更新现有节点时会保留已有属性。新 MAGI 投影不依赖它。 |
| `truncate` | LightRAG 基线/历史数据可有 | LightRAG 重建描述时的截断说明；MAGI 更新现有节点时会保留已有属性。 |
| `atom_ids` | **MAGI 新增** | 当前投影所包含的实体 Atom ID 列表，用于从检索结果回溯 SQLite 事实与证据。 |

### 6.3 关系属性

| 属性 | 来源 | 含义 |
|---|---|---|
| `src_id` | LightRAG 基线 | 关系源端规范名；端点本身也由图结构表达。 |
| `tgt_id` | LightRAG 基线 | 关系目标端规范名。MAGI 在投影前对两端名称排序以稳定方向。 |
| `magi_relation_id` | **MAGI 新增** | SQLite `relation_registry.relation_id` 的稳定回链键。 |
| `description` | 基线字段，MAGI 改变生成语义 | 当前有效关系 Atom 生成的带引用摘要。 |
| `keywords` | LightRAG 基线 | `relation_registry.keywords_json` 以图字段分隔符拼接后的字符串。 |
| `weight` | LightRAG 基线 | LightRAG 关系权重；MAGI 当前保留已有值，缺省为 `1.0`。 |
| `source_id` | LightRAG 基线 | 支持该 owner 的来源 chunk/extraction revision 集合。 |
| `file_path` | LightRAG 基线 | 支持 evidence 的来源路径集合。 |
| `truncate` | LightRAG 基线/历史数据可有 | LightRAG 描述重建截断说明；MAGI 更新现有边时会保留已有属性。 |
| `atom_ids` | **MAGI 新增** | 当前投影所包含的关系 Atom ID 列表。 |

因此，相比原始 LightRAG，当前持久化到 Neo4j 的 MAGI 新增字段为：

```text
实体节点：
  magi_entity_id: TEXT
  atom_ids: LIST<TEXT>

关系边：
  magi_relation_id: TEXT
  atom_ids: LIST<TEXT>
```

`description`、`source_id`、`file_path` 等字段不是新增 schema，但其内容现在由 SQLite Atom/Evidence 管线驱动，而不再只是对 chunk 抽取描述做直接合并。

### 6.4 Dreaming 派生投影

全量 Dreaming 发布时会在一个 Neo4j 事务内先清除旧归属，再写入当前快照：

| 实体节点属性 | 含义 |
|---|---|
| `dream_community_id` | 当前稳定或临时社区 ID。 |
| `dream_community_name` | 当前社区报告生成的名称。 |
| `dream_membership_status` | 全量发布为 `stable`；新节点最近社区归属为 `provisional`。 |
| `dream_snapshot_id` | 归属所基于的已发布快照。 |
| `dream_published_at` | 全量归属发布时间；provisional 节点可不含该字段。 |

同一事务还会替换 workspace 的 `DreamCommunity` 节点。这些节点保存 `workspace_id`、`snapshot_id`、`community_id`、`community_name`、`report`、`member_count` 及 token/call 指标。它们是当前查询投影，不构成新的语义事实节点，也不连接进 `DIRECTED` 实体关系网。

普通 `/graphs` 响应默认把上述内部字段收敛为 `community_id` 与 `community_name`；Balthasar 内部视图可显式请求完整 Dreaming 属性。

### 6.5 明确不会写入 Neo4j 的内部字段

以下字段曾用于实验、调试或管线内部状态，当前均不应出现在新投影中：

| 字段/字段族 | 当前归属 |
|---|---|
| `magi_endpoint_only` | 抽取归一化阶段的瞬时标记，表示实体容器由关系端点兜底合成；不落 SQLite schema，也不落 Neo4j。 |
| `magi_atom_order` | 合并/排序阶段的瞬时输入，不落库。 |
| `magi_summary_revision` | 改由 SQLite checkpoint `summary_revision` 管理。 |
| `magi_summary_compaction_count` | 改由 checkpoint 计数与 `metrics_json` 管理。 |
| `magi_summary_covered_atoms` / `magi_summary_pending_atoms` | 改由 `covered_atom_ids_json` / `pending_atom_ids_json` 管理。 |
| `magi_summary_last_compacted_at` / `magi_summary_last_reason` | 改由 checkpoint 同名语义字段管理。 |
| `magi_summary_last_input_tokens` / `magi_summary_total_input_tokens` | 改由 `metrics_json` 管理。 |
| `magi_summary_last_saved_tokens` / `magi_summary_total_saved_tokens` | 改由 `metrics_json` 管理。 |
| `magi_summary_last_lineage_valid` / `magi_summary_rebuild_required` | 改由 `metrics_json` 管理。 |
| `magi_summary_rebuild_compaction_count` 等调用/压缩计数 | 改由 `metrics_json` 管理。 |

如果旧 Neo4j 数据中仍有上述属性，它们是历史残留，不代表当前 schema；可用以下审计查询检查：

```cypher
MATCH (n)
WITH n, [key IN keys(n) WHERE key STARTS WITH 'magi_'] AS ks
WHERE size(ks) > 0
RETURN labels(n), n.entity_id, ks;

MATCH ()-[r]-()
WITH r, [key IN keys(r) WHERE key STARTS WITH 'magi_'] AS ks
WHERE size(ks) > 0
RETURN type(r), ks;
```

## 7. 各管线阶段读写矩阵

| 阶段 | 主要读取 | 主要写入 |
|---|---|---|
| Episode 登记 | `episodes`（同 ID 校验） | `episodes.status=pending` |
| 抽取归一化 | Episode 内容/抽取 payload | 暂无；关系缺端点时内存中合成实体容器 |
| 实体解析 | `entity_aliases`、`entity_name_fts`、`memory_embeddings`、关系上下文 | `entity_registry`、aliases、FTS、实体名 embedding |
| 关系注册 | entity registry | `relation_registry` |
| Atom 判定 | owner 历史 `atoms`、Atom embeddings | `atoms`、`memory_embeddings(object_kind=atom)` |
| 证据提交 | Episode、Atom | `atom_evidence`、`support_count` |
| 演化应用 | 新旧 Atom | `atom_evolution`、旧 Atom 的 `invalid_at`/`expired_at` |
| 投影排队 | owner 当前状态 | `projection_outbox` |
| 摘要物化 | 当前有效 Atom、checkpoint | `owner_summary_checkpoints` |
| Graph/VDB 投影 | registries、Atoms、Evidence、checkpoint、outbox | Neo4j、LightRAG VDB；registry 物化快照；outbox applied |
| Episode 完成 | Episode | `episodes.status=indexed/failed` |
| 删除 | Episode、Evidence、相关 owner/Atom | deletion backup、SQLite 清理、outbox 删除/重建任务 |
| 恢复 | deletion backup | 回灌 SQLite、`restored_at`、outbox 重建任务 |
| 查询 | Neo4j + LightRAG VDB | 通常只读；`atom_ids` 可回溯 SQLite 事实和证据 |
| Dreaming 聚类/报告 | Neo4j 当前实体关系图 | GDS 临时图；SQLite run、prepared snapshot、memberships、reports |
| Dreaming 发布 | SQLite prepared/上一份 published 快照 | Neo4j 当前归属与 `DreamCommunity`；SQLite snapshot published/run succeeded |
| 新节点临时归属 | Neo4j 1–3 跳已归属邻居 | Neo4j 节点 `provisional` 属性；不改 SQLite 历史快照 |

## 8. 运维与排障原则

1. **不要把 Neo4j 当主数据修复。** 图可由 SQLite registry、Atom、Evidence 和 outbox 重建；直接改图不会反向更新 SQLite。
2. **不要直接编辑 FTS5 影子表。** 名称索引应通过实体 registry/alias backend 重建。
3. **不要把 `summary_revision` 当 LLM 次数。** 查看 `metrics_json` 中两个 `*_llm_call_count`。
4. **不要把 `target_revision` 当业务 revision。** 它只解决投影 worker 并发和重放一致性。
5. **`invalid_at` 与 `expired_at` 含义不同。** 前者描述事实在现实世界何时失效，后者描述系统何时将该 Atom 淘汰/替代。
6. **检查图谱血缘时以 `atom_ids` 为入口。** Atom 内容、时间状态和引用证据都应回 SQLite 查询。
7. **模型漏给实体 schema 不应导致关系丢失。** 当前管线会为只出现在关系中的端点创建空实体容器，随后走正常实体解析和投影；`magi_endpoint_only` 只用于本轮归一化控制。
8. **Dreaming 报告不是事实源。** 修正事实应回到 Episode/Atom/Evidence 管线；不要直接把 `DreamCommunity.report` 反写成 Atom。
9. **不要把 provisional 当全量裁决。** 它只根据 1–3 跳最近已发布社区提供在线视觉连续性，下一次 Leiden 会重新计算。

## 9. 实现位置

- SQLite 迁移：`src/magi_core/backend/sqlite/migrations.py`
- SQLite 初始化、自愈列、CRUD、删除/恢复：`src/magi_core/backend/sqlite/backend.py`
- Episode/Entity/Relation/Atom 模型和稳定 ID：`src/magi_core/memory/models.py`
- Atom 判定：`src/magi_core/memory/decisions.py`
- 严格提交、摘要 checkpoint、outbox 和 Graph/VDB 投影：`src/magi_core/memory/adapter.py`
- 抽取归一化、LightRAG 基线实体/关系字段：`src/magi_core/operate.py`
- Neo4j MERGE 和属性写入：`src/magi_core/kg/neo4j_impl.py`
- Dreaming 编排、报告校验与补偿发布：`src/magi_core/memory/dreaming.py`
- Dreaming REST 控制面：`src/magi_core/api/routers/dreaming_routes.py`
