# MAGI Memo 二期第一阶段：Memory Core 设计基线

状态：实施前基线（方向 B 已确定）
日期：2026-08-04

## 1. 阶段结论

二期第一阶段的目标不是继续扩充 LightRAG 的字符串 `description` 合并逻辑，而是建立一层以
`Episode → Atom → Entity / Relation materialization` 为核心的可增量记忆提交链路。

本阶段确定采用“方向 B + 混合实现”：

- `magi-core` 持有 Episode、Atom、双时间、实体消歧、Atom 判定、Reflect 等领域语义；
- LightRAG 继续承担 chunk 处理、模型调用基础设施、实体/关系抽取、向量检索和兼容查询；
- 在 LightRAG 的“抽取完成、默认按名称合并之前”增加窄扩展点；
- Atom 位于独立的记录存储中；Neo4j 对 Atom 只保存 `atom_ids: list[str]` 引用，同时保留 LightRAG 原有的
  实体/关系语义属性和由 Atom 重建的文本投影；
- LightRAG 的实体 `description` 和关系 `description` 改为由 Atom 生成的语义物化结果；它们仍参与检索和图探索，
  但不再作为不可逆的唯一事实源。

暂不在本阶段处理 runtime、API、实例句柄、初始化/销毁和完整生命周期工程。这些属于二期下一阶段。

## 2. 存储方向决策

“实体节点和关系边内嵌 Atom 列表”的领域表达是合理的，但不能直接作为所有存储后端的物理结构。

### 2.1 Neo4j 限制

Neo4j 节点和关系属性可以保存简单值和同类型简单值列表，不能把 `list[map]` 作为属性持久化。
因此完整的结构化 Atom 对象列表不能直接写成：

```text
(Entity { atoms: [{id, content, valid_at, ...}, ...] })
```

将 Atom JSON 序列化成字符串虽然可以写入，但会失去：

- Atom 级索引和向量搜索；
- 按 `valid_at` / `invalid_at` 的时间过滤；
- Atom 级更新、失效、来源追踪和引用；
- 可控的局部读取和更新能力。

### 2.2 当前 LightRAG 限制

当前 LightRAG：

- 实体以规范化后的名称作为图节点键；
- 关系端点排序后按无向实体对合并；
- 同一实体/关系的描述先精确去重，再拼接或交给 LLM 总结；
- Neo4j 的 `DIRECTED` 关系实际使用无向 `MERGE` 模式；
- 图节点/边的 `created_at` 在每次合并写入时都会重新赋值，不能直接充当稳定的系统创建时间；
- 关系 `weight` 是抽取贡献累计，更接近“出现/支持次数”，不是语义连接强度。

所以，Atom 若只是替换 `description` 字段，会继续受到名称键合并、无向关系、字符串总结和来源上限的约束。

### 2.3 方向评估与最终决策

#### 方向 A：Atom 作为超边节点

Atom 节点化只有在 Atom 被重新定义成可连接任意数量实体的超边时才足够自然：

```text
(Atom)-[:PARTICIPANT {role}]->(Entity)
```

这会把图变成 Entity / Atom 二部图，能够表达零元、一元、二元和多元事件，但代价是：

- Entity 到 Entity 的探索从一跳变成至少两跳；
- 现有 LightRAG 的实体边检索与邻居扩展不能直接复用；
- 一个 n 元 Atom 若物化为实体两两投影，最坏产生 `n × (n - 1) / 2` 条边；
- 若 Atom 不保存 text，就必须把否定、数量、时间、条件和角色全部本体化，抽取和 schema 成本显著上升；
- “超边 Atom”实际上更接近 Event / Relation Instance，未必能覆盖所有文本原子记忆。

该方向仅保留为未来的事件图研究项，不进入二期第一阶段实施计划。

#### 方向 B：Atom 独立记录层，图只保存 ID 列表（已采用）

二期第一阶段后续设计与实现统一以该方向为基线：

- Atom 存入独立的关系型或 KV repository；一期提供 SQLite reference backend；
- Entity 节点和 LightRAG 关系边以同类型字符串列表 `atom_ids` 关联 Atom，不内嵌 Atom 结构；
- Relation 仍是带端点、`keywords`、`description` 和兼容 `weight` 的 LightRAG 语义边，不是 Atom ID 的容器边；
- `description` 是从 Atom 内容生成的物化文本投影；
- Atom 的时间、分数、来源和演化关系从 repository 批量读取；
- Neo4j 继续承载 LightRAG 的实体—关系语义图及其检索投影，但不承担 Atom 记录存储。

这样避免了伪节点和图结构污染，同时保留现有 LightRAG 检索路径。代价是 Atom-aware 查询多一次存储读取，并且
Neo4j 与 Atom repository 之间需要可恢复的一致性协议。

## 3. 推荐的逻辑模型与分层物理模型

### 3.1 Episode

Episode 表示一次需要被记忆的原始输入，是不可变的来源记录。

```text
Episode
  id                  UUID / ULID
  workspace_id        隔离域
  kind                document_chunk | conversation_window | multimodal_text
  content             原始或 filtered 内容
  content_hash        幂等和精确去重
  reference_at        该输入发生或被叙述时的参考时间
  created_at          系统接收时间
  source_uri          文档、会话或外部来源
  metadata            可扩展元数据
```

约束：

- 文档 chunk 可以直接成为一个 Episode；
- 对话先在外部完成 raw accumulation / filter，再作为一个 Episode 提交；
- Episode 本身不因后续解析结果变化而被修改；重新处理产生新的 processing revision，但保留同一来源身份或显式版本关系。

### 3.2 Entity

```text
Entity
  id                  稳定 UUID，不使用名称作为领域主键
  canonical_name      当前规范名
  aliases             名称候选和历史别名
  entity_type
  summary_cited       Reflect 生成，包含 [atomId]
  summary_embedding_text
                      不含引用标记、用于生成 embedding 的派生文本
  summary_version
  created_at
  expired_at
```

LightRAG 兼容投影在第一阶段仍可使用 `canonical_name` 作为其 `entity_name`，但 `magi-core` 内部的关系改名、
引用和合并必须使用稳定 `entity_id`。后续若修改 LightRAG 的主键模型，不影响 Atom 的领域身份。

### 3.3 Relation

一期的 Relation 沿用 LightRAG 的语义边模型：它不只是邻接关系，而是对两个实体之间一组关系事实的语义聚合。

```text
Relation
  id                  稳定 pair-level relation_id
  workspace_id
  entity_a_id         排序后的稳定实体 ID
  entity_b_id         排序后的稳定实体 ID
  atom_ids            该实体对的完整历史 relation Atom ID
  active_atom_count
  keywords            当前有效关系的关键词/关系类别聚合
  description_cited   当前 active Atom 拼接或 Reflect summary
  description_embedding_text
  weight              LightRAG 兼容字段，不解释为语义紧密度
  atom_revision
  created_at
  expired_at
```

Relation 的整体语义由端点、`keywords` 和 active relation Atom 生成的 `description` 共同表达，所以现有 LightRAG
关系检索和图探索仍然成立。当前 LightRAG 以无向实体对聚合关系，因此 pair-level Relation 可以同时容纳多种关系陈述；
每条陈述的精确方向、predicate、双时间和来源保留在 relation Atom 中。需要精确核验时再按 `atom_ids` 下钻，
但不能把这种分层理解成 Relation 本身没有语义。

### 3.4 Atom

Atom 是最小的可判断、可引用、可失效记忆单位。

```text
Atom
  id                  UUID / ULID
  workspace_id
  kind                entity_fact | relation_fact
  owner_kind          entity | relation
  owner_id            稳定 entity_id 或 relation_id
  content             自包含、规范化的原子陈述
  normalized_hash     仅对规范化 content 计算
  fingerprint         owner、内容和双时间的精确幂等键
  subject_entity_id   relation_fact 必填
  predicate           可为空，但建议抽取
  object_entity_id    relation_fact 必填
  relation_keywords   relation_fact 的关键词/关系类别，可为空
  valid_at            事实在现实世界开始有效
  invalid_at          事实在现实世界停止有效
  temporal_text       解析前的原始时间表达，可为空
  created_at          系统首次创建该 Atom
  expired_at          系统停止把该 Atom 作为当前版本使用
  confidence          对事实正确性的置信度
  importance          对记忆价值的权重
  support_count       不同 Episode 支持数的物化计数
  status              由时间字段派生，不作为独立事实源
```

不要把 `confidence`、`importance`、`support_count` 重新合并成 LightRAG 的单个 `weight`。三者语义不同；
LightRAG 旧 `weight` 只保留为兼容字段。

### 3.5 Evidence

一个 Atom 可被多个 Episode 支持，所以来源不应只放一个 `episode_id`。

```text
AtomEvidence
  atom_id
  episode_id
  quote
  span_start
  span_end
  extraction_revision
  created_at
```

精确重复 Atom 不创建第二份事实，只增加 `AtomEvidence` 记录并更新 `support_count`。
`AtomEvidence` 至少以 `(atom_id, episode_id, span_start, span_end)` 幂等；`support_count` 统计不同 `episode_id`，
不能因为同一 Episode 中多次提及而重复加权。

### 3.6 Atom Repository

接口层不绑定 SQLite：

```text
AtomRepository
  put_episode(...)
  put_atom(...)
  add_evidence(...)
  get_atoms(atom_ids)
  list_atoms_by_owner(owner_kind, owner_id, time_filter)
  find_exact(fingerprint)
  apply_resolution(...)
```

Atom 的持久化与检索索引分开抽象：

```text
AtomSearchIndex
  upsert_atoms(atoms)
  delete_atoms(atom_ids)
  full_text_search(query, filter, top_k)
  vector_search(query, filter, top_k)
```

SQLite 通过 FTS5 提供实体名全文索引，并使用统一的 `memory_embeddings` 表持久化实体别名和 Atom 向量；
两类向量以 `object_kind` 分层，共用一套轻量余弦召回实现，不再分别创建数据库或 vec 表。
`AtomRepository` 是事实源，`AtomSearchIndex` 与图上的 description 一样都允许从 repository 重建。

SQLite 原型建议至少包含：

```text
episodes
atoms
atom_evidence
atom_evolution       // REFINES / CONTRADICTS / SUCCEEDS
projection_outbox    // 跨存储恢复
```

`atoms` 中保留 `owner_kind` 和 `owner_id`：

- entity Atom 的 owner 是稳定 `entity_id`；
- relation Atom 的 owner 是稳定 `relation_id`；
- relation Atom 同时保存有方向的 `subject_entity_id`、`predicate`、`object_entity_id`；
- SQLite 与 Neo4j 之间不做物理外键，完整性由 commit service 和 reconciler 校验。

第一阶段兼容当前 LightRAG“一对实体只有一条聚合边”的模型，因此：

```text
relation_id = hash(workspace_id, min(entity_a_id, entity_b_id), max(entity_a_id, entity_b_id))
```

方向和 predicate 保留在各个 relation Atom 中，不能从这个 pair-level `relation_id` 推断。未来若允许同一实体对存在多条
图关系，再把 predicate 或 relation type 纳入 relation ID，不需要迁移 Atom ID。

SQLite 使用 WAL、唯一 fingerprint 约束和显式事务即可满足单机第一阶段实验。多进程高并发或远程部署时可替换成
PostgreSQL，而不改变领域接口。

### 3.7 Neo4j / LightRAG 图投影

实体节点：

```text
(entity {
  entity_id,            // LightRAG 兼容键
  magi_entity_id,       // MAGI 稳定 ID
  atom_ids: [String],   // 完整历史 Atom ID；非事实源
  atom_revision,
  active_atom_count,
  description           // 当前有效 Atom 的物化文本投影
})
```

关系边：

```text
(source)-[:DIRECTED {
  magi_relation_id,
  atom_ids: [String],
  atom_revision,
  active_atom_count,
  keywords,             // active relation Atom 的语义关键词聚合
  description,          // active relation Atom 的语义描述物化
  weight,               // LightRAG 兼容的支持/出现权重，不是语义距离
  source_id,            // 兼容字段，可由 AtomEvidence / Episode 派生
  file_path,            // 兼容字段，可由 Episode 派生
  created_at,
  expired_at
}]->(target)
```

这里的关系边不是仅用于连通两个实体的空壳。`keywords`、`description` 和两个端点共同构成 LightRAG 可直接检索、
排序和探索的关系语义；关系 VDB 仍可使用 `keywords + endpoints + description` 生成内容。方向 B 改变的是这些语义的
来源与可追溯性：边上的语义由 active relation Atom 物化而来，Atom 的原子内容、双时间和证据仍保存在独立记录层。

`atom_ids` 使用 Neo4j 支持的同类型字符串列表，但它仍是反向索引/投影，不是权威归属关系。Atom repository 中的
`owner_id` 才是事实源。这样即使列表写入中断，也可以按 owner 重建。

这是 MAGI 第一阶段唯一支持的 Neo4j projection profile；`atom_ids` 直接使用 Neo4j 原生同类型字符串列表。
其他图后端不进入支持范围，也不为其增加序列化兼容分支。

`description` 的生成规则：

- active Atom 总量较小时，按稳定顺序拼接 Atom content；
- 超过 token 阈值时使用 Reflect summary；
- 展示文本可以保留 `[atomId]`；
- 写入 embedding 的文本来自结构化结果中的纯文本字段，不通过宽泛正则删除引用；
- `atom_revision` 和 Atom 集合 hash 用于判断图投影是否陈旧。

`atom_ids` 保存完整历史归属，Atom 失效时不从列表删除；`active_atom_count` 和 `description` 只根据指定系统时间与
现实时间均有效的 Atom 生成。这样双时间变化只需要更新 repository 和重建文本投影，不会破坏历史追溯。

### 3.8 ID 列表的规模边界

该方案比 Atom 节点轻，但 `atom_ids` 不是零成本：大实体更新时需要重写整个列表，LightRAG `get_node()` 也会取回
完整属性。

第一阶段保留完整 ID 列表以换取实现简单，并记录以下指标：

- 单实体/关系最大 Atom 数；
- `atom_ids` 序列化字节数；
- 每次追加的 Neo4j 写入耗时；
- 图查询中无意取回 ID 列表的额外流量。

若达到阈值，后续可以把图上列表降级为 `recent_atom_ids + atom_count + atom_revision`，完整列表仍通过
`AtomRepository.list_atoms_by_owner()` 获得。这个优化不改变 Atom 的领域模型。

## 4. 一阶段写入流水线

```text
Episode 接收与幂等
  → 实体/关系/Atom 联合抽取
  → 时间解析与 UTC 标准化
  → 本轮实体解析与消歧
  → 按 entity_id 重写本轮关系端点
  → Atom 精确去重
  → Atom 候选召回与 LLM 判定
  → 在 Atom repository 提交 Episode / Atom / Evidence / outbox
  → 更新 AtomSearchIndex
  → 更新 Neo4j atom_ids、description 和 LightRAG 实体/关系向量索引
  → 标记 outbox 已投影
  → 标记 Reflect dirty scope
```

当前 LightRAG 已经有一个合适的切入缝：`_process_extract_entities()` 返回 `chunk_results` 后，才进入
`merge_nodes_and_edges()`。一期应在这两步之间插入 MAGI 的 resolve / commit 阶段，而不是等默认字符串合并
完成后再反向拆解。

### 4.1 跨存储一致性

SQLite 和 Neo4j 不能共享事务，因此采用 Atom-first + outbox：

1. 在一个 Atom repository 事务中提交 Atom、Evidence、状态迁移和 `projection_outbox`；
2. 幂等更新 AtomSearchIndex；
3. 再按 owner 更新 Neo4j 的 `atom_ids`、`atom_revision`、`description`；
4. 更新对应 LightRAG 实体/关系 VDB 文本；
5. 全部成功后把 outbox 标记为 applied；
6. 同步写入接口只有在第 5 步后才返回成功。

这个顺序保证不会出现“图引用了根本不存在的 Atom”。若进程在中途失败，最多出现 repository 已提交、图投影滞后；
reconciler 可根据 outbox 或 `owner_id` 幂等重放。图上的 ID 列表和 description 都必须允许被整体重建。

一期不需要先建设完整 runtime，但 commit service 必须提供：

- `replay_projection(operation_id)`；
- `reconcile_owner(owner_id)`；
- `find_dangling_graph_atom_ids()`；
- `find_unprojected_atoms()`。

### 4.2 两种读取路径

兼容查询：

- LightRAG 直接读取 materialized `description`，不增加 Atom repository 往返；
- 适合现有 local/global/hybrid/mix 路径；
- 默认只表达当前有效记忆。

Atom-aware 查询：

- 图召回一批实体/关系后，收集所有 `atom_ids`；
- 一次批量调用 `get_atoms(ids)`，禁止逐节点 N+1 查询；
- 在 repository 层完成双时间过滤、分数排序和 evidence 展开；
- 按输入 ID 顺序重组结果。

历史时间点查询需要 Atom-aware 路径。仅依赖当前 `description` 的 LightRAG 兼容路径不能回答完整历史状态。

## 5. 实体消歧

### 5.1 候选生成

对每个本轮新实体执行：

1. 当前 Episode 内先按规范化名称精确合并；
2. 在同一 workspace 内执行名称向量搜索；
3. 同时执行名称/alias 全文搜索；
4. 两路结果取并集，不取交集；
5. 按精确 alias、全文分数、向量分数、类型兼容性排序，限制候选数；
6. 将当前 Episode 和少量近期 Episode 与候选一起交给 LLM；
7. LLM 只能选择一个现有 `entity_id` 或返回 `NO_MATCH`。

一期采纳用户给出的前提：现有 Entity 集合彼此不需要再次合并。LLM 不允许返回多个候选，也不在现有实体间
创建“待合并”标记边。该 beta 能力明确推迟。

### 5.2 规范名与关系端点改写

若匹配到现有实体：

- 现有实体 ID 和规范名优先；
- 新名称作为 alias 或 mention 保存；
- 不自动重命名已有实体；
- 建立 `extracted_local_id → canonical_entity_id` 映射。

关系改名不能用字符串查找替换。抽取输出必须让关系端点引用本轮实体的 local ID，然后通过上面的 ID 映射一次性
重写 `subject_entity_id` 和 `object_entity_id`。这样实体消歧后，关系合并与关系消歧会自然使用规范端点。

LightRAG 兼容层需要负责 `canonical_entity_id ↔ entity_name` 转换：领域层和 Atom repository 始终使用稳定 ID，
只有调用现有 LightRAG 图接口时才使用当前规范名。Neo4j 应为 `magi_entity_id` 建唯一约束或至少建立索引，不能依赖
名称反查稳定 ID。

### 5.3 关系归一与关系消歧

实体消歧完成后，关系按以下顺序处理：

1. 用 canonical entity ID 重写本轮所有端点；
2. 计算 pair-level `relation_id`；
3. 将相同实体对的关系陈述归入同一条 LightRAG 语义 Relation；
4. 每条原子关系陈述保存为 relation Atom，保留方向、predicate、keywords、内容和双时间；
5. relation Atom 的重复、接替和冲突继续使用第 6 节的统一判定流程；
6. 从该 Relation 的 active Atom 聚合 `keywords`，拼接或总结 `description`；
7. 用 `keywords + canonical endpoints + description` 重建关系 VDB 文本，并按兼容策略物化 `weight`。

因此，一期仍然保留“关系合并”这一语义操作，只是不再把它实现为不可追溯的 description 字符串累计：端点歧义由
实体消歧解决，关系陈述之间的重复、细化、时序接替和冲突由 Atom 判定解决，判定后的 active Atom 再物化成一条
有 `keywords` 和 `description` 的 LightRAG 语义边。无需额外发明一套独立的“关系名称实体消歧”，但绝不能因此把
Relation 降格为只有实体对拓扑的索引边。

### 5.4 与 Graphiti 的对照

Graphiti 当前的实体候选生成使用名称 embedding 的 cosine search，候选上限 15、最低分 0.6；随后再让 LLM 在候选
和 Episode 上下文中选择或拒绝重复。MAGI 可以沿用“候选召回不是最终判定”这一原则，但增加全文/alias 并集，
对中文简称、译名、缩写和低相似别名会更稳健。

## 6. Atom 去重、冲突和时间演化

### 6.1 语义相似度不能作为唯一入口

只从高向量相似 Atom 中找候选不可靠。以下内容可能相互重要，但 embedding 不一定足够近：

- 同一属性的不同值；
- 否定和肯定；
- “已离职”和“就职于”；
- 同一关系在不同时间段的变化；
- 数字、版本、地点等局部变化。

反过来，语义很相似也不代表重复，例如用户明确提供了新的 `invalid_at`。

所以向量搜索只负责压缩候选空间，不能负责排除所有潜在冲突。

### 6.2 推荐的分层候选策略

每个新 Atom 按以下优先级组装候选包：

1. **精确层**：同 owner / 同端点、同规范化内容、同有效时间窗口；
2. **结构层**：同 entity + attribute key，或同 subject + predicate + object；
3. **时间层**：同结构范围内所有 active Atom，以及与新 Atom 时间窗口重叠的 Atom；
4. **检索层**：BM25/全文与 embedding 两路 top-k 的并集；
5. **近期层**：同 owner 最近变更的少量 Atom；
6. **摘要层**：只在原始 Atom 超出 token 预算时提供带 Atom ID 的摘要作为导航信息。

若同一实体或关系的 active Atom 总量低于 token 预算，直接把全部 active Atom 交给 LLM，通常比过早过滤更可靠。
超过预算后再使用上述候选包，而不是只给 summary。Summary 会损失细节，不能成为去重或冲突判定的唯一证据。

### 6.3 无模型快速路径

最频繁的完全重复应在 LLM 前处理：

```text
fingerprint = hash(
  workspace_id,
  kind,
  owner / subject / predicate / object,
  normalized_content,
  normalized_valid_at,
  normalized_invalid_at
)
```

fingerprint 完全一致时：

- 不调用 LLM；
- 不创建新 Atom；
- 增加 Episode evidence；
- 去重 evidence 后更新 `support_count`。

若 content 相同但 `valid_at` / `invalid_at` 不同，不能走快速重复路径，必须进入时间判定。

### 6.4 LLM 判定枚举

一期保留以下五类，但把分类结果和持久化动作定义清楚。判定接口以一次 Episode 写入为边界：
先按已消歧的 owner 组装每个新 Atom 的允许候选，再把本轮全部请求、去重后的候选表、owner summary
和近期 Episode 放入同一次结构化 LLM 调用。模型返回每个新 Atom 的操作计划，代码验证目标确实属于
该 Atom 的候选集合后再执行：

| 判定                   | 含义                           | 默认动作                                                                       |
| ---------------------- | ------------------------------ | ------------------------------------------------------------------------------ |
| `DUPLICATE`          | 同一事实的重复表达             | 复用旧 Atom，追加 evidence                                                     |
| `REFINEMENT`         | 新事实增加细节但不推翻核心事实 | 新建 Atom，建立`REFINES`；旧 Atom 设置 `expired_at`，不设置 `invalid_at` |
| `TEMPORAL_SUCCESSOR` | 同一槽位在更晚时间产生新状态   | 新建 Atom；按时间设置旧 Atom 的`invalid_at`，并设置系统 `expired_at`       |
| `CONTRADICTION`      | 同一时间范围内不兼容           | 新建 Atom 和`CONTRADICTS`；只有来源权威且时间顺序明确时才自动失效旧 Atom     |
| `INDEPENDENT`        | 无需合并或失效                 | 正常插入                                                                       |

`REFINEMENT` 与 `TEMPORAL_SUCCESSOR` 都不应原地改写旧 Atom 内容，否则来源和历史查询会被破坏。

`atom_evolution` 是独立关系表，不是 Atom 行中的可变字段。当前统一使用“新 Atom 为 source、被比较的
历史 Atom 为 target”的方向：

```text
new Atom --REFINEMENT / TEMPORAL_SUCCESSOR / CONTRADICTION--> old Atom
```

`expired_at` 由提交代码决定，不由 LLM 自由生成：REFINEMENT 和 TEMPORAL_SUCCESSOR 会使旧 Atom
退出当前系统投影；CONTRADICTION 默认同时保留两者，只有明确纠正/撤回且来源更权威时才允许旧 Atom
退出当前投影。`invalid_at` 只记录现实世界中事实停止成立的时间；REFINEMENT 不设置它，时间接替通常
使用新 Atom 的 `valid_at` 作为旧 Atom 的 `invalid_at`。

### 6.5 Graphiti 的实际做法

Graphiti 的事实边与 MAGI 的 relation Atom 最接近。其当前写入逻辑：

- 同端点候选先用 hybrid RRF 检索；
- 另做一次更广的 hybrid 检索寻找可能需要失效的事实；
- 若端点和规范化 fact 文本完全一致，直接复用并追加 Episode，跳过 LLM；
- 否则由 LLM 同时选择 duplicate facts 和 contradicted facts；
- 再根据 `valid_at` / `invalid_at` 的时间区间决定旧事实是否失效。

这说明“精确快速路径 + 结构约束候选 + hybrid 检索 + LLM 最终判断”是可行方向，也说明仅靠向量相似度并不是
Graphiti 的完整策略。

## 7. 双时间系统

统一使用带时区的 UTC 时间，并采用半开区间：

```text
现实有效区间：[valid_at, invalid_at)
系统记录区间：[created_at, expired_at)
```

- `valid_at`：模型根据 Episode 的 `reference_at` 和用户文本推断事实开始有效的时间；
- `invalid_at`：事实在现实世界中停止成立的时间；未知为 `null`；
- `created_at`：Atom 第一次提交到系统的时间，创建后不可修改；
- `expired_at`：该 Atom 不再作为当前系统版本参与默认召回的时间；未知为 `null`。

`invalid_at` 表示“世界变了”，`expired_at` 表示“系统对记录的当前性判断变了”。两者不能互相替代。

时间解析建议从实体/关系结构抽取中拆开，作为独立标准化步骤：

1. 抽取模型返回原始时间表达和可选 ISO 值；
2. 时间解析器以 Episode `reference_at` 为基准处理“昨天、下个月、已经”等表达；
3. 统一成 UTC；
4. 校验 `invalid_at > valid_at`；
5. 解析失败保留 `null` 和原始表达，不编造精确时间。

Graphiti 最新版本也已把 `valid_at` / `invalid_at` 的解析拆成结构抽取后的独立步骤，值得直接参考这一职责分离。

## 8. Reflect

一期的 Reflect 是可调用接口和后台能力，不涉及 runtime 调度器。

### 8.1 实体与关系总结

触发条件建议同时支持：

- active Atom 数超过阈值；
- active Atom token 总量超过阈值；
- Atom 集合发生变化，scope 被标记为 dirty；
- 手动指定实体、关系或 workspace 范围。

输出：

```text
summary_cited          包含 [atomId]
summary_embedding_text 从结构化引用段生成，不用宽泛正则删除方括号
summary_version
summary_atom_set_hash  用于幂等和判断是否需要重算
generated_at
```

只使用当前查询时间点有效的 Atom 生成默认 summary；历史 summary 通过时间参数另行生成。每条关键陈述至少保留一个
`[atomId]`，必要时可引用多个 Atom。

### 8.2 Community 与 Community Report

第一阶段可支持一个 `CommunityDetector` 接口：

- `Neo4jGdsDetector`：Neo4j GDS 可用时使用 Leiden 或 Louvain；
- `InProcessDetector`：测试和无插件环境的回退实现；
- 输入必须显式限制 workspace / group / 时间范围；
- 从 Atom repository 读取当前有效 relation Atom，再投影成 entity-to-entity 的 GDS 临时图；
- 重复 evidence 不应自动生成多条图关系。

当前 LightRAG 的 edge `weight` 不应作为 GDS 的 `relationshipWeightProperty`。它表示历史抽取贡献累计，重复写入越多
权重越大，会把“被重复提及”错误解释成“语义连接更紧密”。

一期建议先跑**无权图**：Neo4j Louvain/Leiden 在未指定 `relationshipWeightProperty` 时会按无权图执行。随后用固定
样本对比以下投影，再决定是否引入新的 `semantic_weight`：

1. 每个 canonical entity pair 一条无权边；
2. 每个不同 relation predicate 一条边；
3. 使用 `confidence × importance` 的显式语义权重；
4. 加入有限、封顶后的独立 Episode 支持度。

Community Report 同样从 Atom 生成并保留 `[atomId]`，而不是只总结已有实体 `description`。

注意：APOC 和 GDS 是两类插件。当前 Core 测评只确认 APOC 缺失并发生遍历回退，不能据此推断 GDS 已安装。

## 9. Magi Core 与 LightRAG 的边界

### 9.1 Magi Core 负责

- Episode / Atom / Entity / Evidence 领域模型；
- AtomRepository、AtomSearchIndex、SQLite 实现和 repository migration；
- 稳定 ID 和幂等 key；
- 实体候选召回与 LLM resolution policy；
- 本轮 local entity ID 到 canonical entity ID 的映射；
- Atom 快速去重、候选组装和五类判定；
- 双时间标准化和状态迁移；
- projection outbox、owner reconcile 和批量 Atom 读取；
- Reflect summary、community 和 report；
- 将事实源投影为 LightRAG 可消费的实体/关系描述。

### 9.2 LightRAG 负责

- 既有解析、chunk、LLM/embedding provider、缓存和限流基础设施；
- 初始实体/关系抽取；
- 既有向量和全文检索能力；
- local/global/hybrid/mix 等兼容查询；
- 作为可重建 projection 的实体/关系图、`atom_ids`、description 和 VDB。

### 9.3 建议的 LightRAG 内部改动

一期只增加窄接口，避免把 MAGI 领域逻辑散落进 `operate.py`：

1. 在 `_process_extract_entities()` 与 `merge_nodes_and_edges()` 之间增加 resolver/committer hook；
2. 抽取记录增加本轮 local entity ID，使关系端点不依赖名称字符串；
3. 允许 hook 返回 canonicalized chunk results 或直接声明默认 merge 已被接管；
4. 允许实体和关系安全携带并更新 `atom_ids`、`atom_revision`、`magi_*_id` 标量属性；
5. 为实体/关系 description 提供 projection writer，而不是继续执行旧的描述累计逻辑；
6. 修正 MAGI 路径下 `created_at` 每次 upsert 被覆盖的问题；
7. 保留原 LightRAG 默认行为，未启用 MAGI hook 时不改变兼容性。

一期不建议直接把整个提交逻辑塞进 `LightRAG` 类，也不建议完全在写入结束后调用 `amerge_entities()` 补救。后者会产生
多余向量写入、关系重建和短暂的不一致状态。

### 9.4 方向 B 一致性复核

| 设计项                         | 在方向 B 中的位置                                         | 复核结论                                                                                                 |
| ------------------------------ | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Episode                        | Atom repository 的不可变来源记录                          | 匹配；不需要图节点化                                                                                     |
| Entity                         | Neo4j 实体节点，附加稳定`magi_entity_id`                | 匹配；仍是图探索的基本节点                                                                               |
| Relation                       | Neo4j pair-level 语义聚合边，附加稳定`magi_relation_id` | 匹配当前 LightRAG；保留 endpoints、keywords、description 和兼容 weight，精确方向/predicate/时间留在 Atom |
| Atom                           | repository 独立记录，按 owner 归属                        | 匹配；不污染图结构                                                                                       |
| Evidence                       | `atom_evidence` 多对多记录                              | 匹配；重复事实只增加来源                                                                                 |
| Entity resolution              | Atom 确定 owner 前完成                                    | 匹配；避免事后迁移大量 Atom                                                                              |
| Relation endpoint rewrite      | 用 stable entity ID 映射完成                              | 匹配；不做名称字符串替换                                                                                 |
| Relation disambiguation        | 端点消歧 + relation Atom 判定 + Relation 语义物化         | 匹配；关系合并仍存在，但不再是不可追溯的字符串累计                                                       |
| Exact dedup                    | repository 唯一 fingerprint                               | 匹配；可无模型完成                                                                                       |
| Semantic dedup / contradiction | owner 范围 + AtomSearchIndex + LLM                        | 匹配；候选和事实记录均不依赖 Neo4j Atom 节点                                                             |
| Bi-temporal state              | Atom repository 字段与查询条件                            | 匹配；图只保留当前文本投影和历史 ID 引用                                                                 |
| Description                    | active Atom 拼接或 Reflect summary                        | 匹配；兼容 LightRAG，但不是事实源                                                                        |
| Reflect                        | 批量读取 owner Atom，写回 summary projection              | 匹配；引用通过 Atom ID 保留                                                                              |
| Community                      | Neo4j 拓扑或从 active relation Atom 构造临时投影          | 匹配；不使用旧 extraction weight                                                                         |
| Recovery                       | Atom-first outbox + owner reconcile                       | 必需；解决 SQLite/Neo4j/VDB 无跨库事务问题                                                               |

复核结论：结构体、实体消歧、关系归一、Atom 去重、双时间和 Reflect 都可以在方向 B 下闭合，不需要任何
Atom-as-node 假设。当前唯一明确的工程代价是跨存储投影一致性和大 `atom_ids` 列表的规模上限，两者均已纳入
一期接口和验收指标。

## 10. 本阶段明确不做

- runtime worker、任务调度和长期运行策略；
- 对外 REST/API schema；
- 实例句柄、池化、多租户实例生命周期；
- 初始化、销毁、热切换和 provider 生命周期；
- 自动合并两个已经存在的 canonical Entity；
- 待合并标记边和 Reflect 中的实体物理合并；
- 复杂本体、跨 workspace 实体共享；
- 把社区结果直接用于最终生产检索排序；
- 用 LightRAG 历史 `weight` 作为社区算法语义权重。

## 11. 纵向实施切片

正式开工按 A → B → C → D 顺序进行。首先冻结领域 dataclass/Pydantic model、repository protocol、SQLite migration
和 projection contract，再改 LightRAG 写入缝；在存储契约稳定前不先实现 LLM prompt，避免 prompt 输出反向绑死
领域模型。

### Slice A：领域模型与物理存储

- Episode / Atom / Evidence SQLite schema 与 migration；
- AtomRepository / AtomSearchIndex 接口、SQLite WAL、FTS5、唯一 fingerprint 约束；
- Neo4j `magi_entity_id` / `magi_relation_id` / `atom_ids` / revision 投影；
- Atom 内容向量索引与全文索引；
- 双时间范围查询；
- LightRAG projection 与事实源分离。

验收：可以插入一个 Episode，得到带来源和双时间的 Atom；Neo4j 只出现 Atom ID，不出现 Atom 节点；可以按 entity、
relation、时间和 episode 反查。

### Slice B：实体消歧与关系端点规范化

- 名称精确、alias 全文、embedding 候选并集；
- LLM 单选或 `NO_MATCH`；
- 近期 Episode 上下文；
- ID 映射后再生成关系 Atom。

验收：译名、简称、同名不同人和新实体四类固定样本可重复得到预期结果；本轮所有关系端点均使用 canonical ID。

### Slice C：Atom 判定

- fingerprint 快速重复；
- 结构/时间/hybrid 候选包；
- 五类判定与状态迁移；
- evidence 追加和 `support_count`；
- 并发提交的幂等约束；
- Atom-first outbox、owner reconcile 和故障重放。

验收：完全重复不调用 LLM；重复、细化、时序接替、冲突、独立五类均有固定测试；显式 `invalid_at` 不被误删。

### Slice D：Reflect 基础能力

- entity/relation summary with `[atomId]`；
- embedding 文本与引用文本分离；
- dirty scope 和幂等 hash；
- 指定隔离范围的 community + report；
- 无权图基准，以及不同权重方案对比报告。

验收：summary 每条关键事实可追溯 Atom；Atom 变化后可定向重算；无 GDS 时有明确失败或可测试回退。

## 12. 一阶段完成定义

以下条件全部满足才算第一阶段完成：

- Episode 和 Atom 已成为真实持久化对象，而不是 `description` 中的约定文本；
- Atom 不作为 Neo4j 节点；Neo4j 对 Atom 只保存稳定 ID 列表，实体和 Relation 仍保留自身语义属性及可重建投影；
- Atom 具备稳定 ID、来源、双时间、置信度/重要度/支持度；
- 新实体在写入时完成候选召回和单目标消歧；
- 关系端点通过 ID 映射自动规范化，不依赖字符串改名；
- Relation 保留 LightRAG 的语义边能力；其 `keywords`、`description` 和 VDB 文本可由 active relation Atom 重建，
  精确方向、predicate、双时间和证据可从 relation Atom 无损恢复；
- 完全重复 Atom 在无 LLM 路径中被折叠；
- 非精确重复使用结构、时间、全文、向量的候选并集和 LLM 最终判定；
- Entity/Relation summary 带 Atom 引用，embedding 文本不含引用；
- LightRAG 兼容查询不额外读取 Atom；Atom-aware 查询对一批图结果只做批量 repository 读取；
- 模拟 repository 已提交而 Neo4j 写入失败后，可以从 outbox 幂等恢复；
- AtomSearchIndex、图 `atom_ids` 和 description 均可从 AtomRepository 全量重建；
- 可以对指定范围构建 community 并生成带 Atom 引用的 report；
- 默认 LightRAG 查询仍可工作，且旧行为在未启用 MAGI 时不回归；
- 整个纵向切片有离线 mock 测试，不依赖真实 LLM 或 Neo4j 才能跑单元测试。

## 13. 需要实验决定而不是提前锁死的参数

- Episode 对话窗口大小和 filter 策略；
- entity candidate top-k、cosine 阈值和全文召回上限；
- Atom “全部打包”切换到候选包的 token 阈值；
- 图上完整 `atom_ids` 列表切换为 recent IDs + owner 查询的规模阈值；
- `REFINEMENT` 是否总是设置旧 Atom `expired_at`；
- 模糊 contradiction 是否允许两个 Atom 同时 active；
- summary 触发阈值和刷新频率；
- community 使用 Leiden 还是 Louvain；
- community 是否使用无权 pair、predicate 多边或显式 `semantic_weight`。

这些都应进入可配置实验参数和固定评测集，不应成为第一版硬编码的领域真理。

## 14. 参考

- [Graphiti README：Episode、事实边、双时间与增量图](https://github.com/getzep/graphiti)
- [Graphiti entity resolution：名称向量候选与 LLM resolve](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/node_operations.py)
- [Graphiti edge resolution：hybrid candidates、精确快速路径、duplicate/contradiction](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/edge_operations.py)
- [Graphiti EntityEdge 数据模型](https://github.com/getzep/graphiti/blob/main/graphiti_core/edges.py)
- [Graphiti community：label propagation 与 community summary](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/community_operations.py)
- [Neo4j 可持久化属性类型](https://neo4j.com/docs/cypher-manual/current/values-and-types/property-structural-constructed/)
- [Neo4j Louvain：无权和显式 relationship weight](https://neo4j.com/docs/graph-data-science/current/algorithms/louvain/)
- [Neo4j GDS graph projection](https://neo4j.com/docs/graph-data-science/current/management-ops/graph-creation/graph-project/)
