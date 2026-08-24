# MAGI Memo 三期 MAG-25：Agentic Graph Retrieval 设计探索与方案

日期：2026-08-19
状态：v1 已实现，待评审。Linear: [MAG-25](https://linear.app/dengrc/issue/MAG-25)，下游 [MAG-38](https://linear.app/dengrc/issue/MAG-38)
关联工单：MAG-17（Done）、MAG-21（In Review）、MAG-26（Todo）、MAG-27（Backlog）、MAG-36（In Review）

## 1. 背景与定位

MAG-25（MELCHIOR）研究基于 dsh 的并发主动图谱检索。MAG-17 已收敛三期方向，MAG-21 已实际跑通
dsh + MAGI 插件，所以 MAG-25 不再讨论"如何接 dsh"，而是从已有插件和 Sub-Agent runtime 出发，
定义一套可评测的主动探索协议。

本文档记录设计讨论全过程。第 2–4 节保留了方案演进，包含已被 v1 否决的 APOC、服务端 session、
stable id 交互、多 Agent 和 path 输出等历史方案；实现的规范以第 1.1 节和代码为准。

## 1.1 v1 实现结论（MAG-38）

- Core 暴露 `POST /memory/explore/expand` 与 `POST /memory/explore/evidence`。
- Agent 只用实体名和无序关系端点名；Core 内部解析并校验 stable id，dsh 客户端在模型可见前移除 id。
- `frontier` 只包含实体名。`expand(frontier, visit)` 完成一跳展开，并且仅当候选的邻居实体与关系端点对都在
  `visit` 时过滤该候选；早期未选择的候选不会进入额外黑名单。
- 唯一探索状态是结构化 `visit` 与语义 `findings`。没有 `seen`、`deferred`、`expanded` 或客户端 path 状态。
- `exhausted=true` 只表示所有 frontier 完整读取、去重后确实为空；缺失 frontier、截断与读取失败均显式区分。
- dsh 新增可选 `/memory explore` 模式。复合工具先做 mix recall，再启动恰好一个隔离的 Explorer Sub-Agent；
  child 只能调用 expand/evidence，主 Agent 只接收结构化 `visit + findings`。
- 正常终止条件严格为：`(本轮 expand 确实为空或本轮一个都不选) AND 本轮不从历史 context 补选任何候选`。

## 2. 思考演进

讨论不是直线推进，而是经过几次反思和重定向。把全过程记录下来，便于回顾"为什么是这个方案，不是别的"。

### 2.1 起点：codex 调研结论

前置 session（codex）完成了对 MRAgent、IRCoT、Think-on-Graph、A2RAG、PAR²-RAG、MAGMA、SCAIR
等主动检索工作的调研，得出几个关键判断：

- MRAgent 的"主动检索"实质是单 LLM 在有限工具集上做多轮状态化导航，**不是多 Agent 系统**，
  也没在遍历时并发修改持久化图谱。
- 增加"探索深度"通常持续有效，扩大"每轮并行宽度"会饱和——**深度不能被宽度替代**。
- 当前 MAGI 系统的关键 gap：
  (a) `/query/data` 输出会丢 `magi_entity_id` / `magi_relation_id` / `atom_ids`
  （[utils.py:5239](../src/magi_core/utils.py) `convert_to_user_format`）；
  (b) HTTP 没有"从 frontier 受限展开"的只读原语
  （[memory_routes.py](../src/magi_core/api/routers/memory_routes.py) 仅 list/detail）；
  (c) dsh 通用 `subagent` 模型工具未暴露 `outputSchema`
  （[tool-subagent/src/index.ts](../sages/dsh/packages/subagent/tool-subagent/src/index.ts) 模型只能填
  description/prompt/run_in_background；schema 通过 [structured.ts](../sages/dsh/packages/subagent/subagent-in-process-driver/src/structured.ts)
  的 `attachStructuredRuntime` 在 child 创建窗口由宿主注入）。

codex 推荐的初版架构：4 个 Core 原语（search/expand/inspect/evidence）+ 并发 3 个 one-shot
Explorer Sub-Agent + Sufficiency Gate + 可选第二轮。这套方案的问题在第 2.5 节暴露。

### 2.2 APOC + GDS 引入：从 4 工具到 3 工具

发现 Neo4j 已装 APOC 和 GDS 插件，且 [neo4j_impl.py:1373](../src/magi_core/kg/neo4j_impl.py)
已经在用 `apoc.path.subgraphAll` 做 WebUI 子图展开。这让我们能：

- 用 APOC `path.expandConfig` 原生处理 BFS、visited 去重、跳数上限、节点截断——**预算由
  Cypher 查询本身保证**，呼应 dsh AGENTS.md "Enforce a decision in the operation that makes it"。
- 用 GDS WCC + PageRank 物化 `community_id` 和 `pagerank` 到每个 entity 节点，查询时仅读属性。
- 把 expand 和 inspect 合并：APOC subgraph 返回的 node 对象本身带完整属性（包括 Phase 2
  物化的 description 和 atom_ids），不需要单独 inspect 步。

3 工具方案：`search` / `expand` / `evidence`。

### 2.3 反思一：这套方案就是 agentic RAG 套了图形状的工具

用户提出核心质疑：**"agent 深入图谱主动探索，这个故事是能办到的吗"**。诚实评估后承认：

- 让 Explorer LLM 拿 expand/evidence 自己决定下一步、看观察、再决定——**这就是 ReAct**。
- 并发 3 个 Explorer + Sufficiency Gate 是工程加固，**不是范式差异**。
- "agent 深入图谱"作为新故事——讲不通。

确立主动检索的本质：**state-driven iterative retrieval**。设计空间是三个正交维度——谁持有状态、
谁决定下一步、谁判断相关性。ReAct = (LLM, LLM, LLM)。前述方案 = (host, LLM, LLM)。
都是 agentic RAG 家族成员。

### 2.4 反思二：什么让 Agentic GraphRAG 真正不同

思考后其实

```
ReAct-like control  ⊃  Agentic RAG  ⊇  Agentic GraphRAG / KG Agent
```

确立三条判别标准（用户提出）：

1. **维护 graph-specific 结构化状态**（frontier / visited / path / subgraph）
2. **action space 图特定窄化**（不是任意 SPARQL）
3. **能输出 ReAct + 通用 search 做不到的东西**（访问轨迹、拓扑早停、progress 信号）

按这三条评 3 工具方案：状态只有半个（exclude_ids 算 visited，path/subgraph 没有一等公民身份）；
action space 图窄化 ✓；可输出差异化内容 ✗。**3 工具方案仍然不是真正的 Agentic GraphRAG**。

### 2.5 反思三：host 端启发式过滤做得越多，越背离 agentic 灵魂

继续往 host 端加 Layer 1（pagerank filter）+ Layer 2（embedding filter）+ Layer 3（LLM judge）
的设计，被用户进一步反思戳破：

> "在局部子图做 embedding topk + 模型判断，跟我直接对全量已有混合索引系统召回 topk 然后模型判断，
> 有什么优越性"

如果 host 端启发式已经把候选筛干净，LLM 就只是在 rerank 一个几乎确定的集合，"active" 名存实亡。
**主动权在 host 启发式 = fancy RAG；主动权在 agent 基于图结构决策 = agentic GraphRAG**。
越加 layer，越往前一种滑。

### 2.6 演化：内部 LLM → 外部 agent → sub-agent 委托

在 host 过滤反思（§2.5）和极简方案突破（§2.7）之间，有一个关键架构决策必须先解决：
**探索循环由谁跑**——Core 内部 LLM、主 agent 直接干、还是委托 sub-agent。这决定整个方案的
故事、context 边界和工程实现路径。

用户提出张力："到底是让外层 agent 干这个事，还是让记忆系统内的 llm 干这个事"。

#### 内部 LLM（Core 自己跑探索循环）

- 优点：节约 context，每次只需基于新 query/时间匹配过滤 frontier
- 缺点：故事塌掉——"封装成调用接口显得无趣"；本项目 LLM 调用阻塞和缓慢，Core 内部跑会拖累
  ingest 等其他工作；Core 越来越重，要承担多个 LLM 角色（抽取/消歧/裁决/探索）

#### 外部 agent 直接干（主 agent 自己调 expand/evidence 原语）

- 优点：故事成立（"agent 主动检索"）；记忆系统保持通用，不包含探索算法
- 缺点：context 爆炸——每次 expand 结果都进主 agent 的 conversation log，5 轮 expand × 15 候选
  × 描述体积 ≈ 50K+ tokens 污染主 agent context；agent loop 每步走完整 system prompt + tool
  schema + history，开销大；dsh 不一定支持 plugin tool result hiding（用户当时质疑这点）

#### 委托给 sub-agent（突破）

第三个选项：主 agent 通过 `ctx.subagents.start()` 委托主动检索给 sub-agent。sub-agent 在
自己的 context 里跑探索循环，主 agent 只收到 `structured_output` 提交的 Finding。

- 用户当时的担心："对他来说探索过程仍然可能是累积的形态，而我们所需要的可能只是需要激活的
  路径而已"
- 澄清：sub-agent context 累积正是 **feature 不是 bug**——query 重写、frontier 判断、progress
  评估都需要 path 在 context 里；真正不想要的是主 agent context 被污染，sub-agent 隔离正好解决
- 这正是 "plugin context hiding" 的等价物——不是 tool result 层面隐藏，是通过 sub-agent + 强制
  structured_output 实现的**会话层面隔离**。dsh 已有这套基础设施（[structured.ts](../sages/dsh/packages/subagent/subagent-in-process-driver/src/structured.ts)
  `attachStructuredRuntime`），不用新发明
- "本项目 LLM 调用阻塞和缓慢" 的考虑：内部 Core LLM 主 agent 阻塞等返回；外部 sub-agent 主
  agent 不阻塞，可以并行做其他工作（如 fallback 普通 mix 召回）；sub-agent 可用 `agentOptions`
  配更快模型。所以 sub-agent 反而对"阻塞和缓慢"更友好

#### 故事定性："agent 委托主动检索"

委托本身是 agent 的主动决策（选择委托、选择起点、选择预算、接收结果）。不如"agent 自己主动
检索"强，但比"Core 内部 fancy 检索接口"强。用户确认接受："我所希望的就是 subagent 来做主动检索，
这就是我最想看到的方案"。

#### 这条演化路径的本质

从"封装成接口"（内部 LLM）→ "agent 自己做"（外部主 agent）→ "agent 委托 sub-agent 做"，本质是
**在不污染主 agent context 的前提下保留 agent-driven 故事**。每次妥协都不是放弃 agentic 灵魂，
而是把它放到正确的 context 边界上：sub-agent 是临时的、专注的、有结构化提交契约的——
这是它跟"主 agent 自己探索"和"Core 内部循环"的根本差别。

### 2.7 突破：极简方案——agent 完全自主，Core 只做物理约束

用户提出激进 idea：**直接抛弃所有扩展算法，完完全全交给 agent**。Core 只暴露一个 expand 原语，
agent 自己决定一切：

- agent 输入激活的实体/关系，expand 返回指定跳数内所有去重信息
- agent 自己判断是否够回答，需要扩展哪些实体，自己生成新 frontier 列表再次 expand
- 跨 sub-agent 用加锁文件记录 visited 路径，自动注入 system prompt

这套方案的核心洞察：**主动检索的优势是让 agent 充分利用图结构能力实现自主决策与信息发现**。
host 不抢决策，但也不剥夺 agent 看图结构的能力。

文件锁方案后被改为服务端 `exploration_session_id` 维护 visited 集合——更轻量，无 OS-level flock，
无 system prompt 重 build 开销，锁在 Core 内部用现有 SQLite 事务或 asyncio.Lock 解决。

### 2.8 修订：从 3 工具到 2 工具——description 是饱和记忆视图

用户进一步指出关键事实：**Phase 2 的 description 设计本身就是饱和式记忆视图**——按写入顺序拼当前
未过期 Atom，带 [atom-id] 血缘标签，校验过一致性（见 [phase2-completion-and-phase3-handoff.md](./phase2-completion-and-phase3-handoff.md)
§5.3）。description 不是"摘要"，是结构化的完整记忆。

这意味着 expand 应该直接返 description，inspect 原语完全不需要。Atom 层的 SQLite 查询（evidence）
只在需要原始 Atom 文本、完整双时间字段、演化链、Episode provenance 这些 description 不包含的信息时
才用——是 **drill-down 而非高频调用**。

最终收敛到 2 工具方案：`expand` + `evidence`。

## 3. 当前方案

### 3.1 核心设计原则

1. **主动权在 sub-agent**：host 端零相关性决策。Core 只做物理约束（大小截断、visited 去重）和
   跨存储 hydration（evidence）。
2. **sub-agent 委托模式**：主 agent 通过 `ctx.subagents.start()` 委托主动检索给 sub-agent。
   sub-agent 在自己的 context 里跑探索循环，主 agent 只收到 structured_output 提交的 Finding。
   这是"agent 委托主动检索"故事，不是"Core 内部实现主动检索"。
3. **description 是主载体**：Phase 2 不变量直接利用——description 是饱和记忆视图，多数查询不需要
   drill-down。
4. **evidence 是 escape hatch**：只在需要原始 Atom / 完整时间 / 演化 / Episode 时调用。
5. **图结构信号交给 agent**：pagerank、community_id、edge_keywords 都返给 agent，agent 自己看到
   community_id 跨界可以决定停或继续——host 不替它判断。

### 3.2 三条判别标准的满足

| 标准                       | 当前方案                                                                                                                                            |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| 维护 graph-specific 状态   | ✓ frontier + visited + path + subgraph + progress 都是 sub-agent context 里的一等公民                                                              |
| Action space 图特定窄化    | ✓ 唯一动作是 expand / evidence；候选由 APOC 一次性返回，agent 自己挑选                                                                             |
| 输出 ReAct+search 做不到的 | ✓ path 访问轨迹（sub-agent 自己维护并提交） / 拓扑早停（agent 看 community_id + pagerank 自己决定） / progress 信号（sub-agent 自己评估 coverage） |

### 3.3 Core 暴露的两个原语

```
POST /memory/explore/expand
  in:  { frontier_ids[], hops, max_nodes,
         exploration_session_id, own_visited_ids[] }
  out: { neighbors: [{
           stable_id, kind, name, type,
           via_edge: { label, keywords, weight, from },
           description,            ← 饱和记忆视图，带 [atom-id] 标签
           atom_ids[],              ← 独立保存，方便直接调 evidence
           pagerank, community_id,  ← 仅结构信号
           degree
         }],
         truncated?, server_visited_count }

  实现要点:
    - APOC path.expandConfig 做 BFS、跳数上限、节点截断
    - 服务端 union own_visited + session_visited 去重
    - 超量按 degree 截断（物理上限，非相关性筛选）
    - description / atom_ids / pagerank / community_id / degree 全部从节点属性读取，零额外计算
    - exploration_session_id 在 Core 端维护 visited 集合，跨 sub-agent 自动同步

POST /memory/explore/evidence      ← 低频 drill-down
  in:  { atom_ids[] }
  out: { atoms: [{
           atom_id, content,           ← 原始 Atom 文本（非 description 视图）
           valid_at, invalid_at,       ← 完整双时间字段
           status, support_count,
           evolution: [{ kind, target_atom_id, reason }],  ← 演化链
           evidence_refs: [{
             episode_id, reference_at, source_uri
           }]
         }] }

  实现要点:
    - 跨存储（Neo4j atom_ids → SQLite Atom 内容）
    - 复用 Phase 2 已有 SQLite backend 方法
    - 包含演化链（REFINEMENT / TEMPORAL_SUCCESSOR / CONTRADICTION）
    - 包含 Episode provenance（episode_id / reference_at / source_uri）
```

### 3.4 Sub-agent 行为契约

sub-agent 拿到：

- `seed_frontier`：初始激活的 stable_id 列表（来自 mix recall）
- `sub_query`：拆分后的子查询
- `edge_intent`：子查询期望的边类型 keywords（可选）
- `budget`：max_tool_calls / max_tokens / per_explorer_timeout
- `exploration_session_id`：跨 sub-agent visited 同步用
- `outputSchema`：Finding 结构（注入式，受信，不由模型决定）

sub-agent 循环：

```
1. 调 expand(seed_frontier, hops=2, max_nodes=15)
2. 看返回的 ≤15 个候选的 description + 结构信号
3. agent 自己判断:
   a. 当前 evidence 是否足够回答 sub_query?
   b. 如果不够: 哪几个候选值得继续 expand? 哪几个值得 evidence drill-down?
   c. 重新生成 frontier 实体列表
4. 按需调 evidence(atom_ids) 拿原始 Atom / 双时间 / 演化 / Episode
5. 回到步骤 1，直到:
   - agent 判断足够回答
   - budget 耗尽
   - agent 判断无新进展
6. structured_output 提交 Finding
```

Finding schema（sub-agent 必须以 structured_output 提交）：

```typescript
{
  entities_found: string[],       // magi_entity_id 列表
  relations_found: string[],     // magi_relation_id 列表
  atoms_found: string[],          // atom_id 列表（已被 agent 认为相关的）
  evidence_refs: [{
    atom_id: string,
    episode_id: string,
    owner_revision: number        // 提交时 Core 端的 revision 快照
  }],
  path_taken: [{
    round: number,
    from_id: string,
    to_id: string,
    via_edge: string,
    decision_reason: string       // agent 自己写"为什么 expand 这个"
  }],
  coverage_self_assessment: {
    sub_question: string,
    verdict: "answered" | "partial" | "missing",
    gap_description?: string
  }[],
  stop_reason: "sufficient" | "budget_exhausted" | "no_progress" | "error",
  suggested_continuation?: {
    next_frontier: string[],
    rewritten_sub_query?: string
  }
}
```

### 3.5 主 agent 视角

主 agent（dsh）在 `explore` 记忆模式下：

1. 收到用户问题
2. 跑 mix recall 拿初始 anchors
3. 把 query 拆成 1-N 个 sub_query + edge_intent（一次 LLM 调用）
4. 按 sub_query 数量启动 1-N 个 sub-agent，每个拿一个 sub_query + 对应 anchors 子集
5. 等所有 sub-agent settle（或部分 settle 后早期收口）
6. 聚合 Finding：
   - 按 stable_id 去重
   - 检测 owner_revision 一致性（sub-agent 之间是否读到同一快照）
   - 合并 path_taken 形成完整访问轨迹
   - 检测 coverage：哪些 sub_question 已 answered，哪些仍 missing
7. 如果有 missing 且 budget 还允许：可选启动第二轮 sub-agent（默认关闭）
8. 把聚合 RecallBundle 注入主 agent context
9. 主 agent 生成最终回答

主 agent context **不直接持有探索过程**——它只看到 anchors → 聚合 RecallBundle。中间的
expand/evidence 调用都在 sub-agent context 里。这是"agent 委托主动检索"故事的实现机制。

## 4. 图信号与聚类

### 4.1 仅保留三个结构信号

| 信号             | 来源                      | 形态                                        | 重算时机                            |
| ---------------- | ------------------------- | ------------------------------------------- | ----------------------------------- |
| `pagerank`     | GDS`gds.pagerank.write` | 0~1 浮点                                    | projection_outbox revision 阈值触发 |
| `community_id` | GDS`gds.leiden.write`   | 字符串/整数 +`community_version` 单调递增 | 同上                                |
| `degree`       | 写入路径上即可算          | 整数                                        | 每次 owner 投影时增量更新           |

**为什么只这三个**：

- 它们跟 description 内容**完全正交**——agent 无法从 description 推出节点的拓扑位置。
- 它们是**纯结构信号**，让 agent 看到节点在图中的位置。
- 其他候选信号（atom_count / active_atom_count / latest_atom_valid_at / has_invalid_at /
  embed_similarity / edge_intent_match）**都已经在 description 里**或 agent 自己能从
  description + edge_keywords 推出，不需要单独返回。

### 4.2 PageRank 是什么

PageRank 是一个 0~1 之间的浮点数（GDS 默认未归一化时可能略大，我们归一化到 0~1），表示
**结构重要度**——一个节点重要，当且仅当有重要节点连向它。GDS 跑迭代算法：

```
PR(v) = (1 - d) + d × Σ [ PR(u) / out_degree(u) ]  for all u → v
```

`d=0.85` 默认。MAGI 规模（几百到几千节点）秒级跑完。跑完用 `gds.pagerank.write` 把分数物化到
每个 entity 节点的 `pagerank` 属性。`expand` 返回时直接读属性，不重新跑算法。

**关键限制**：PageRank 只看拓扑，不懂语义。一个高 PageRank 节点可能是"很多边连过来"但跟查询
完全无关。所以它是**结构信号，不是相关性信号**——agent 拿到后自己结合 description 判断。

### 4.3 聚类：选 Leiden，只做一层

| 算法              | 复杂度     | 速度                | 质量                                | 适合场景           |
| ----------------- | ---------- | ------------------- | ----------------------------------- | ------------------ |
| WCC               | O(V+E)     | 极快                | 太粗（连通就同社区）                | 不适合             |
| Label Propagation | 近线性     | 极快                | 中（随机性大）                      | 极大图、需频繁重算 |
| **Leiden**  | O(n log n) | 快（MAGI 规模秒级） | **高**（优于 Louvain）        | **推荐**     |
| Louvain           | O(n log n) | 快                  | 中高（可能欠分割）                  | Leiden 的前身      |
| SCC               | O(V+E)     | 极快                | 强连通（MAGI 图是混合方向，不适合） | 不适合             |

选 Leiden：它是 Louvain 的改进版，保证 "well-connected" 性质（每个社区内部连通），修了 Louvain
的退化 bug。MAGI 规模秒级跑完，重算成本可接受。

**层次聚类留给 MAG-26**：Leiden 本身支持 refinement tree，未来要做层次聚类时不必推倒重来。
MAG-25 v1 只跑一层 flat partition。

### 4.4 刷新策略

PageRank + Leiden 一起挂到 `projection_outbox` 的 revision 阈值上：

```
触发条件: distance_to_last_cluster_revision > 50 owners
       OR graph_size_delta > 20% since last cluster
执行:    gds.pagerank.write + gds.leiden.write → 物化到每个 entity 节点
成本:    MAGI 规模秒级，后台异步跑，不阻塞写入或查询
版本:    每次物化时 community_version 单调递增，expand 返回带版本号
```

**关键不变量**：每次聚类物化时同时记下 `community_version`（单调递增整数）。`expand` 返回的
`community_id` 旁附带 `community_version`，Finding 提交时也带 `community_version`。聚合层能
检测"这个 Finding 是基于版本 3 的聚类，但当前已是版本 4"——避免跨版本误判社区边界。

## 5. 预算和时延

### 5.1 默认预算

```
max_nodes_per_expand              15
max_hops_per_expand               2
max_tool_calls_per_sub_agent      8
max_tokens_per_sub_agent          4,000 (output budget)
per_sub_agent_timeout             15s
overall_timeout                   25s (无第二轮) / 40s (有第二轮)
max_sub_agents                    3 (默认)
第二轮                            默认关闭
```

### 5.2 时延分档

```
路径 A：explore-fast（Round 1 only）
  mix recall              2-5s
  query 分解              1-2s (一次 LLM 调用)
  3 sub-agent 并行        10-15s (瓶颈是最慢的一个)
  aggregate               <500ms
  ─────────────────────────────────
  recall 侧总计          ~14-22s
  + 主 agent 回答         3-5s
  全轮                    ~17-27s

路径 B：explore-deep（Round 2 启用）
  上面 + 10-15s (R2) + 1-2s (Gate2)
  全轮                    ~30-42s

路径 C：no_progress 早期终止
  mix + R1（partial）+ agent 判定无进展
  全轮                    ~12-18s，回退普通 mix 结果
```

对比当前普通 mix 检索 ~3-8s，主动探索确实慢 10-30s。**关键 mitigation 不是把它做快，而是让
"何时退化"由 agent 自己决定**：

- sub-agent 在自己的循环里判断"无新进展"就提前 stop，不需要等 overall_timeout
- 主 agent 等所有 sub-agent settle 或部分 settle 后早期收口（前 2 个 settle 且覆盖已足够 → 杀第 3 个）
- 退化通道显式：`stop_reason=no_progress` 时主 agent 在回答里说"主动探索未发现新增益，以下基于
  普通检索"

### 5.3 description 体积估算

description 饱和意味着单条候选 token 体积上升。预估：

- 单条 candidate 体积：300-800 tokens（description + 信号 + 边信息）
- max_nodes=15 → 单次 expand 返 4.5-12K tokens
- 5 轮累计：22-60K tokens（sub-agent context）

这是 sub-agent context 的合理上限。超过则需要：

- 降低 max_nodes 到 10
- dsh `compaction` 机制压缩早期轮次 expand 结果

## 6. 非目标（明确不在 MAG-25 v1 范围）

1. **完整 Atom hybrid 检索**：当前初始 anchor 仍由 LightRAG mix 召回，没有直接 Atom 向量/关键词
   混合索引。这影响初始锚点质量，但**不阻塞探索算法本身**。作为后续 issue。
2. **查询时双时间过滤**（`as_of` / `valid_at` range）：v1 不做。接口已预留 `temporal_filter?`
   参数但实现为空。时间型问题不进 v1 benchmark。
3. **社区隔离强制约束**：v1 返 `community_id` 给 agent，但 agent 可自由跨社区扩展。社区边界
   只是 hint，不是硬约束。等 MAG-26 落地层次聚类后再考虑强制约束。
4. **跨社区并发写入**：MAG-25 全部只读，所有 sub-agent 严格只读。社区级并发写入属于并发记忆
   演化，是独立议题。
5. **层次聚类 / Reflect 聚类摘要**：MAG-26（BALTHASAR）范围。MAG-25 v1 只跑 Leiden 一层
   flat partition。
6. **路径 B（topology-driven host-driven frontier）**：本 session 早期讨论过的另一范式，被
   "agent 完全自主"方案取代。不做。
7. **MAGI Core 内部 LLM 跑探索循环**：明确拒绝。Core 保持通用，探索算法在 dsh 插件层。

## 7. 开放问题

### 7.1 sub-agent continuable 模式与中间结果流回

长探索（5+ 轮）sub-agent 可能 timeout。dsh `tool-subagent` 支持 `one-shot` vs `continuable`
两种模式（[tool-subagent/src/index.ts](../sages/dsh/packages/subagent/tool-subagent/src/index.ts)）。需要确认：

- continuable 模式下 sub-agent 能否阶段性 commit Finding、parent 决定是否续？
- 如果不能，长探索要么用更大 timeout，要么用多轮 one-shot + parent 在中间决策

### 7.2 sub-agent 中间过程可观测性

debugging 和评测需要看 sub-agent 走过的路径。需要确认：

- dsh 是否有 sub-agent 调用日志的导出机制？
- sub-agent 的 expand 调用历史能否被回放用于评测？
- 如果没有，需要在 dsh 插件层加 trace 收集

### 7.3 query→edge_intent 分解的 prompt

开局一次 LLM 调用把 query 拆成 sub_questions + edge_intent：

```
"赵凯在西南项目里和谁一起工作？他导师是谁？"
→ sub_questions: ["赵凯在西南项目的同事", "赵凯的导师"]
→ edge_intent: {
    "同事": ["works_on", "collaborates", "team_member"],
    "导师": ["mentored_by", "supervised_by", "taught_by"]
  }
```

需要设计这个 prompt 的具体形态，包括：

- 如何让 LLM 知道 LightRAG 边 keywords 的常见模式（不能凭空造）
- 失败 fallback：如果 LLM 拆错或 edge_intent 不匹配实际边类型，sub-agent 仍能在纯 description
  判断下工作（edge_intent 只是 hint，不是硬过滤）

### 7.4 探索路径的压缩

长探索可能产生几十条 PathStep。需要：

- 预算上限（如 max_path_steps=30）
- 必要时压缩策略（合并同一边类型的连续步、丢弃低相关步）
- 压缩不能破坏可解释性

### 7.5 MAG-25 v1 工单描述的诚实重写

MAG-25 Linear 工单描述当前是"基于 dsh 的并发主动图谱检索与 Sub-Agent 树"。按本 session
讨论结论，应该改写为：

- 明确 v1 范围：agent 委托主动检索，2 工具极简 Core 原语，sub-agent 完全自主决策
- 明确非目标：Atom hybrid、双时间过滤、社区隔离强制约束、层次聚类、并发长期记忆写入
- 明确验收：对照评测（普通 mix vs sub-agent explore），看多跳召回、证据多样性、时延、token
- 故事定性："agent 委托主动检索"——agent 选择委托、选择起点、选择预算、接收结构化结果

**注意**：本 session 不修改 Linear 工单状态或描述。等用户确认方案后再单独操作。

## 8. 与其他工单的关系

| 工单                | 关系                                                                                                                                                  |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| MAG-17（Done）      | 三期方向收敛，明确"主动探索以结构化 ExplorationTask/Evidence/Result 交换，不依赖 Blackboard/MQP/MCC"——本方案遵守此边界                              |
| MAG-21（In Review） | dsh 已跑通，MAGI 插件已有 workspace/extracted/recall/auto/manual/off 模式。MAG-25 新增`explore` 模式，与现有模式正交（不与 active/manual/off 冲突） |
| MAG-26（Todo）      | BALTHASAR 聚类研究。MAG-25 v1 已用 Leiden 一层 flat partition 作为 community_id 来源；层次聚类、Reflect 摘要留给 MAG-26                               |
| MAG-27（Backlog）   | 评估基线。MAG-25 必须配合 MAG-27 做对照评测，否则"主动检索收益"无法验证                                                                               |
| MAG-36（In Review） | Session 内可切换记忆模式。MAG-25 的`explore` 模式应作为第四个选项接入                                                                               |

## 9. 验收标准

按 MAG-25 工单原描述 + 本 session 收敛：

1. **单 sub-agent 路径先跑通**：单 sub-agent 从单一起点完成 expand → 判断 → 继续扩展 →
   提交 Finding 闭环。
2. **多 sub-agent 并发**：3 个不同起点并发探索，返回结构化 Finding。部分失败仍能返回结果。
3. **对照评测**：固定样本上对比"普通 mix / 单 sub-agent / 3 sub-agent 并发 / 可选第二轮"四档，
   在多跳召回、证据多样性、时延、token 上有可测差异。
4. **退化通道**：无收益或错误扩散明显时，能关闭第二轮或退化为单 sub-agent，最坏回退普通 mix。
5. **可解释性**：RecallBundle 带 path 访问轨迹，能回答"为什么找到这个 Atom"。
6. **资源回收**：sub-agent 取消、超时、关闭不泄漏 session 或 Memory Handle。

## 10. 代码导航（实现时参考）

| 主题                                          | 主要文件                                                                     |
| --------------------------------------------- | ---------------------------------------------------------------------------- |
| LightRAG 检索主流程                           | `src/magi_core/operate.py`                                                 |
| 当前 /query/data 格式化（需修复丢 ID 问题）   | `src/magi_core/utils.py:5239`                                              |
| APOC 已用模式（subgraphAll）                  | `src/magi_core/kg/neo4j_impl.py:1373`                                      |
| Phase 2 description 物化与 [atom-id] 血缘     | `src/magi_core/operate.py` + `src/magi_core/memory/adapter.py`           |
| SQLite memory backend（evidence 复用）        | `src/magi_core/backend/sqlite/backend.py`                                  |
| Memory Core 模型                              | `src/magi_core/memory/models.py`                                           |
| 公开 MagiAPI                                  | `src/interface/api.py`                                                     |
| HTTP 路由（新增 expand/evidence）             | `src/magi_core/api/routers/memory_routes.py`                               |
| dsh MAGI 插件（新增 explore 模式 + 原语工具） | `sages/dsh/magi-memo/src/index.ts`                                         |
| dsh sub-agent 工具                            | `sages/dsh/packages/subagent/tool-subagent/src/index.ts`                   |
| dsh structured output runtime                 | `sages/dsh/packages/subagent/subagent-in-process-driver/src/structured.ts` |
| Phase 2 完成与交接                            | `doc/phase2-completion-and-phase3-handoff.md`                              |

---

本文档从 2026-08-19 的设计讨论稿演进而来。MAG-38 已落地 v1；第 2–10 节保留为决策历史，
其中的多 Agent、path、社区信号、APOC 与服务端 exploration session 均不代表 v1 已实现范围。
