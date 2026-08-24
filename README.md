# MAGI Memo

> 面向 Agent 的可演化图谱记忆系统。

MAGI Memo 是一个以知识图谱为主要组织与检索结构、面向 Agent 长期记忆场景的图谱记忆系统。它从 LightRAG 的图谱检索内核演化而来，在 Neo4j 语义图之下增加独立的 Episode、Atom、Evidence、实体注册表和双时间记录层，使图中的实体与关系不只是一次抽取产生的静态描述，而是能够持续写入、消歧、演化、追溯和重新物化的长期记忆。

图谱是 MAGI Memo 的主体：Entity 与 Relation 构成可检索、可遍历的语义网络；Episode、Atom 和 Evidence 是让这张图能够长期维护的底层记忆机制。普通 GraphRAG 回答“哪些节点和关系与问题相关”，MAGI Memo 还要记录“这些图谱事实来自哪里、是否是同一实体、何时成立、后来如何变化，以及当前投影为什么采用这个版本”。

项目从 LightRAG 的检索内核出发，已经完成两次关键演进：

1. 从文档 RAG 演进为可持续维护的图谱记忆系统；
2. 从单一服务演进为可被 Agent Harness 调用的记忆基础设施。

当前主线是 DeepSeek Harness（DSH）接入、受预算约束的主动图谱检索，以及 Reflect / 社区摘要研究。未来的 MAGI Sys 才负责人格化的 CASPER、MELCHIOR、BALTHASAR、Blackboard、MQP、MCC 与通用多 Agent 协商。

> [!IMPORTANT]
> MAGI Memo 当前处于 Beta / research preview。记忆内核、Runtime、REST API、WebUI 和 DSH 插件已经可用；主动检索 v1 正在验收，Reflect、多 Explorer 并发和通用 MCP 入口仍在演进。

## 快速开始

### 1. 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- 一个 LLM 与 embedding provider；默认示例使用 OpenAI-compatible API
- Neo4j（当前完整图谱记忆主路径）
- Bun（仅修改或重新构建 WebUI 时需要）

### 2. 安装

```bash
git clone <your-magi-memo-repository>
cd MAGI-Memo
uv sync --frozen --extra api --extra offline-storage --extra offline-llm
```

### 3. 配置

```bash
cp env.example .env
```

至少检查以下配置：

```dotenv
HOST=127.0.0.1
PORT=3491
WORKSPACE_HOME=./mgc-test

LLM_BINDING=openai
LLM_BINDING_HOST=https://api.openai.com/v1
LLM_BINDING_API_KEY=your_api_key
LLM_MODEL=your_model

EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=https://api.openai.com/v1
EMBEDDING_BINDING_API_KEY=your_api_key
EMBEDDING_MODEL=your_embedding_model
EMBEDDING_DIM=your_embedding_dimension

NEO4J_URI=neo4j://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

角色级 LLM 可以用 `EXTRACT_*`、`RESOLVE_*`、`DEDUPLICATE_*`、`KEYWORD_*`、`QUERY_*`、`VLM_*` 单独覆盖。完整变量及注释以 [`env.example`](./env.example) 为准。

> [!WARNING]
> 未配置 `AUTH_ACCOUNTS` 或 `LIGHTRAG_API_KEY` 时，服务没有身份验证。仅本机使用请保持 `HOST=127.0.0.1`；暴露到网络前必须配置认证，并检查 `WHITELIST_PATHS`。

### 4. 启动服务与 WebUI

```bash
./server
```

默认入口：

- WebUI：[http://127.0.0.1:3491/](http://127.0.0.1:3491/)
- 健康检查：[http://127.0.0.1:3491/health](http://127.0.0.1:3491/health)
- OpenAPI：[http://127.0.0.1:3491/docs](http://127.0.0.1:3491/docs)

也可以使用安装后的入口：

```bash
magi-core-server
# 或生产多进程入口
magi-core-gunicorn
```

修改 WebUI 后重新构建：

```bash
cd src/webui
bun install --frozen-lockfile
bun run build:bun
```

## 为什么图谱还需要记忆语义

普通向量 RAG 擅长回答“哪段文本与问题相似”，GraphRAG 进一步回答“哪些实体和关系连接了这些信息”。但一张长期增长的记忆图谱还必须回答：

- 两个相似名称是否真的是同一个实体？
- 这条信息来自哪一次经历？
- 多次出现的是同一事实、补充、后续状态，还是冲突？
- 一个事实在现实世界中何时有效，在系统中又何时被记录或淘汰？
- 删除一段经历后，哪些事实仍有其他证据支持？
- Agent 应该直接召回，还是沿图谱继续探索隐藏关系？

MAGI Memo 因此把检索建立在显式记忆语义之上：

```text
Episode
  └─ Evidence ──> Atom ──> Entity / Relation owner
                       │
                       ├─ temporal state & evolution
                       └─ reliable projection ──> semantic graph / vector retrieval
```

这带来几个直接能力：

- **可追溯**：每个 Atom 可以由多个 Episode 支持，也能回到原始内容；
- **会演化**：重复、独立、细化、时间继任与冲突被分别处理；
- **懂时间**：现实有效时间与系统记录时间互不混淆；
- **可恢复**：SQLite 是记忆事实源，Neo4j 与向量索引是可重建投影；
- **可接入**：Python API、REST/WebUI 与 DSH plugin 共用同一套服务端语义；
- **可探索**：普通召回不足时，隔离的 Explorer 可以只读展开图谱并下钻 Evidence。

因此，“Episode / Atom-aware”是 MAGI Memo 的内部记忆语义，不是项目最上层的品类定位；项目首先是图谱记忆系统，这套语义负责让图谱能够长期演化而不丢失身份、时间和来源。

## 当前能力

| 能力                      | 状态     | 说明                                                               |
| ------------------------- | -------- | ------------------------------------------------------------------ |
| Entity / Relation 语义图  | 稳定     | 稳定身份、语义关系、Neo4j 图遍历与可重建 owner description         |
| LightRAG 检索基座         | 稳定     | 保留`local`、`global`、`hybrid`、`mix`、`naive` 五种模式 |
| Episode / Atom / Evidence | 稳定     | 原始经历、原子记忆与多对多证据血缘                                 |
| 实体消歧与 Atom 裁决      | 稳定     | Episode 内归一化、候选召回、批量消歧和五类演化决策                 |
| 双时间模型                | 稳定写入 | `valid_at` / `invalid_at` 与 `created_at` / `expired_at`   |
| 可靠投影                  | 稳定     | SQLite transactional outbox 驱动 Neo4j / VDB 最终一致投影          |
| Runtime / Workspace       | 稳定     | 单 active Runtime、多 Handle、Workspace 隔离、切换与级联删除       |
| REST API / WebUI          | 可用     | 写入队列、Memory Core、语义图、检索、Workspace 与运行日志          |
| DSH plugin                | 可用     | Session 级`auto` / `manual` / `explore` / `off` 记忆模式   |
| 主动图谱检索 v1           | 验收中   | Mix 冷启动、单 Explorer、单跳 expand、Evidence 下钻                |
| 主动检索 v2 / Reflect     | 规划中   | 热启动、query rewrite、多 Explorer、社区聚类与层次摘要             |

## 架构

```text
WebUI / Python caller / DSH plugin / future MCP clients
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ MagiAPI + REST                                               │
│ stable handles · ingest · search · workspace · status        │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ MagiRuntime                                                  │
│ instance lifecycle · in-flight drain · workspace isolation   │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ MagiCore                                                     │
│ queue · parsing · extraction · resolution · Atom evolution   │
│ graph/vector retrieval · active exploration read surface     │
└───────────────┬──────────────────────┬───────────────────────┘
                │                      │
                ▼                      ▼
┌──────────────────────────┐  ┌───────────────────────────────┐
│ SQLite: source of truth  │  │ Neo4j + LightRAG projections │
│ Episode · Atom · Evidence│  │ semantic graph · vector/KV   │
│ registry · outbox · time │  │ query context · doc status   │
└──────────────────────────┘  └───────────────────────────────┘
```

三层公开边界保持清晰：

- `interface` 提供调用方可以依赖的 `MagiAPI` / `MagiHandle`；
- `magi_runtime` 管理资源生命周期、在途调用和 Workspace；
- `magi_core` 负责记忆语义、写入管线、图投影与检索。

上层 Agent 不应直接持有 SQLite、Neo4j 或底层 LightRAG 资源对象。

### 写入链路

```text
Episode
  → durable queue / parse / chunk
  → Entity + Relation + Atom + time extraction
  → Episode-local normalization
  → batch entity resolution
  → owner-scoped Atom decision
  → SQLite transaction + projection outbox
  → Neo4j / vector / KV materialization
```

已完成结构化提取的 Agent 也可以通过 `ingest_extracted` 跳过第一次抽取，但仍会经过实体消歧、Atom 裁决、Evidence、幂等写入与可靠投影。

### 检索链路

普通检索沿用 LightRAG 的五种模式：

- `local`：围绕命中实体和邻近关系组织上下文；
- `global`：从关系与高层结构回答全局问题；
- `hybrid`：组合 local 与 global；
- `naive`：直接做文本块向量召回；
- `mix`：融合知识图谱与向量上下文，通常配合 reranker 使用。

主动检索 v1 在普通 Mix 召回之后增加一个隔离的只读 Explorer：

```text
query → mix seeds → frontier
                       ↓
              expand one hop
                       ↓
              select candidate
                       ↓
              inspect Evidence
                       ↓
             continue or stop
```

Explorer 只接收 `expand` 和 `evidence` 两个内部工具，不能写入记忆。它维护紧凑的 `visit` 与 `findings`，所有真实写入仍统一经过单一 Memory Writer。

## 图谱记忆如何形成

MAGI Memo 的核心不是把 Episode 或 Atom 当成新的检索噱头，而是解决一张长期增长的知识图谱如何保持身份稳定、事实可追溯、状态可演化。写入时依次完成：

```text
Episode 证据边界
  → Entity 身份归一与跨 Episode 消歧
  → Relation 端点重写与稳定注册
  → Atom 的 owner 隔离候选过滤
  → 五类演化裁决与双时间更新
  → 当前 Entity / Relation description 重新物化
  → Neo4j 图和向量检索投影
```

### Episode：一次输入的证据边界

Episode 是不可变的输入与溯源边界，目前支持：

- `document`：文件或文本输入；
- `conversation`：由外层 Agent 选择并提交的对话；
- `multimodal_text`：多模态内容经过分析后的文本表示。

Episode 保存原始内容、来源 URI、参考时间、创建时间和处理状态。长文档可以被切成多个 chunk 并并行抽取，但所有结果仍回到同一个 Episode；chunk 是处理单元，不是长期记忆本体。

### Entity 与 Relation：图谱的稳定骨架

Entity 和 Relation 是 Neo4j 语义图中真正参与检索与遍历的结构。Entity registry 为同一个现实对象维护稳定 ID、规范名称、别名和类型；Relation registry 在完成实体消歧后，用两端稳定 Entity ID 生成稳定 Relation ID。Entity Atom 与 Relation Atom 分别归属于这些 owner。

这样，同一个人被不同 Episode 写成“周航”“Zhou Hang”或明确别名时，可以汇入同一图节点；两个同名但不同的人则可以保留为不同节点。关系端点会在实体裁决后统一重写，不会继续连接抽取阶段的临时名称。

## 实体消歧与分层候选过滤

实体消歧发生在 Atom 裁决之前。只有先确定“这是谁”，后续 Atom 才能被严格限制在正确的 Entity 或 Relation owner 下比较。

### 第一层：Episode 内显式别名归并

同一 Episode 中，系统先根据抽取结果明确给出的主名称与 aliases 做归一化比较和并查集合并：

```text
周航 + aliases=[Zhou Hang]
Zhou Hang + aliases=[周航]
        ↓
同一个 Episode-local Entity container
```

归并后会同步重写本轮所有 Relation 端点。这里只采用 Episode 明确提供的别名，不凭字符串相似度擅自合并两个实体；归并后变成自环的临时关系会被移除。

### 第二层：从已有实体注册表召回候选

系统使用新实体的主名称和每个 alias，从当前 Workspace 的未过期实体中并行收集多路候选：

| 候选来源    | 当前行为                                          |
| ----------- | ------------------------------------------------- |
| Exact       | 规范化 alias 完全一致，基础分数`1.0`            |
| Lexical     | 新旧名称互为包含，基础分数`0.8`                 |
| SQLite FTS5 | 名称分词后全文匹配，基础分数`0.7`               |
| Embedding   | 每个主名称和 alias 独立向量召回，默认阈值`0.72` |

候选按 Entity ID 去重，同一实体从多路命中时保留最高分。默认最终最多保留 8 个候选；每个候选还会补充其最近 8 条 owner Atom 表示，避免模型仅凭名字决定同一性。

只有关系端点、没有 Entity Atom 的 endpoint-only entity 也不会退化成纯名称判断：系统会把它相邻的 Relation Atom 文本作为消歧语义。

### 第三层：Episode 级批量语义裁决

候选生成只负责缩小范围，不直接等同于“已经匹配”。之后本 Episode 中所有拥有候选的实体会进入一次批量 LLM 裁决，并附带最近 4 个 Episode 的上下文：

- 每个新实体只能选择分配给自己的候选 ID，不能越过 allow-list；
- 主题相似、同名人物、相关组织、版本和父子实体不会自动视为同一对象；
- 没有任何候选时不调用 LLM，直接创建新实体；
- 证据不足时模型必须返回 `null`，系统创建新的稳定 Entity；
- 模型调用失败或返回非法候选时安全地创建新实体，不会误合并既有身份；
- 新实体若与候选规范名称冲突，会生成可区分的规范名称，避免 namesake 再次碰撞。

成功合并后，本轮新名称与 aliases 会补入既有 Entity，并分别建立名称 embedding。由于整个 commit 边界串行化，两个并发文档不会同时看到空注册表后各自创建重复身份。

```text
Episode-local alias merge
        ↓
exact / lexical / FTS / embedding candidate union
        ↓
candidate Atom context + recent Episode context
        ↓
one batched, allow-listed entity decision
        ↓
reuse stable Entity ID or create a new Entity
        ↓
rewrite Relation endpoints
```

## Atom、Evidence 与分层裁决

Atom 是归属于 Entity 或 Relation 的最小可演化事实单元，并不是 Neo4j 节点。Evidence 连接 Atom 与支持它的 Episode：

```text
Episode 1 ─┐
Episode 2 ─┼─ AtomEvidence ─> Atom ─> Entity / Relation owner
Episode N ─┘
```

同一 Atom 可以由多个 Episode 支持。重复事实不会复制 Atom，而是新增 Evidence 并增加 `support_count`，因此系统既能保持事实唯一，又能回溯每一条来源。

Atom 的候选选择也不是把整个数据库交给模型比较，而是逐层收窄。

### 第零层：严格 owner 隔离

Entity Atom 只与同一个 Entity 的 Atom 比较，Relation Atom 只与同一对稳定关系端点的 Atom 比较。不同 owner、Entity 与 Relation、两个不同同名实体之间都不能互选候选。

这是最重要的安全边界：embedding 只决定同一 owner 内哪些历史事实值得进入上下文，不决定事实属于谁。

### 第一层：同一 Episode 的批内归并

长文档的不同 chunk 可能在同一 Episode 中重复或逐步补全一个事实。系统会在访问历史数据库之前，按 owner 对本轮 Atom 分组：

1. `normalized_content + invalid_at` 完全相同的 Atom 直接折叠到最早出现的代表 Atom，Evidence 合并；
2. 后出现的 Atom 只能指向同 owner、同 Episode 中更早的 Atom，避免环和跨 owner 误判；
3. owner 内本轮先前 Atom 不超过 16 条时全部进入上下文；
4. 超过 16 条时使用 embedding Top-K 8（默认阈值 `0.45`），再补最近 4 条，保持时间状态变化可见；
5. 有语义候选的 Atom 组成一次批量裁决，非法目标或调用失败降级为 `INDEPENDENT`。

批内先处理可以避免同一文档的并行 chunk 因数据库尚未提交而制造重复 Atom，也让同一 Episode 内的细化、后继和冲突能够形成演化链。

### 第二层：历史 Atom 本地快速路径

对每个批内代表 Atom，系统再检查同 owner 的历史：如果一个当前活跃 Atom 同时满足：

```text
normalized_content 完全相同
AND invalid_at 完全相同
```

则无需调用 LLM，直接判为 `DUPLICATE`。新 Episode 只为旧 Atom 增加 Evidence。

这里有意不要求 `valid_at` 完全相同：重复陈述可能因为新 Episode 的参考时间而得到较晚的推断起点，但只要文本断言和明确结束时间相同，仍视为同一当前事实。

### 第三层：按历史规模切换上下文

未命中本地快速路径时，系统构造同 owner 的历史候选窗口：

| owner 历史规模 | 候选上下文                                                     |
| -------------- | -------------------------------------------------------------- |
| 不超过 16 条   | 提供该 owner 的全部历史 Atom                                   |
| 超过 16 条     | embedding Top-K 8，默认阈值`0.45`，再补最近 4 条 active Atom |

同时附带 owner 当前 summary 和最近 4 个 Episode 的上下文。embedding 适合找语义相近事实，但可能漏掉“同一属性最近发生变化”的旧状态，所以近期 active tail 是独立的补充通道，不能被相似度 Top-K 取代。

### 第四层：Episode 级批量裁决与结果校验

所有需要访问历史的 Atom 组成一次 Episode 级批量请求。候选 Atom 表在整个请求中去重，但每个 `new_atom` 都有独立的 `candidate_atom_ids` allow-list：

- 模型必须在五类操作中选择一个；
- 非 `INDEPENDENT` 操作必须指向自己的一个允许候选；
- 模型不能跨 owner、不能选择未召回的 Atom；
- 非法 decision、缺失结果、越权目标或模型异常都安全降级为 `INDEPENDENT`；
- 裁决完成后才确定 Evidence 复用、新 Atom 插入、旧 Atom 时间更新和演化边。

批内裁决和历史裁决分别批量执行；不是为每一个 Atom 单独调用一次 LLM。

## 双时间：事实时间与系统时间

五类决策不是五种“相似度”，而是五种对旧 Atom 的演化操作。判断时始终有两个对象：

- `new_atom`：本次 Episode 新提取出的事实；
- `matched_atom`：同一 owner 下、经过上述分层过滤后与它最相关的历史事实。

理解这些操作前，必须先区分两条时间轴：

| 时间轴        | 字段                            | 含义                                                   |
| ------------- | ------------------------------- | ------------------------------------------------------ |
| 现实有效时间  | `valid_at` / `invalid_at`   | 事实在现实世界中何时开始成立、何时不再成立             |
| MAGI 系统时间 | `created_at` / `expired_at` | Atom 何时被 MAGI 记录、何时退出当前 description 与投影 |

`expired_at` 不等于“事实为假”，也不是硬删除。它只表示当前 Entity / Relation description 和图谱投影不再采用这个旧版本；旧 Atom、Evidence 和演化链仍保留，用于历史查询、审计和溯源。

例如系统在 2026 年才得知用户 2024 年搬家：

```text
invalid_at = 2024-03-01   # 旧居住状态在现实世界中结束
expired_at = 2026-08-24   # MAGI 在此时获知变化并撤下旧投影
```

这两条时间轴让“后来才知道的旧变化”和“现在才发生的新变化”不会混为一谈。当前写入和物化已经使用双时间；完整的 query-time 双时间过滤仍属于后续工作。

## 五类 Atom 演化操作

| 决策                           | 新建 Atom | 旧 Atom 增加 Evidence |   旧`invalid_at` |   旧`expired_at` |                        演化记录 |
| ------------------------------ | --------: | --------------------: | -----------------: | -----------------: | ------------------------------: |
| `DUPLICATE`                  |        否 |                    是 |               不变 |               不变 |                              无 |
| `INDEPENDENT`                |        是 |                    否 |               不变 |               不变 |                              无 |
| `REFINEMENT`                 |        是 |                    否 |               不变 | 设置为当前系统时间 |         `新 → 旧 REFINEMENT` |
| `TEMPORAL_SUCCESSOR`         |        是 |                    否 | 设置为状态转变时间 | 设置为当前系统时间 | `新 → 旧 TEMPORAL_SUCCESSOR` |
| `CONTRADICTION`              |        是 |                    否 |           默认不变 |           默认不变 |      `新 → 旧 CONTRADICTION` |
| `CONTRADICTION + supersedes` |        是 |                    否 |   有可靠时间时设置 | 设置为当前系统时间 |      `新 → 旧 CONTRADICTION` |

演化关系保存在 SQLite 的 `atom_evolution` 表，方向统一为：

```text
新 Atom ──REFINES / SUCCEEDS / CONTRADICTS──> 旧 Atom
```

### 1. DUPLICATE：同一事实的新证据

```text
已有 A1：周航喜欢推理小说
新建候选 N1：周航喜欢推理小说
```

系统不会创建 N1，而是把新 Episode 的 Evidence 指向 A1：

```text
Episode-1 ─Evidence→ A1
Episode-2 ─Evidence→ A1
```

结果是系统中仍只有一个 Atom，`support_count` 增加，可以回溯多个 Episode；不产生演化关系，也不修改任何时间字段。同 owner 的活跃 Atom在标准化文本和 `invalid_at` 完全一致时，会走无需 LLM 的本地快速路径。

### 2. INDEPENDENT：相关但不同的事实

```text
已有 A1：周航喜欢推理小说
新增 N1：周航加入了推理社
```

两者都属于“周航”，也可能具有很高的语义相似度，但表达的是不同事实。系统会创建 N1、保留 A1，让两者都参与当前 description；二者各自保留 Evidence 和时间区间，不建立 Atom 演化关系。

“独立”不等于完全无关，而是新事实无法被旧事实吸收，也不需要改变旧事实的状态。它同时是安全降级结果：模型输出非法 decision、目标不在 allow-list、目标跨 owner 或调用失败时，系统宁可保留新事实，也不会错误覆盖历史。

### 3. REFINEMENT：知识表示变得更精确

```text
已有 A1：周航曾担任推理社社长
新增 N1：周航于 2023 年至 2024 年担任明州大学推理社社长
```

N1 没有否定 A1，而是补充了组织、时间和角色边界。系统会：

1. 新建 N1 并保存本次 Evidence；
2. 建立 `N1 → A1 REFINEMENT`；
3. 设置 `A1.expired_at = 当前系统时间`；
4. 保持 `A1.invalid_at` 不变。

旧陈述在现实世界中并没有变成错误，只是作为知识表示不够精确。从现在起，当前图谱描述采用 N1，不再重复物化 A1。旧 Atom 仍然保留，可用于审计、Evidence 回溯和演化链查询。

因此，`REFINEMENT` 是知识版本替换，只操作系统时间，不是现实状态变化。

### 4. TEMPORAL_SUCCESSOR：现实状态后来发生变化

```text
A1：周航居住在北京
    valid_at = 2022-01-01
    invalid_at = null

N1：周航于 2024-03-01 搬到上海居住
    valid_at = 2024-03-01
```

这是同一属性“居住地”的后续状态。系统形成两个不重叠的现实有效区间：

```text
A1 北京：[2022-01-01, 2024-03-01)
N1 上海：[2024-03-01, ...)
```

提交时会：

- 新建 N1；
- 设置 `A1.invalid_at = 2024-03-01`；
- 设置 `A1.expired_at = 本次写入的系统时间`；
- 建立 `N1 → A1 TEMPORAL_SUCCESSOR`。

状态转变时间优先采用模型给出的 `target_invalid_at`，没有时采用新 Atom 的 `valid_at`；系统还会验证该时间不早于旧 Atom 的 `valid_at`。

这里 `invalid_at` 表示北京居住状态何时在现实中结束，`expired_at` 表示 MAGI 何时获知并处理这次变化。这是双时间最典型的使用场景。

### 5. CONTRADICTION：重叠时间内的不兼容主张

```text
已有 A1：周航出生于 1990 年
新增 N1：周航出生于 1991 年
```

两者不能同时为真，但系统不能仅凭一次模型判断擅自删除旧来源。默认行为是：

- 新建 N1，同时保留 A1；
- 两者的 `invalid_at` 和 `expired_at` 默认都不变；
- 建立 `N1 → A1 CONTRADICTION`；
- 分别保留双方 Evidence。

这表示 MAGI 已知两个主张互相冲突，但暂时不知道哪个可靠。召回时可以把冲突双方、时间与来源一起交给模型，而不是提前抹掉信息。

只有 Episode 明确表达纠正、撤回，或新来源可证明更权威时，模型才能返回 `supersedes_target=true`。此时系统仍保留旧 Atom 与 Evidence，但会设置旧 `expired_at`；只有存在可靠的现实结束时间时，才同时设置旧 `invalid_at`。

```json
{
  "decision": "CONTRADICTION",
  "supersedes_target": true,
  "target_invalid_at": "2026-08-24T10:00:00Z"
}
```

如果只是两个来源说法不同，没有明确的纠正、撤回或权威依据，就必须保持 `supersedes_target=false`。

### 最容易混淆的三组区别

```text
DUPLICATE：没有新增事实，只增加来源。
REFINEMENT：新增了实质精度，需要一个新 Atom 替换当前知识表示。

REFINEMENT：我们现在知道得更准确了。
TEMPORAL_SUCCESSOR：现实世界后来发生了变化。

TEMPORAL_SUCCESSOR：两个说法位于不同时间，可以先后成立。
CONTRADICTION：两个说法在重叠时间范围内无法同时成立。
```

例如“2023 年住北京，2024 年住上海”是 `TEMPORAL_SUCCESSOR`；“同一时期一个来源说住北京，另一个说住上海”才是 `CONTRADICTION`。

整体原则是：

> 能复用 Evidence 就不复制 Atom；能保留历史就不删除；只有现实状态确实结束时才写 `invalid_at`；只有旧版本不再参与当前物化时才写 `expired_at`。

对应实现见 [`src/magi_core/memory/adapter.py`](./src/magi_core/memory/adapter.py)，模型的实体与 Atom 裁决约束见 [`src/magi_core/prompt.py`](./src/magi_core/prompt.py)，批量 allow-list 校验与失败降级见 [`src/magi_core/memory/decisions.py`](./src/magi_core/memory/decisions.py)。

## 存储与一致性

每个 Workspace 拥有独立的记录层和投影命名空间：

- **SQLite** 保存 Episode、Atom、Evidence、演化、实体/关系注册表、别名、embedding、删除备份与 projection outbox；
- **Neo4j** 保存实体节点、有语义的关系边、当前物化描述与 `atom_ids`；
- **LightRAG storage** 保存文本块、向量、KV、缓存和文档处理状态。

SQLite 是事实源。跨存储不假装拥有分布式强事务，而是通过 owner revision、幂等重投、版本栅栏、指数退避和启动 reconcile 达成最终一致。

## Python API

推荐通过 `MagiAPI` 管理生命周期，而不是让调用方直接管理 Core 和各存储：

```python
import asyncio
from datetime import datetime, timezone

from interface import Episode, EpisodeKind, MagiAPI
from magi_core import MagiCore


def build_engine(ragstore, workspace_id):
    return MagiCore(
        working_dir=str(ragstore),
        workspace=workspace_id,
        llm_model_func=my_llm_model,
        embedding_func=my_embedding_model,
        graph_storage="Neo4JStorage",
    )


async def main():
    api = MagiAPI.build(
        workspace_home="./magi-data",
        workspace_id="personal",
        core_factory=build_engine,
    )
    await api.init()
    handle = await api.open(owner="example-agent")

    try:
        episode = Episode(
            id="conversation-2026-08-24-001",
            kind=EpisodeKind.CONVERSATION,
            content="用户决定将 MAGI Memo 作为长期记忆服务。",
            reference_at=datetime.now(timezone.utc),
            source_uri="conversation://2026-08-24/001",
        )
        await handle.ingest(episode)
        result = await handle.search(
            "用户如何定位 MAGI Memo？",
            mode="mix",
            enable_rerank=True,
        )
        print(result)
    finally:
        await handle.close()
        await api.finalize()


asyncio.run(main())
```

生命周期是：

```text
build → init → open → ingest/search/status → close → finalize
```

`index` / `query` 仅作为一期兼容别名保留。直接实例化 `MagiCore` 的底层调用方仍必须显式执行 `await core.initialize_storages()`。

## REST API 与 WebUI

FastAPI 服务同时承载 WebUI 与公开 API。常用接口包括：

| 类别              | 接口                                                                                      |
| ----------------- | ----------------------------------------------------------------------------------------- |
| Workspace         | `GET/POST /workspaces`、`POST /workspaces/{id}/activate`、`DELETE /workspaces/{id}` |
| 原始写入          | `POST /documents/upload`、`POST /documents/text`、`POST /documents/texts`           |
| 已抽取写入        | `POST /memory/ingest/extracted`                                                         |
| 生成式查询        | `POST /query`、`POST /query/stream`                                                   |
| 结构化召回        | `POST /query/data`                                                                      |
| Memory inspection | `GET /memory/overview`、`/memory/episodes`、`/memory/atoms`                         |
| 主动探索原语      | `POST /memory/explore/expand`、`POST /memory/explore/evidence`                        |
| 图谱              | `GET /graphs` 与 `/graph/*`                                                           |
| 管线状态          | `GET /documents/pipeline_status`                                                        |

WebUI 当前提供：

- Episode 上传、文本写入、扫描、队列与错误状态；
- Episode、Atom、Evidence、Entity、Relation 与演化详情；
- 语义图可视化和普通检索控制台；
- Workspace 创建、切换与受控删除；
- Runtime 状态和近期日志。

具体请求 schema 以运行中的 `/docs` 为准。

## DeepSeek Harness 集成

[`sages/dsh/magi-memo`](./sages/dsh/magi-memo/) 是当前主力 Harness 接入：一个本地 Cordis plugin 通过 REST 调用 MAGI Memo，并向 DSH 注册记忆工具。

对模型可见的主要工具包括：

- `magi_memory_write_episode`
- `magi_memory_write_extracted`
- `magi_memory_recall`
- `magi_memory_explore`
- `magi_workspace_create`
- `magi_workspace_activate`

在 DSH Session 中可动态切换：

```text
/memory auto
/memory manual
/memory explore
/memory off
/memory
```

- `auto`：每轮执行 Recall Gate 与 Write Gate，但不机械强制工具调用；
- `manual`：仅在用户明确要求时读写记忆；
- `explore`：允许在隐藏图关系可能重要时启动一个隔离 Explorer；
- `off`：禁止记忆召回与写入。

模式由 DSH Session log 持久化，恢复或 fork 后仍然有效；Agent 无权自行切换模式。Workspace 的创建和激活同样要求用户明确授权。

启动 DSH Web profile：

```bash
cd sages/dsh
pnpm dsh web --patch ./magi-memo/cordis.yml
```

默认访问 [http://127.0.0.1:3080](http://127.0.0.1:3080)。完整配置、工具边界和验证命令见 [`sages/dsh/magi-memo/README.md`](./sages/dsh/magi-memo/README.md) 与 [`doc/dsh-magi-memo-plugin.md`](./doc/dsh-magi-memo-plugin.md)。

## MAGI 三贤者现在代表什么

MAGI 的命名仍然保留，但在 **Memo** 中代表三条能力研究线，而不是三个常驻人格 Agent：

| 研究线           | 当前含义                                                          |
| ---------------- | ----------------------------------------------------------------- |
| CASPER / 直觉    | 让外部 Harness 低开销、可靠地调用基础记忆检索，并持续优化召回效率 |
| MELCHIOR / 分析  | 受预算约束的主动图谱探索、Evidence 下钻与后续多 Explorer 研究     |
| BALTHASAR / 判断 | Neo4j/GDS 社区聚类、Reflect、主题识别与带证据血缘的层次摘要       |

人格、群聊、投票、Blackboard、MQP、MCC 和通用 Agent 状态机属于未来 **MAGI Sys**，不应被描述为 MAGI Memo 的现有功能。

## 项目阶段与路线图

### 已完成

1. **一期：LightRAG + Neo4j 记忆基座**

   - 保留成熟的抽取、图谱、向量、文档队列和五种检索模式；
   - 完成 `magi_core` 命名与工程边界迁移。
2. **二期：Episode / Atom Memory Core 与 Runtime**

   - Episode、Atom、Evidence、实体/关系注册表与双时间；
   - 实体消歧、五类 Atom 演化、删除备份与可靠 projection outbox；
   - `MagiAPI`、多 Handle、Workspace 生命周期、REST 与新版 WebUI。

### 当前主线

3. **三期：DSH 主动图谱检索与 Reflect**
   - DSH plugin 与 Session 级记忆模式；
   - 主动检索 v1：单 Explorer 的 `mix → expand → evidence` 闭环；
   - 主动检索 v2：冷热启动、自动 query rewrite、自适应多 Explorer；
   - Neo4j/GDS 社区聚类、Reflect 与证据可追溯的摘要。

### 明确不在当前范围

- 三个常驻人格 Sage 与通用多 Agent 群聊；
- Blackboard、MQP、MCC 和协商 Session；
- Memo 自身成为一个完整的 Agent Harness；
- 让只读 Explorer 直接修改记忆或图谱；
- 把社区摘要替代为 Atom 事实源。

这些边界让 Memo 专注于一件事：成为可解释、可演化、可被不同 Agent 系统复用的长期记忆层。

## 仓库结构

```text
MAGI-Memo/
├── src/
│   ├── interface/            # 稳定的 MagiAPI / MagiHandle
│   ├── magi_runtime/         # 生命周期、Handle、Workspace、BackendBundle
│   ├── magi_core/            # 记忆语义、管线、存储、检索与 FastAPI
│   └── webui/                # React 19 + TypeScript + Vite
├── sages/dsh/
│   └── magi-memo/            # DSH Cordis plugin 与主动检索编排
├── doc/                      # 阶段设计、验收、快速开始与存储手册
├── tests/                    # 后端统一测试树
├── scripts/                  # 测试、环境与发布工具
├── env.example               # 完整配置模板
└── server                    # 本地服务入口
```

## 进一步阅读

- [`doc/memory-core-quickstart.md`](./doc/memory-core-quickstart.md)：Memory Core 使用方式
- [`doc/phase2-completion-and-phase3-handoff.md`](./doc/phase2-completion-and-phase3-handoff.md)：二期架构与三期交接
- [`doc/phase3-mag25-active-graph-retrieval-design.md`](./doc/phase3-mag25-active-graph-retrieval-design.md)：主动图谱检索设计
- [`doc/dsh-magi-memo-plugin.md`](./doc/dsh-magi-memo-plugin.md)：DSH plugin 的工具与安全边界
- [`doc/magi-storage-schema-manual.md`](./doc/magi-storage-schema-manual.md)：SQLite / Neo4j / VDB 存储模型
- [`AGENTS.md`](./AGENTS.md)：仓库架构、并发契约与开发约定

`doc/phase*` 中的阶段文档保留了设计演进背景；当历史设计与代码冲突时，以当前代码、测试和本 README 的“状态”标记为准。

## 开发与验证

后端测试：

```bash
# 全部测试
./scripts/test.sh tests

# 相关子集
./scripts/test.sh tests/memory tests/interface tests/api

# 静态检查
ruff check .
```

WebUI：

```bash
cd src/webui
bun test
bun run lint
bun run build
```

DSH plugin：

```bash
cd sages/dsh
node node_modules/typescript/bin/tsc -p magi-memo/tsconfig.check.json
node --import tsx --test magi-memo/tests/*.test.ts
node --import tsx scripts/run-oxlint.ts magi-memo
```

外部服务测试必须使用 mock；真实 Neo4j、LLM 或其他集成测试按标记与环境变量显式启用。Bug 修复应同时增加回归测试。

## 来源与致谢

MAGI Memo 直接演化自 [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG)，保留并继续维护其经过验证的抽取、知识图谱、向量检索、存储后端与 API 基础。项目当前的 Episode / Atom 记忆语义、Runtime / Workspace、可靠投影、WebUI 改造和 Agent Harness 接入建立在这套基座之上。

MAGI 的命名灵感来自《新世纪福音战士》中的 MAGI System；本项目与相关版权方没有隶属或背书关系。
