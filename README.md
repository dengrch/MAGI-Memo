# MAGI Memo：面向 Agent 的三贤者记忆协商模块

## 1. 动机：从 RAG 到可协商的个人记忆

早期的 LLM 记忆设计大多从 RAG 开始。

它的基本思路是：

```text
外部文档 / 历史记录
  ↓
切分为 chunk
  ↓
embedding / keyword index
  ↓
top-k retrieval
  ↓
塞入 LLM context
```

这种方式解决了最直接的事实召回问题，但很快暴露出一个核心缺陷：

> 普通 RAG 擅长局部片段命中，却难以回答全局性问题。

例如，当用户询问：

```text
我长期以来关注的研究方向是什么？
这个项目和我之前讨论过的方向有什么关系？
过去几个月我在某个主题上的思路如何演化？
我现在这个想法是不是继承了之前某条技术路线？
```

这些问题往往不是命中几个 chunk 就能解决的。

它们需要跨越：

```text
大量片段
长时间跨度
多层语义抽象
实体与实体之间的关系
用户长期偏好
项目演化脉络
```

为了缓解普通 RAG 的局部性，人们开始引入层次化 summary、树状索引、文档主题摘要等结构。它们可以在一定程度上回答全局问题，但树状结构通常预设了很强的父子层级关系，而现实中的个人记忆并不总是树状的。

真实记忆更像一张不断变化的网络：

```text
一个项目会连接多个技术概念；
一个偏好会反复出现在不同对话；
一个旧想法会在几个月后被重新激活；
一个新方向可能同时继承多个历史主题；
同一条记忆可能既是事实，也是偏好，也是项目线索。
```

于是图谱被引入 RAG 和记忆系统。GraphRAG、LightRAG、HippoRAG 等方向试图通过实体、关系、社区、子图和路径结构补足传统 RAG 的不足。

图谱结构带来了几个重要能力：

1. 通过实体链接跨越 chunk 边界；
2. 通过关系连接分散在不同上下文中的信息；
3. 通过子图扩展发现多跳关联；
4. 通过社区或主题结构形成更高层级抽象；
5. 通过 evidence trace 保持可解释性。

但图谱记忆的代价也很明显。

为了构建可用图谱，系统通常需要大量 LLM 参与：

```text
实体抽取
关系抽取
实体合并
关系合并
社区总结
全局摘要
查询时子图解释
冲突判断
```

这使图谱记忆在构建成本、更新成本和查询时延上都变得很重。

对于个人记忆系统而言，这个问题更加尖锐。个人记忆不是一次性构建的静态知识库，而是持续增长、持续变化、可能冲突、可能过期、可能被用户纠正的动态系统。

因此，真正的个人记忆系统不仅要“查得到”，还要能够：

1. 持续写入；
2. 持续更新；
3. 处理冲突；
4. 处理过期；
5. 判断哪些记忆值得扩展；
6. 判断什么时候应该停止；
7. 在不同记忆范式之间选择合适的调用方式；
8. 为上层 Agent 构造可用、可解释、可收敛的上下文。

MAGI Memo 就是在这个问题背景下出现的。

---

## 2. MAGI 的来源与隐喻

MAGI 的灵感来自《新世纪福音战士》中的 MAGI System。

在 EVA 中，MAGI 是由三台超级计算机组成的决策系统，分别承载开发者赤木直子博士的三种人格侧面：

```text
科学家
母亲
女人
```

三台计算机不是简单并行计算，而是从不同人格视角出发，对重大问题进行判断、协商和表决。

MAGI Memo 借用的是这个核心思想：

> 一个复杂系统不应该只有单一判断视角，而应该由多个认知视角共同参与决策。

在 MAGI Memo 中，三贤者不再对应 EVA 原作中的人格身份，而是对应个人记忆系统中的三种认知功能：

```text
CASPER·3      = Intuition / 直觉
MELCHIOR·1    = Analysis / 分析
BALTHASAR·2   = Judgment / 判断
```

也可以概括为：

```text
CASPER sees.
MELCHIOR explains.
BALTHASAR decides.
```

中文对应为：

```text
CASPER 直觉命中。
MELCHIOR 结构分析。
BALTHASAR 全局裁决。
```

这个隐喻不是视觉风格，而是系统架构的核心。

MAGI Memo 的目标不是做一个更大的检索器，而是让不同记忆视角形成快速、可约束、可回放的协商。

---

## 3. 设计转向：从记忆系统到 Agentic Memory Council

最初的 MAGI Memo 可以被理解为一个可协商的个人记忆召回内核：

```text
User Query
  ↓
BALTHASAR global judgment
  ↓
CASPER fast search / activation
  ↓
MELCHIOR graph analysis
  ↓
BALTHASAR pruning / stop / MCC
  ↓
上层模型回答
```

这个版本的核心是：

```text
不同记忆范式通过 MQP 交换 query；
CASPER 负责快速召回；
MELCHIOR 负责关系分析；
BALTHASAR 负责全局判断；
最终生成 MCC。
```

但继续推演后会遇到一个关键矛盾：

如果底层选择 Neo4j 或 LightRAG 这样的图谱记忆后端，那么同一个实例本身就可能同时承载：

```text
vector search
keyword search
entity lookup
graph traversal
hybrid retrieval
relation expansion
```

这时如果把 CASPER 定义成“向量检索模块”，把 MELCHIOR 定义成“图谱检索模块”，二者就会被底层数据库能力吞掉。

换句话说：

```text
如果三贤者只是三个检索器包装器，
MAGI Memo 就没有足够独特的系统意义。
```

因此，MAGI Memo 的定位需要进一步改变。

新版本中，MAGI Memo 不再被定义为某种 RAG 或图谱记忆后端，而是被定义为：

```text
一个面向 Host Agent 接入的多子 Agent 记忆协商模块。
```

它不替代 LightRAG、RAG-Anything、Neo4j。

它的职责是：

```text
组织多个记忆 Agent 如何调用、讨论、互证、裁决和提交这些后端能力。
```

这使 MAGI Memo 从：

```text
可协商 RAG
```

演化为：

```text
Agentic Memory Council
```

在工程形态上，第一版倾向采用：

```text
对外：MCP-first memory server
对内：轻量 Pi Agent / Pi-style sub-agent
```

也就是说，MAGI Memo 首先作为一个通用 MCP 记忆服务挂载到 Host Agent 上。Codex、Pi Agent 或其他 Host Agent 都可以通过 MCP 调用它。

同时，MAGI Memo 内部的 CASPER、MELCHIOR、BALTHASAR 可以采用轻量化 Pi Agent 范式运行。它们在一次 search、ingest、update、forget 或 reflect 调用中按需启动，使用各自独立 workspace 与 `AGENT.md` 描述自身的记忆角色、协商风格、输出协议和工具边界。

这意味着：

```text
MCP 负责对外稳定；
Pi-style sub-agent 负责对内灵活；
LightRAG / RAG-Anything / Neo4j 负责内部记忆能力。
```

---

## 4. 总体架构

```text
Host Agent
    │
    │ search / ingest / update / forget / reflect / status / trace
    ▼
┌────────────────────────────────────┐
│             MAGI Memo              │
│                                    │
│   CASPER ←→ Shared Blackboard ←→ MELCHIOR
│                    ↑               │
│               BALTHASAR            │
└────────────────────┬───────────────┘
                     │ GraphPatch
                     ▼
          Deterministic Commit Service
                     │
                     ▼
                   Neo4j
```

MAGI Memo 作为 Host Agent 可接入的 MCP 记忆模块，对外暴露一组稳定工具。

第一版工具名不加 `magi_` 前缀，因为 MCP server 本身已经代表 MAGI Memo：

```text
search     协商式记忆搜索
ingest     写入新记忆
update     更新已有记忆、实体、路径或关系
forget     软删除 / 失效标记
reflect    内部反思、整理、冲突合并
status     查看 MAGI Memo 状态
trace      查看运行时追踪
```

Host Agent 不需要知道 MAGI 内部如何启动三个子 Agent，也不需要知道 LightRAG 用了什么 query mode、Neo4j 跑了什么 Cypher、RAG-Anything 如何解析多模态内容。

这些都属于 MAGI Memo 内部实现。

MAGI Memo 内部包含三个同层级子 Agent：

```text
CASPER·3
MELCHIOR·1
BALTHASAR·2
```

它们共享：

```text
Shared Blackboard
Neo4j graph instance
LightRAG / RAG-Anything memory backend
MAGI Query Protocol
GraphPatch proposal format
```

但它们各自拥有独立的：

```text
system prompt
tool handle
filesystem workspace
AGENT.md
working memory
search policy
decision policy
```

三贤者不是三个数据库，不是三个 Python class，也不是三个普通 retriever，而是三个围绕同一记忆底座工作的专业记忆子 Agent。

在 Pi-style sub-agent 范式下，每个子 Agent 的核心认知逻辑主要由其独立 workspace 中的 `AGENT.md` 描述。

这些 `AGENT.md` 不应该继承通用 coding agent 的偏好，而应该定义：

```text
记忆角色
协商边界
输出格式
工具权限
停止原则
禁止事项
```

例如：

```text
CASPER/AGENT.md    定义直觉激活风格；
MELCHIOR/AGENT.md  定义图谱分析风格；
BALTHASAR/AGENT.md 定义全局裁决风格。
```

---

## 5. 三贤者的新定义

### 5.1 CASPER·3：Intuition / 直觉

CASPER 负责快速激活。

它的工作不是完整解释，也不是深度图谱分析，而是快速回答：

```text
这次 query 最像什么？
哪些原始记忆被激活？
哪些实体种子被激活？
哪些关键词或近期记忆值得注意？
有没有明显应该交给 MELCHIOR 深挖的入口？
```

CASPER 可以调用：

```text
LightRAG naive mode
LightRAG hybrid / mix mode 的轻量查询
关键词检索
语义向量检索
entity seed extraction
recent memory lookup
```

CASPER 的认知风格应该是：

```text
快
短
局部
直觉式
不做最终裁决
```

它的输出应该极简，因为输出大概率会被其他 Agent 继续消费：

```json
{
  "agent": "CASPER",
  "type": "activation_result",
  "memory_hits": ["mem_001", "mem_014"],
  "entity_seeds": ["MAGI Memo", "LightRAG", "RAG-Anything"],
  "note": "当前问题主要激活图谱底座选型和多 Agent 记忆协商方向。"
}
```

CASPER 可以是小模型或低成本 Agent。

它的价值不在于“更聪明”，而在于快速激活候选空间。

---

### 5.2 MELCHIOR·1：Analysis / 分析

MELCHIOR 负责深入图谱搜索和关系分析。

它的工作不是替代 Neo4j，也不是构建完整世界图谱，而是在被激活的记忆范围上回答：

```text
这些实体之间有什么关系？
是否存在关系路径？
是否存在主题簇？
是否存在冲突？
是否存在项目演化脉络？
哪些关系有证据支撑？
哪些关系值得进入 MCC？
哪些更新值得形成 GraphPatch？
```

MELCHIOR 可以调用：

```text
LightRAG local mode
LightRAG global mode
LightRAG mix mode
Neo4j Cypher query
graph traversal
entity neighborhood search
relation / path / community search
```

它的认知风格应该是：

```text
结构化
证据驱动
关系敏感
路径敏感
不直接写图
```

示例输出：

```json
{
  "agent": "MELCHIOR",
  "type": "relation_result",
  "paths": [
    {
      "from": "MAGI Memo",
      "to": "LightRAG",
      "relation": "candidate_memory_backend",
      "evidence": ["mem_014"]
    }
  ],
  "note": "LightRAG 更适合作为动态图谱记忆底座，而不是完整替代 MAGI 协商层。"
}
```

MELCHIOR 只能提出 GraphPatch proposal。

它不应该直接提交最终写入。

---

### 5.3 BALTHASAR·2：Judgment / 判断

BALTHASAR 是三 Agent 记忆系统的 leader。

它掌握更完整的全局信息，包括：

```text
Global.md
User profile
长期偏好
项目方向
当前任务目标
Blackboard 当前状态
CASPER / MELCHIOR 的结果
预算与停止条件
```

BALTHASAR 负责：

```text
判断是否需要启动协商；
决定先问 CASPER 还是 MELCHIOR；
选择 LightRAG query mode；
控制协商轮次；
判断信息是否足够；
裁决 GraphPatch 是否允许提交；
构造最终 MCC；
向 Host Agent 返回记忆结果。
```

BALTHASAR 的职责确实很重。

但这是系统定义决定的，因为只有它掌握最完整的全局信息和最终任务目标。

工程上要避免的问题不是“让 BALTHASAR 变轻”，而是避免它变成一个不可调试的大 prompt。

因此后续应将 BALTHASAR 拆成固定步骤：

```text
Global Check
Gap Formulation
Agent Routing
Candidate Judge
Stop Decision
MCC Builder
Commit Approval
```

这样 BALTHASAR 仍然是全局判断者，但每一步都有清晰输入输出，可以日志化、测试和替换。

---

## 6. Shared Blackboard

Blackboard 是三个子 Agent 的共享消息空间。

它像一个群聊空间，但不应该只是自然语言聊天记录，而应该是结构化 message buffer。

它负责记录：

```text
Agent 消息
MQP 请求
检索结果
关系分析
GraphPatch proposal
冲突候选
停止信号
MCC 草稿
提交结果
失败记录
```

一个最小消息结构可以是：

```json
{
  "message_id": "bb_001",
  "session_id": "session_001",
  "round": 1,
  "from_agent": "BALTHASAR",
  "to_agent": "CASPER",
  "type": "query",
  "payload": {
    "intent": "activate_memory",
    "query": "比较 Neo4j GraphRAG、LightRAG 和 RAG-Anything 对 MAGI Memo 的适配性"
  },
  "priority": 0.82,
  "ttl": 2,
  "created_at": "2026-07-27T00:00:00Z"
}
```

Blackboard 的关键价值：

1. 支持 Agent 间直接交互；
2. 支持同层级协商，而不是只有下级向上级汇报；
3. 支持回放和调试；
4. 支持去重和轮次控制；
5. 支持异步执行；
6. 支持后续评估协商质量。

---

## 7. 记忆底座选型

当前倾向：

```text
LightRAG       = 主文本 / 图谱记忆底座
RAG-Anything  = 多模态摄入与多模态 RAG 扩展
Neo4j         = 图数据库实例
Blackboard    = 三 Agent 协商消息层
MAGI Runtime  = 调度、协议、裁决与提交层
```

MAGI Memo 不应该把自己绑定成某个后端的 wrapper。

但在当前阶段，需要一个确定的实例对象来推动工程设计，因此可以先选：

```text
Neo4j 作为共享图数据库实例；
LightRAG 作为主要图谱 RAG 底座；
RAG-Anything 作为多模态 ingestion / retrieval 扩展。
```

### 7.1 LightRAG

LightRAG 适合作为 MAGI Memo 的核心记忆后端，因为它关注：

```text
轻量图谱 RAG
向量 + 图谱双层检索
多种 query mode
较低查询成本
增量更新
Neo4j graph storage
文档删除与局部重建
```

LightRAG 的 query mode 可以自然映射到三贤者：

```text
naive / hybrid / mix  → CASPER 快速激活
local / global / mix  → MELCHIOR 关系分析
mode selection        → BALTHASAR 判断调度
```

这里的关键是：

```text
LightRAG 提供能力；
MAGI Memo 决定谁在什么时候以什么意图调用这些能力。
```

### 7.2 RAG-Anything

RAG-Anything 更适合作为多模态入口，而不是单独替代 MAGI Memo。

它负责：

```text
PDF
Office documents
images
tables
equations
charts
multimodal parsing
multimodal content indexing
VLM-enhanced query
```

在 MAGI Memo 中，它可以作为 ingestion layer：

```text
raw multimodal data
  ↓
RAG-Anything parsing / analysis
  ↓
LightRAG indexing
  ↓
Neo4j graph storage
  ↓
MAGI agents search / analysis / judgment
```

### 7.3 Neo4j

Neo4j 是当前确定的图数据库实例。

三个 Agent 可以共享同一个 Neo4j，但应通过不同工具句柄和不同权限策略访问。

建议原则：

```text
CASPER: 读为主，轻量检索
MELCHIOR: 读为主，深度图查询
BALTHASAR: 读 + 审批写入
Commit Service: 唯一真正写入方
```

这样可以避免多个 Agent 直接并发写图造成状态混乱。

---

## 8. MQP：MAGI Query Protocol

MQP 的定位也随新架构发生变化。

它不再只是“检索 query 的 JSON 格式”，而应该被定义为：

```text
MAGI 子 Agent 之间交换记忆任务、证据、疑问、结果和提交请求的协议。
```

如果没有 MQP，系统会退化成：

```text
三个 Agent 各查各的；
最后把结果拼接给 Host Agent。
```

而有了 MQP，系统才真正变成：

```text
一个记忆 Agent 发现缺口；
将缺口翻译成结构化消息；
另一个记忆 Agent 根据自己的工具和视角补全；
BALTHASAR 判断这些结果是否足够；
必要时继续协商；
最终形成 MCC 或 GraphPatch。
```

当前不急于完整设计 MQP。

第一阶段只需要最小可运行结构：

```json
{
  "id": "mqp_001",
  "round": 1,
  "from": "BALTHASAR",
  "to": "CASPER",
  "intent": "activate_memory",
  "query": "用户当前讨论 MAGI Memo 的新架构版本",
  "basis": ["host_query"],
  "budget": {
    "top_k": 5,
    "ttl": 2
  }
}
```

后续再逐步加入：

```text
expected_response
constraints
risk_control
information_gap
deduplicate_against
confidence_policy
commit_policy
```

MQP intent 可以先保留有限枚举：

```text
activate_memory       激活记忆
find_evidence         查找证据
expand_relation       扩展关系
expand_subgraph       扩展子图
resolve_conflict      解决冲突
judge_answerability   判断是否足够回答
propose_graph_patch   提出图更新
approve_commit        审批提交
build_mcc             构造共识上下文
stop                  停止协商
```

---

## 9. MCP 对外工具

MAGI Memo 第一版以 MCP server 的形式对外暴露工具。

工具名保持简洁：

```text
search
ingest
update
forget
reflect
status
trace
```

这些工具不是 LightRAG 或 Neo4j 的直接透传，而是 MAGI Memo 的稳定外部语义。

### 9.1 search

`search` 是 MAGI Memo 最核心的能力。

它通过 CASPER、MELCHIOR、BALTHASAR 三个子 Agent 实现协商机制的记忆搜索。

它不是简单 top-k retrieval，而是：

```text
Host Agent query
  ↓
BALTHASAR 判断搜索意图与预算
  ↓
CASPER 快速激活记忆、实体和关键词
  ↓
MELCHIOR 搜索图谱路径、关系和结构证据
  ↓
BALTHASAR 裁决哪些内容进入上下文
  ↓
返回 MCC
```

`search` 可以返回：

```text
结构化 MCC
memory ids
激活实体
graph 搜索路径
关系证据
不确定性
回答指导
可选 trace id
```

Host Agent 可以根据这些结构化信息继续判断下一步是否需要 `update`、`forget` 或再次 `search`。

### 9.2 ingest

`ingest` 负责写入新记忆。

它配合 LightRAG 后端和 Neo4j 实例完成确定性写入。

MAGI Memo 默认假设 Host Agent 已经决定“这条内容应该被记住”。

因此，`ingest` 前的价值判断不属于 MAGI Memo 默认职责：

```text
ingest 前是否值得记住 → Host Agent 决定；
ingest 如何可靠写入   → MAGI Memo 决定；
ingest 后是否整理反思 → MAGI Memo 可选触发。
```

输入可以来自：

```text
conversation
note
file
webpage
code
image
PDF / Office document
其他多模态资产
```

内部流程可以是：

```text
Host Agent ingest
  ↓
MAGI ingest service
  ↓
raw memory / episode registry
  ↓
RAG-Anything parsing if multimodal
  ↓
LightRAG indexing
  ↓
LightRAG / Neo4j backend stores chunks, entities, relations
  ↓
Blackboard ingest event
  ↓
optional post-ingest policy
```

第一版 `ingest` 的目标是可靠写入，而不是每次都启动三贤者协商。

BALTHASAR 不应该成为每条写入的门卫。它只在写入后根据策略判断是否需要进一步动作：

```text
no action
quick review
trigger reflect
ask CASPER re-activate related memories
ask MELCHIOR inspect graph relations
propose / approve GraphPatch
```

复杂的实体合并、关系合并、偏好抽取和冲突判断可以交给后续 `reflect`。

如果 Host Agent 希望在写入前先判断是否值得记忆，应显式调用 `search` 或未来的 judge 类流程，而不是让 `ingest` 默认承担该判断。

### 9.3 update

`update` 负责更新已有记忆、实体、关系或路径解释。

这里保留 `update` 这个外部工具名，因为它对 Host Agent 更自然。

但 MAGI Memo 内部不应该把 update 理解为“直接修改数据库”。

更安全的实现是：

```text
Host Agent update request
  ↓
MemoryPatch / GraphPatch proposal
  ↓
Blackboard
  ↓
BALTHASAR approval if needed
  ↓
Commit Service
  ↓
LightRAG / Neo4j update
```

`update` 适用于：

```text
修正某条记忆；
补充某条记忆的 metadata；
合并两个实体；
更新关系描述；
给关系增加证据；
标记某条路径解释更可靠；
将用户纠正转化为新的记忆版本。
```

Host Agent 可以根据 `search` 返回的结构化信息发起 update。

例如 `search` 返回：

```text
memory_id
activated_entities
graph_paths
relation_ids
uncertainties
```

Host Agent 可以据此决定具体更新哪条记忆、哪个实体、哪条关系或哪条路径解释。

第一阶段 `update` 暂不承担删除语义。

从 LightRAG 后端角度看，`update` 的实现需要谨慎分层：

```text
entity / relation update:
可以优先实现，适合映射到 GraphPatch。

memory metadata update:
可以优先实现，适合在 MAGI 自己的 memory registry 中维护。

document-level content update:
不建议第一版承诺直接原地更新。
更稳的策略是生成新版本，必要时让旧版本失效，再重新 ingest。
```

原因是文档内容一旦变化，chunk、embedding、实体、关系、路径证据都可能变化。

因此，第一版的 `update` 更适合表达：

```text
受控 patch
版本化修正
关系补充
实体合并
证据追加
```

而不是任意覆盖原始文档。

### 9.4 forget

`forget` 负责软删除、失效标记、降权或遗忘策略。

它可以配合 `update` 使用，但不建议合并进 `update` 作为同一个对外工具。

第一版保持：

```text
update = 修改 / 补充 / 合并
forget = 失效 / 隐藏 / 降权 / 软删除
```

二者内部都可以表示为 patch，但对 Host Agent 来说意图和风险不同。未来如果支持 hard delete，`forget` 也需要单独的确认和审计策略。

内部实现上，`forget` 可以复用 `update` 的 MemoryPatch / GraphPatch 管线。

例如：

```json
{
  "target": "memory:mem_001",
  "policy": "soft",
  "patch": {
    "valid": false,
    "invalid_reason": "user_requested_forget",
    "invalid_at": "2026-07-28T00:00:00Z"
  }
}
```

第一版默认只做 soft forget。

hard delete 可以后续再作为高风险选项设计。

第一版可以这样实现：

```text
forget(memory_id)
  ↓
MemoryPatch(valid=false)
  ↓
Commit Service
  ↓
MAGI registry / Neo4j edge property 标记失效
  ↓
search 时默认过滤无效记忆
```

等系统稳定后，再考虑真正调用 LightRAG / Neo4j 的 hard delete。

### 9.5 reflect

`reflect` 是 MAGI Memo 的内部反思和整理能力。

这个名字很适合当前架构，因为三贤者会围绕一批记忆反复协商、弹回、修正和收敛。

`reflect` 适用于：

```text
整理最近记忆；
提取稳定偏好；
发现重复实体；
发现冲突记忆；
合并关系；
生成 Global.md 更新候选；
生成 GraphPatch proposal；
发现值得长期保留的主题。
```

它不是普通搜索，而是主动整理。

一个典型流程：

```text
BALTHASAR 选择反思范围
  ↓
CASPER 激活相关记忆集合
  ↓
MELCHIOR 分析实体、关系、冲突和演化路径
  ↓
BALTHASAR 判断哪些整理值得提交
  ↓
GraphPatch / MCC / reflection report
```

### 9.6 status

`status` 用于查看 MAGI Memo 状态。

它可以返回：

```text
MCP server 状态
LightRAG 状态
Neo4j 连接状态
RAG-Anything 状态
Blackboard 状态
当前 workspace
pending ingest jobs
pending GraphPatch
最近一次错误
```

### 9.7 trace

`trace` 用于查看运行时追踪。

它主要服务调试和评估。

输入通常是：

```text
session_id
trace_id
message_id
```

输出可以包括：

```text
Blackboard 消息
CASPER 输出
MELCHIOR 输出
BALTHASAR 裁决
LightRAG query mode
Neo4j 查询摘要
GraphPatch 状态
MCC 构造过程
```

`trace` 不一定给普通 Host Agent 高频调用，但它对于调试 MAGI Memo 是否真的产生了有效协商非常重要。

---

## 10. MCC：MAGI Consensus Context

MCC 仍然是 MAGI Memo 给 Host Agent 的主要输出之一。

但它不只是 RAG context，而是：

```text
三 Agent 协商后的记忆共识包。
```

普通 RAG context 通常是：

```text
top-k chunk 拼接
```

而 MCC 应该是：

```text
经过直觉激活、图谱分析、全局判断后形成的上下文包。
```

它应该包含：

1. 已确认事实；
2. 关键证据；
3. 关系解释；
4. 冲突与不确定性；
5. 用户长期偏好；
6. 当前回答指导；
7. 不应过度推断的边界；
8. 可选 GraphPatch 结果。

示例：

```json
{
  "mcc_id": "mcc_001",
  "status": "consensus_reached",
  "query": "MAGI Memo 当前架构如何定位？",
  "confirmed_context": [
    "MAGI Memo 当前更适合定义为面向 Agent 的多子 Agent 记忆协商模块。",
    "LightRAG 倾向作为核心文本和图谱记忆底座。",
    "RAG-Anything 倾向作为多模态摄入层。",
    "Neo4j 是共享图数据库实例。"
  ],
  "remaining_uncertainties": [
    "MQP schema 尚未详细设计。",
    "三 Agent 的具体系统提示词与工具权限尚未确定。",
    "GraphPatch 的事务模型和冲突策略尚未确定。"
  ],
  "answering_guidance": [
    "不要把 MAGI Memo 描述成单一 RAG 后端。",
    "强调 MAGI 的价值在多 Agent 记忆协商与确定性写入。"
  ]
}
```

---

## 11. GraphPatch 与确定性提交

三个 Agent 不应该随意直接修改 Neo4j。

LightRAG 基础索引写入由 LightRAG 自己负责。

MAGI 子 Agent 主动提出的更新、合并、修正、失效和高层关系调整，应该先形成 GraphPatch proposal。

示例：

```json
{
  "patch_id": "patch_001",
  "op": "merge_relation",
  "source": "MAGI Memo",
  "target": "LightRAG",
  "relation": "uses_as_memory_backend",
  "evidence": ["mem_001", "bb_014"],
  "confidence": 0.78,
  "proposed_by": "MELCHIOR",
  "requires_approval": true
}
```

Commit Service 负责：

```text
schema validation
idempotency check
conflict detection
BALTHASAR approval
Neo4j transaction
commit log
blackboard notification
```

这让 MAGI Memo 可以保持：

```text
Agentic reasoning
Deterministic writing
Auditable memory update
```

---

## 12. 并发与一致性原则

MAGI Memo 会存在多个 Agent 同时读写候选消息、同时读取 Neo4j、同时提出 GraphPatch 的情况。

因此需要明确：

```text
Blackboard 可以并发写入；
Neo4j 读可以并发；
LightRAG 基础索引写入由 LightRAG 管线负责；
MAGI 主动 patch 写入必须收敛到 Commit Service；
GraphPatch 必须幂等；
Entity / Memory / Relation 必须有唯一 ID；
重要写入必须可回滚或可标记失效；
不要物理删除重要记忆，优先 soft delete / invalid_at / superseded_by。
```

Neo4j 自身提供事务、锁和约束能力，但 MAGI Memo 不应该把全部一致性责任都丢给数据库。

系统层应该主动控制：

```text
single writer
commit queue
version field
idempotency key
unique constraint
append-only audit log
```

---

## 13. 收敛机制

收敛机制是 MAGI Memo 能否真正可用的关键。

如果没有收敛机制，三个子 Agent 之间的消息交换会产生 query explosion，导致时延、成本和状态复杂度失控。

当前阶段，收敛机制主要服务 `search`。

`reflect` 未来也需要收敛机制，但它的目标不是回答当前问题，而是整理记忆、合并冲突、生成候选更新。因此 `reflect` 的收敛规则应该在其设计展开后单独细化。

### 13.1 BALTHASAR-led 协商状态机

`search` 的第一版状态机可以保持简单：

```text
Host Agent search
  ↓
BALTHASAR Global Check
  ↓
Enough?
  ├── Yes → Build MCC directly
  └── No  → Send MQP to CASPER / MELCHIOR
              ↓
          CASPER Activation
              ↓
          Blackboard
              ↓
          MELCHIOR Relation Analysis
              ↓
          Blackboard
              ↓
          BALTHASAR Batch Judge
              ↓
          Enough?
              ├── Yes → MCC / Stop
              └── No  → Continue until budget limit
```

`search` 默认只生成 MCC，不默认写入。

如果 `search` 过程中发现明显需要更新的事实、关系或失效记忆，可以返回 candidate patch，但是否调用 `update` 或 `forget` 应交给 Host Agent 或后续策略决定。

### 13.2 局部停止与全局停止

CASPER 可以局部停止：

```text
没有足够新召回内容；
召回结果重复；
top-k 分数低；
query novelty 低；
已达到召回预算。
```

MELCHIOR 可以局部停止：

```text
没有足够相关实体；
子图扩展增益低；
关系路径置信度低；
max_hop / max_nodes 达到上限；
图扩展没有带来新的有效关系。
```

BALTHASAR 负责全局停止：

```text
已经足以回答；
轮次达到限制；
时间 / token / query 预算达到限制；
剩余不确定性无法继续解决；
candidate MQP 的潜力不足；
MCC 草稿已经稳定。
```

### 13.3 Candidate Pool + Batch Gate

MAGI Memo 不应该让 BALTHASAR 对每个候选逐个判断。

更合适的方式是：

```text
cheap expansion
  ↓
candidate pool
  ↓
cheap heuristic filtering
  ↓
batch package
  ↓
BALTHASAR batch judge
  ↓
top-N MQP routing
```

这可以将 LLM 调用次数从：

```text
O(number of candidates)
```

降低为：

```text
O(number of batches)
```

### 13.4 Frontier Budget

每轮只允许扩展有限数量的 frontier：

```text
max_frontier_per_round = N
```

无论候选池有多大，真正进入下一轮的 MQP、实体或子图都必须受限。

这类似 beam search。

### 13.5 待细化：search 与 reflect 的不同收敛目标

`search` 的收敛目标是：

```text
足够回答当前 Host Agent query；
返回结构化 MCC；
控制轮次、延迟和 token 成本。
```

`reflect` 的收敛目标会不同：

```text
找出值得整理的记忆；
发现重复、冲突、过期或稳定偏好；
生成候选 MemoryPatch / GraphPatch；
避免过度整理和无意义合并。
```

因此，`reflect` 不应该直接复用 `search` 的 answerability checklist。它需要自己的 reflection budget、整理范围和提交策略。

---

## 14. 架构中仍然巧妙的点

### 14.1 图谱压力被释放

MAGI Memo 不要求图谱承担所有职责。

LightRAG / Neo4j 负责提供图谱能力，但最终判断、收敛和写入裁决不完全压在图谱算法上。

这避免了传统重型 GraphRAG 的几个压力：

```text
重型 community summary
全局图谱压缩
大规模关系总结
频繁重建索引
查询时复杂子图解释
```

### 14.2 三贤者不再是三个后端，而是三个 Agent

这是新版本最重要的转向。

如果三贤者只是：

```text
CASPER = vector search
MELCHIOR = graph search
BALTHASAR = LLM judge
```

那它们很容易被一个强大的图谱数据库或 GraphRAG 框架吞掉。

但如果三贤者是：

```text
CASPER = 快速激活记忆空间的子 Agent
MELCHIOR = 深入解释关系结构的子 Agent
BALTHASAR = 调度、裁决和全局收敛的子 Agent
```

那么即使它们共享同一个 Neo4j 实例，系统设计仍然成立。

区别不在底层数据库，而在认知角色、工具权限、工作空间和协商协议。

### 14.3 同层级协商比单向汇报更有意思

很多多 Agent 系统是：

```text
worker → supervisor
worker → supervisor
worker → supervisor
```

MAGI Memo 希望支持：

```text
CASPER → MELCHIOR
MELCHIOR → CASPER
BALTHASAR → CASPER
BALTHASAR → MELCHIOR
CASPER / MELCHIOR → Blackboard → BALTHASAR
```

这让记忆系统不只是下级交付结果，而是形成短促、可约束、可回放的协商过程。

### 14.4 确定性写入保护 Agentic 推理

多 Agent 推理可以是开放的，但记忆写入不能是开放的。

因此需要：

```text
开放协商
确定提交
```

GraphPatch 和 Commit Service 正是这个边界。

---

## 15. 工作计划

MAGI Memo 将按照“先验证记忆基座，再构建多 Agent 协商，最后完成外部接入”的顺序推进。

每个阶段都应形成可运行、可观察、可验证的结果，再进入下一阶段。

### 15.1 阶段一：复现 LightRAG 与 Neo4j 记忆基座

首先复现 LightRAG，并将 Neo4j 配置为其图存储实例。

这一阶段需要：

1. 跑通 LightRAG 的安装、初始化、写入与查询；
2. 验证 Neo4j storage 的接入方式和数据结构；
3. 检查 naive、local、global、hybrid、mix 等 query mode 的实际接口与返回结果；
4. 验证实体、关系、文档的新增、更新、合并和删除能力；
5. 明确 LightRAG、Neo4j 实例、连接句柄、工作目录和存储后端的生命周期；
6. 记录版本、配置、限制和已知问题，为后续封装稳定 adapter。

阶段目标是得到一条最小端到端链路：

```text
Memory input
  ↓
LightRAG
  ↓
Neo4j
  ↓
Query / Graph inspection
```

### 15.2 阶段二：动态记忆、GraphPatch 与 Commit Service

在记忆基座可用后，实现个人记忆的持续写入、更新、修正和失效。

这一阶段需要：

1. 设计 Memory、Entity、Relation、Evidence 等基础对象及其稳定 ID；
2. 验证 LightRAG 对动态图谱更新的实际支持；
3. 根据 LightRAG 与 Neo4j 的实现决定 forget 使用失效标记、后端删除或组合策略；
4. 设计 GraphPatch schema 与 patch 生命周期；
5. 实现 Commit Service，统一控制对图谱基座和存储后端的修改；
6. 加入 schema validation、幂等、版本检查、冲突检测、事务、并发控制和审计日志；
7. 验证重复提交、并发提交、失败恢复和重启后的状态一致性。

阶段目标是让开放的 Agent 推理只能提出修改建议，所有真实写入都通过受控提交完成。

### 15.3 阶段三：接入 Pi Agent 与封装 MAGI Core

在记忆读写稳定后，接入 Pi Agent，并解决现有 TypeScript 生态与 Python 记忆基座之间可能存在的兼容问题。

候选方案包括：

```text
编写 TNI 兼容层；
将记忆基座或 MAGI Core 封装为 extension；
改用支持 Python 的 Cubi Pi。
```

这一阶段需要完成：

1. 三个 Agent 的创建、启动、停止、恢复和异常处理；
2. Agent 句柄、模型、工具权限和预算的分配；
3. CASPER、MELCHIOR、BALTHASAR 独立 workspace 与文件系统边界；
4. 各自 `AGENT.md`、system prompt、工具策略和停止原则；
5. Shared Blackboard 的实现；
6. 将 MQP 定义为 Blackboard 中的标准事件协议；
7. MCC、GraphPatch、trace 与协商 session 的封装；
8. 将多 Agent 状态机设计为可配置、可替换、可测试的独立模块；
9. 优先实现共享群聊式 Blackboard，同时保留点对点调用作为可比较的通信方案。

阶段目标是形成统一的 MAGI Core：

```text
MAGI Core
  ├── Agent Runtime
  ├── Memory Backend
  ├── Blackboard / MQP
  ├── State Machine
  ├── MCC Builder
  ├── GraphPatch
  └── Trace
```

### 15.4 阶段四：验证多 Agent 核心检索机制

使用 LoCoMo 中的一段对话数据写入 MAGI Memo，并运行默认协商机制。

重点验证：

1. CASPER 能否快速激活相关记忆；
2. MELCHIOR 能否发现有效实体、关系和演化路径；
3. BALTHASAR 能否正确判断信息缺口、控制预算并生成 MCC；
4. Blackboard / MQP 是否产生了真正有价值的协商，而不是重复传递信息；
5. 系统能否在有限轮次、token、查询次数和时间预算内停止；
6. 最终证据、冲突、不确定性和回答指导是否可靠。

收敛机制应作为独立实验变量。可以比较：

```text
固定顺序状态机
动态路由状态机
共享群聊协商
点对点协商
规则驱动停止
prompt 内生停止
```

同时记录检索质量、证据质量、协商轮次、工具调用、token 成本和端到端时延。必要时优化并行度、候选池、batch gate 和 frontier budget。

### 15.5 阶段五：MCP Server、外部 Agent 与 TUI

基础能力验证通过后，将 MAGI Memo 封装为 MCP server，并正式接入 Codex 等 Host Agent。

这一阶段需要：

1. 实现 `search`、`ingest`、`update`、`forget`、`reflect`、`status` 和 `trace`；
2. 定义稳定的输入输出 schema、错误语义和兼容策略；
3. 验证外部 Agent 调用、长任务、取消、重试和并发 session；
4. 制作 MAGI Memo TUI，将 Blackboard 事件、三贤者状态、协商轮次和最终裁决可视化；
5. TUI 的视觉语言和协商节奏可以参考 EVA 中的 MAGI System，但显示层应与 MAGI Core 解耦。

阶段目标是让 Host Agent 无需了解内部实现，也能稳定调用一个可观察的 Agentic Memory Council。

### 15.6 阶段六：完善 Reflect

在 `search` 状态机稳定后，单独设计 `reflect` 的目标、预算和收敛机制。

重点包括：

```text
近期记忆整理
重复实体与关系合并
冲突和过期信息发现
稳定偏好提取
Global.md 更新候选
MemoryPatch / GraphPatch 生成
自动提交与人工审批边界
```

`reflect` 不直接复用 `search` 的 answerability 规则，而应拥有独立、可配置的状态机。

### 15.7 后续迭代

完成基础架构后，再根据实验结果逐步加入：

```text
RAG-Anything 多模态 ingestion
更丰富的 MCP 能力
更成熟的评估集与 benchmark
更多收敛策略
缓存与成本优化
安全、权限与隐私策略
长期记忆和用户模型
LoRA / Engram 等实验性记忆范式
```

MAGI Memo 的目标不是一次性完成所有记忆能力，而是在可运行、可追踪和可验证的基础上持续演化。
