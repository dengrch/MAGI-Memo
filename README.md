# MAGI Memo：基于三贤者协商的个人记忆系统设计草案

## 1. 动机：从 RAG 到可协商的个人记忆系统

早期的 LLM 记忆设计大多从 RAG 开始。其基本思想是：将外部文档或历史记录切分为 chunk，通过 embedding 或关键词检索召回相关片段，再将这些片段作为 context 塞入大模型，从而让模型在回答时获得外部知识。

这种方式解决了最直接的事实召回问题，但很快暴露出一个核心缺陷：
**普通 RAG 擅长局部片段命中，却难以回答全局性问题。**

例如，当用户询问“我长期以来关注的研究方向是什么”“这个项目和我之前讨论过的方向有什么关系”“过去几个月我在某个主题上的思路如何演化”时，单纯依靠 top-k chunk 检索往往无法得到完整答案。因为这类问题需要跨越大量片段、长时间跨度和多层语义抽象，而不是命中几个局部文本片段即可解决。

为了解决这个问题，人们开始将 LLM 引入 RAG 的构建过程，为明文知识设计更有结构的存储方式。在带结构的明文存储中，最早用于解决全局信息问题的方案之一是**层次化结构**，例如树状索引或多层 summary。其思路是提前对文档或记忆进行多层抽象，将底层 chunk 逐级总结为父节点、主题节点或全局摘要。查询时，系统可以召回较高层级的抽象内容，从而把全局信息放入上下文中。

层次化结构在一定程度上缓解了普通 RAG 的局部性问题，但它也存在局限：树状结构通常预设了较强的父子层级关系，而现实语义关联往往不是树状的。不同 chunk 之间可能存在跨章节、跨时间、跨主题、跨实体的联系，这些联系很难被单一层次结构完整表达。

之后，图谱被引入记忆与 RAG 系统中。GraphRAG、LightRAG、HippoRAG 等方向试图通过实体、关系、社区和子图结构，补足传统 RAG 与层次化结构的不足。图谱结构同时解决了几个关键问题：

1. **全局信息抽象**：通过社区发现、实体聚类或图摘要形成更高层级的语义概括；
2. **多层抽象**：通过实体、关系、社区、子图等结构组织不同粒度的信息；
3. **跨上下文关联**：通过实体链接跨越原始 chunk 边界，连接分散在不同片段中的相关信息；
4. **跨 chunk 切分限制**：即使相关内容被切分到不同 chunk，只要它们共享实体或关系，仍然可以在图上重新连接。

然而，图谱方案的代价也非常明显。为了构建可用的图谱索引，系统通常需要大量 LLM 参与实体抽取、关系抽取、社区总结、层级摘要和查询时的子图解释。这使得图谱记忆在构建成本、更新成本和查询时延上都变得很重。尤其对于个人记忆系统而言，记忆并不是静态文档库，而是持续增长、持续变化、可能冲突、可能过期的动态系统。重型图谱索引在这种场景下会面临更明显的维护压力。

基于这些记忆范式，已有许多优化方向被提出。无论是更好的 hybrid retrieval、更高效的 rerank、更轻量的 graph expansion、更合理的 summary cache，还是层次化检索中的剪枝优化，其核心目标都是一致的：

> 为更加开放语义的问答构建更相关的 context，同时尽可能节约计算消耗与查询时延。

但对于真正的个人记忆系统而言，仅仅优化静态知识库的检索还不够。个人记忆系统需要进一步满足以下要求：

1. 它必须是一个可以持续增长和减少的记忆系统，而不是一次性构建的静态知识库；
2. 它必须在构建成本与查询成本之间取得平衡；
3. 它必须能够处理记忆之间的冲突、过期、重复和不确定性；
4. 它必须能够判断哪些记忆值得继续扩展，哪些记忆应该停止探索；
5. 它必须能够在不同记忆范式之间进行决策，而不是固定依赖某一种存储结构；
6. 它必须能够为上层强模型构造真正可用、可解释、可收敛的上下文。

这些需求对记忆系统提出了比普通 RAG、层次化索引或静态 GraphRAG 更高的要求。

基于此，本文提出 **MAGI Memo**：一个基于三贤者协商机制的个人记忆系统。MAGI Memo 不试图让单一记忆结构承担所有职责，而是将不同记忆范式拆分为不同认知功能：由 CASPER·3 负责直觉式快速召回，由 MELCHIOR·1 负责结构化关系分析，由 BALTHASAR·2 负责全局判断与决策。三者通过 MAGI Query Protocol（MQP）交换结构化 query，在多轮协商中完成候选记忆扩展、筛查、剪枝与收敛，最终生成 MAGI Consensus Context（MCC），为上层 AI Agent 提供可靠的个人化上下文。

## 2. MAGI 的来源与隐喻

MAGI 的灵感来自《新世纪福音战士》（Neon Genesis Evangelion）中的 MAGI System。

在 EVA 中，MAGI 是由三台超级计算机组成的决策系统，分别承载开发者赤木直子博士的三种人格侧面：

* 科学家；
* 母亲；
* 女人。

三台计算机并不是简单地并行计算，而是从不同人格视角出发，对重大问题进行判断、协商和表决。

MAGI Memo 借用的是这个核心思想：

> 一个复杂系统不应只有单一判断视角，而应由多个认知视角共同参与决策。

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

这使得 MAGI 不只是一个命名或视觉风格，而是整个系统架构的认知隐喻。

---

## 3. Overview

MAGI Memo 的目标是构建一个用于个人 AI Agent 的记忆召回内核。

它不直接作为最终回答模型，而是位于用户记忆存储与上层强模型之间，负责：

1. 读取用户问题；
2. 判断 Global summary 是否足够回答；
3. 如果不足，则启动多轮记忆协商；
4. 在 CASPER、MELCHIOR、BALTHASAR 之间通过 MQP 报文交换信息；
5. 对候选 query、实体、子图、证据进行批量筛查与剪枝；
6. 在信息收敛后生成 MAGI Consensus Context；
7. 将 MCC 交给上层强模型或母 Agent 进行最终回答。

整体流程如下：

```text
User Query
   ↓
BALTHASAR·2 Global Judgment
   ↓
如果 global 信息足够：
   ↓
直接构造 Consensus Context

如果 global 信息不足：
   ↓
进入 MAGI Deliberation Loop
   ↓
CASPER·3 快速召回候选记忆
   ↓
MELCHIOR·1 分析相关实体与子图
   ↓
BALTHASAR·2 对候选 MQP 进行潜力打分与裁决
   ↓
多轮收敛
   ↓
MAGI Consensus Context
   ↓
上层强模型回答
```

---

## 4. 三贤者模块含义

### 4.1 BALTHASAR·2：Judgment / 判断

BALTHASAR·2 是 MAGI Memo 的主控判断模块。

它对应：

```text
Global summary
Global.md
User profile
User world model
LoRA / Engram / 参数化用户模型（可选）
```

它负责：

1. 判断 Global summary 是否已经足以回答问题；
2. 决定是否启动协商流程；
3. 对 MAGI Blackboard 中积累的候选 MQP 进行潜力打分；
4. 选择哪些 MQP 值得继续发送；
5. 判断系统是否已经可以停止；
6. 构造最终的 MAGI Consensus Context。

BALTHASAR 的核心优势是具备整体性把握。

CASPER 只知道“像不像”；
MELCHIOR 只知道“连不连”；
BALTHASAR 知道“当前用户、当前任务、已有证据、长期画像和最终回答目标”。

因此，BALTHASAR 不只是普通 LLM 裁判，而是一个具备 global memory / user model 的判断模块。

它评估的不是简单相关性，而是：

```text
这个 query 是否能提高当前问题的可回答性？
这个扩展是否能减少关键不确定性？
这个候选是否符合用户长期偏好与当前任务目标？
这个信息是否值得进入最终 consensus context？
```

因此，BALTHASAR 是整个系统的收敛控制器。

---

### 4.2 CASPER·3：Intuition / 直觉

CASPER·3 对应快速混合检索模块。

它包括：

```text
1D vector retrieval
dense embedding retrieval
BM25 / sparse retrieval
hybrid retrieval
reranker
```

CASPER 的作用是快速从海量记忆片段中找出第一批候选。

它适合处理：

1. 原文事实；
2. 最近聊天记录；
3. 关键词命中；
4. 语义相似内容；
5. 初始候选记忆；
6. 被 Graph 反向请求的原文证据。

CASPER 的认知功能是“直觉”。

它不一定能完整解释为什么某条记忆重要，但它能快速回答：

> 这条记忆好像相关。

CASPER 产生的结果通常包括：

```text
memory hits
activated entities
keywords
timestamps
source snippets
candidate evidence
```

这些内容会被包装成 MQP，发送给 BALTHASAR 和 MELCHIOR。

---

### 4.3 MELCHIOR·1：Analysis / 分析

MELCHIOR·1 对应轻量图谱记忆模块。

它可以借鉴：

```text
LightRAG
GraphRAG
HippoRAG
Entity graph
Relation graph
Subgraph retrieval
```

但 MAGI Memo 中的 MELCHIOR 不追求重型全局图谱摘要。

它不是：

```text
Graph as global summarization engine
```

而是：

```text
Graph as relational router
```

即图谱只负责：

1. 实体识别；
2. 关系扩展；
3. 局部子图召回；
4. 轻量聚类；
5. 关系路径解释；
6. 判断某些实体或主题之间是否存在结构关联。

MELCHIOR 回答的问题是：

> 这些记忆之间为什么相关？
> 它们在用户长期记忆网络中处于什么位置？
> 是否存在值得继续扩展的实体、关系或子图？

MELCHIOR 的结果也会被组织成 MQP，反馈给 BALTHASAR 和 CASPER。

---

## 5. 架构设计

MAGI Memo 的整体架构由以下几个部分组成：

```text
MAGI Core
├── User Interface
├── MAGI Scheduler
├── MAGI Blackboard
├── BALTHASAR·2 Judgment Module
├── CASPER·3 Intuition Module
├── MELCHIOR·1 Analysis Module
├── MQP Protocol Layer
└── MCC Builder
```

其中：

### 5.1 MAGI Core

MAGI Core 是整个系统的运行内核。

它不是单一模型，而是由以下部分组成：

```text
User Interface
+ MAGI Scheduler
+ MAGI Blackboard
+ MQP Controller
+ Consensus Context Builder
```

MAGI Core 的职责包括：

1. 接收用户 query；
2. 初始化 BALTHASAR 判断；
3. 管理 MQP 报文队列；
4. 调度 CASPER 和 MELCHIOR；
5. 管理候选 query / evidence / entity / subgraph buffer；
6. 控制轮次、预算、停止信号；
7. 组织最终 MCC。

---

### 5.2 MAGI Blackboard

MAGI Blackboard 是一个事件缓冲区，而不是让 LLM 实时盯着看的工作台。

它负责积累：

```text
候选 MQP
候选实体
候选子图
候选证据
候选冲突
已有尝试
失败 query
不确定性
部分 consensus context
```

Blackboard 的核心作用是：

> 聚合候选、记录状态、支持批量筛查，而不是触发 LLM 对每个候选逐个判断。

---

### 5.3 MAGI Scheduler

MAGI Scheduler 是非 LLM 的轻量调度器。

它负责：

1. 收集 CASPER / MELCHIOR 产生的候选；
2. 进行去重；
3. 计算 cheap score；
4. 进行 query family grouping；
5. 判断是否达到 batch trigger；
6. 将候选压缩成 compact package；
7. 唤醒 BALTHASAR 做批量裁决。

因此，BALTHASAR 不需要一直监控 Blackboard，而是由 Scheduler 事件触发。

这可以避免：

```text
buffer 一更新
↓
LLM 看一次
↓
又更新
↓
LLM 再看一次
```

所带来的 token 和时延浪费。

---

## 6. MQP：MAGI Query Protocol

### 6.1 MQP 的由来

MAGI Memo 的核心不是三个模块本身，而是模块之间交换的结构化 query 报文。

如果没有 MQP，系统会退化成：

```text
三个检索器各查各的
最后拼接结果
```

而有了 MQP，系统变成：

```text
一个记忆视角发现缺口
↓
将缺口翻译成结构化 query
↓
另一个记忆视角根据自己的能力补全
↓
结果再影响下一轮判断
```

因此，MQP 是 MAGI Memo 的核心协议。

MQP 不只是 query string，而是一个携带意图、证据、缺口、预算和期望返回类型的结构化报文。

---

### 6.2 MQP 最小结构

一个最小 MQP 可以设计为：

```json
{
  "mqpid": "mqp_001",
  "round": 1,
  "from": "BALTHASAR",
  "to": "CASPER",
  "intent": "find_evidence",
  "query": "Find memories related to MAGI Memo and personal memory architecture.",
  "basis": [
    "Global summary is insufficient to answer the current query."
  ],
  "missing": "Need concrete historical memory evidence.",
  "expected": "evidence_list",
  "constraints": {
    "top_k": 5,
    "max_latency_ms": 150
  }
}
```

---

### 6.3 MQP 完整字段建议

更完整的 MQP 可以包括：

```json
{
  "message_id": "mqp_20260629_001",
  "round": 1,

  "from": "CASPER·3",
  "to": "MELCHIOR·1",

  "intent": "expand_relation",
  "priority": "high",

  "natural_query": "用户长期讨论 GraphRAG 和个人记忆系统之间有什么关系？",

  "structured_query": {
    "entities": ["GraphRAG", "LightRAG", "MAGI Memo", "LoRA Memory"],
    "relation_types": ["research_interest", "project_dependency", "conceptual_link"],
    "time_range": "all",
    "scope": "user_memory"
  },

  "context_basis": {
    "trigger": "CASPER retrieved multiple memories about GraphRAG, LightRAG, and MAGI Memo.",
    "supporting_items": [
      {
        "memory_id": "mem_123",
        "summary": "用户多次讨论 GraphRAG 与长期记忆系统结合。",
        "confidence": 0.91
      }
    ]
  },

  "information_gap": {
    "missing": "这些主题是否属于同一个长期研究路线？",
    "why_needed": "需要判断当前回答应按单点技术解释，还是按整体记忆系统架构展开。"
  },

  "expected_response": {
    "type": "subgraph",
    "max_items": 8,
    "must_include": ["entities", "relations", "path_explanations"],
    "allow_inference": true,
    "require_evidence": true
  },

  "budget": {
    "max_latency_ms": 120,
    "max_hops": 2,
    "top_k": 5,
    "ttl": 2
  },

  "risk_control": {
    "avoid": ["unsupported_personal_fact", "over_broad_expansion"],
    "deduplicate_against": ["mqp_20260629_000"]
  }
}
```

---

### 6.4 MQP Intent 枚举

MQP 的 intent 可以设计为有限枚举：

```text
verify_fact          核实事实
find_evidence        查找原文证据
expand_relation      扩展关系
expand_subgraph      扩展子图
resolve_conflict     解决冲突
infer_preference     推断偏好
rank_relevance       相关性排序
summarize_context    总结上下文
find_missing         寻找缺口
judge_answerability  判断是否足够回答
```

有限枚举有利于降低系统不稳定性，也方便后续调试、日志记录和评估。

---

## 7. MCC：MAGI Consensus Context

### 7.1 MCC 的由来

MAGI Memo 的目标不是让三贤者直接生成最终回答，而是生成一个高质量上下文，交给上层强模型或母 Agent。

这个上下文称为：

```text
MCC: MAGI Consensus Context
```

MCC 是三种记忆视角经过协商后形成的共识上下文。

它不同于普通 RAG context。

普通 RAG context 通常是：

```text
topK 文档片段拼接
```

而 MCC 是：

```text
经过直觉召回、结构分析、全局判断之后形成的上下文包
```

它应该包含：

1. 已确认事实；
2. 关键证据；
3. 相关实体和关系；
4. 用户长期画像相关信息；
5. 当前问题下的判断依据；
6. 仍然存在的不确定性；
7. 不应过度推断的边界。

---

### 7.2 MCC 示例结构

```json
{
  "mcc_id": "mcc_001",
  "query": "MAGI Memo 的设计是否合理？",
  "status": "consensus_reached",
  "confidence": 0.84,

  "confirmed_context": [
    {
      "claim": "用户正在设计一个名为 MAGI Memo 的个人记忆系统。",
      "source": ["CASPER", "BALTHASAR"],
      "confidence": 0.95
    },
    {
      "claim": "该系统核心是通过 MQP 在不同记忆模块之间进行 query exchange。",
      "source": ["BALTHASAR", "MELCHIOR"],
      "confidence": 0.91
    }
  ],

  "relational_context": [
    {
      "entity": "MAGI Memo",
      "relations": [
        {
          "target": "GraphRAG",
          "relation": "uses_lightweight_graph_analysis"
        },
        {
          "target": "Global Summary",
          "relation": "uses_as_judgment_layer"
        }
      ]
    }
  ],

  "user_model_context": [
    "用户倾向于工程化、架构化解释。",
    "用户关注个人长期记忆系统、GraphRAG、Agentic RAG 与参数记忆。"
  ],

  "remaining_uncertainties": [
    "Engram 模块的实际工程收益仍需实验验证。"
  ],

  "answering_guidance": [
    "回答应从系统架构和实现路径展开。",
    "避免把 LoRA 描述为事实记忆主库。",
    "强调 MQP 与收敛机制是核心创新。"
  ]
}
```

---

## 8. 大体数据结构

### 8.1 Memory Item

```json
{
  "memory_id": "mem_001",
  "content": "用户提出 MAGI Memo 的三贤者记忆系统设计。",
  "timestamp": "2026-06-29T00:00:00Z",
  "source": "conversation",
  "type": "dialogue",
  "embedding_id": "emb_001",
  "entities": ["MAGI Memo", "BALTHASAR", "CASPER", "MELCHIOR"],
  "tags": ["personal_memory", "architecture", "GraphRAG"],
  "metadata": {
    "importance": 0.86,
    "recency": 0.95
  }
}
```

---

### 8.2 Entity

```json
{
  "entity_id": "ent_magi_memo",
  "name": "MAGI Memo",
  "type": "project",
  "aliases": ["MAGI Memory System"],
  "first_seen": "2026-06-29",
  "last_seen": "2026-06-29",
  "importance": 0.95
}
```

---

### 8.3 Relation

```json
{
  "relation_id": "rel_001",
  "source_entity": "MAGI Memo",
  "target_entity": "MQP",
  "relation_type": "uses_protocol",
  "evidence_memory_ids": ["mem_001", "mem_002"],
  "confidence": 0.9,
  "last_updated": "2026-06-29"
}
```

---

### 8.4 MQP Message

```json
{
  "mqp_id": "mqp_001",
  "round": 1,
  "from": "CASPER",
  "to": "MELCHIOR",
  "intent": "expand_subgraph",
  "query": "Expand entities related to MAGI Memo and MQP.",
  "basis_memory_ids": ["mem_001", "mem_002"],
  "expected": "subgraph",
  "status": "pending",
  "priority": 0.82,
  "ttl": 2
}
```

---

### 8.5 Blackboard State

```json
{
  "session_id": "session_001",
  "user_query": "MAGI Memo 的设计是否合理？",
  "round": 2,

  "candidate_mqps": [],
  "candidate_entities": [],
  "candidate_subgraphs": [],
  "candidate_evidence": [],
  "resolved_claims": [],
  "open_uncertainties": [],
  "failed_queries": [],

  "budgets": {
    "max_round": 3,
    "max_latency_ms": 2000,
    "max_mqp": 12
  }
}
```

---

## 9. 对 Memory 设计的需求

这一部分后续可以结合实践继续细化。目前可以先确定一些基本需求。

### 9.1 统一 ID

RAG、Graph、Global summary、MQP、MCC 应尽量共享统一的 `memory_id` 或可追溯引用。

这样可以保证：

```text
检索结果可追溯
图谱关系可追溯
Global summary 可回溯
MCC 中的 claim 可验证
```

---

### 9.2 可更新

个人记忆系统必须支持持续更新。

包括：

```text
新增记忆
修改记忆
删除记忆
实体合并
关系更新
summary 更新
过期记忆降权
```

---

### 9.3 可解释

MCC 中的重要 claim 应该尽量能回溯到：

```text
原始 memory item
graph relation
global summary section
MQP 讨论过程
```

---

### 9.4 可分层

不同类型记忆应该进入不同层：

```text
一次性事件 → RAG
反复出现实体 → Graph / Entity Memory
稳定偏好 → Global summary
高频概念锚点 → Engram
长期行为模式 → LoRA
```

---

### 9.5 可接入新范式

MAGI Memo 不应该绑定某一种具体 memory backend。

未来新的记忆设计只要能实现 MQP 接口，就可以接入系统。

---

## 10. 收敛机制

收敛机制是 MAGI Memo 能否真正可用的关键。

如果没有收敛机制，三贤者之间的 query exchange 会产生 query explosion，导致时延和 token 成本失控。

---

### 10.1 BALTHASAR-led 状态机

当前较清晰的状态机如下：

```text
User Query
  ↓
BALTHASAR Global Check
  ↓
Enough?
  ├── Yes → Build MCC directly
  └── No  → Send MQP to CASPER
              ↓
          CASPER Recall
              ↓
          Activated Entities / Evidence
              ↓
          Package as MQP to Blackboard
              ↓
          Scheduler Cheap Gate
              ↓
          BALTHASAR Batch Judge
              ↓
          Send selected MQP to MELCHIOR
              ↓
          MELCHIOR Subgraph Expansion
              ↓
          Relation Gain / Graph Evidence
              ↓
          Package as MQP to Blackboard
              ↓
          Scheduler Cheap Gate
              ↓
          BALTHASAR Batch Judge
              ↓
          Enough?
              ├── Yes → MCC + Stop Signal
              └── No  → Continue until budget limit
```

核心是：

```text
BALTHASAR 启动；
CASPER 召回；
MELCHIOR 分析；
BALTHASAR 裁决；
Scheduler 控制批量候选；
Blackboard 缓冲状态；
MCC Builder 输出共识上下文。
```

---

### 10.2 局部停止与全局停止

CASPER 和 MELCHIOR 可以局部停止。

#### CASPER 停止条件

```text
没有足够新召回内容；
召回结果重复；
topK 分数低；
query novelty 低；
已达到召回预算。
```

#### MELCHIOR 停止条件

```text
没有足够相关实体；
子图扩展增益低；
关系路径置信度低；
max_hop / max_nodes 达到上限；
图扩展没有带来新的有效关系。
```

#### BALTHASAR 停止条件

```text
已经足以回答；
轮次达到限制；
时间 / token / query 预算达到限制；
剩余不确定性无法继续解决；
candidate MQP 的潜力不足；
answer draft 已经稳定。
```

CASPER / MELCHIOR 是局部停止，BALTHASAR 是全局停止。

---

### 10.3 Candidate Pool + Batch Gate

这是一个关键优化。

传统层次化检索和子图扩展的一大困难是：

> 很难判断父抽象层级、邻近实体、社区描述或关联路径是否具有继续回答 query 的潜力。

直观方案是让 LLM 判断每个候选是否值得扩展，但这会导致：

```text
候选数 × LLM call
```

从而出现严重时延问题。

MAGI Memo 中的优化方案是：

```text
cheap expansion
↓
candidate pool
↓
cheap heuristic filtering
↓
batch package
↓
BALTHASAR 一次性判断
↓
top-N MQP routing
```

也就是说：

1. 先用 cheap heuristic 找出一批潜在扩展方案；
2. 将它们积累到 Blackboard；
3. Scheduler 做去重、增益阈值过滤和 family grouping；
4. 将一批候选打包交给 BALTHASAR；
5. BALTHASAR 一次性做 batch pruning / routing；
6. 只转发 top-N MQP。

这大幅减少了 LLM 调用次数。

---

### 10.4 Frontier Budget

每轮只允许扩展有限数量的 frontier：

```text
max_frontier_per_round = N
```

无论候选池有多大，真正进入下一轮的 MQP、实体或子图都必须受限。

这类似 beam search。

---

### 10.5 MQP Beam Search

每个 MQP 都可以有 potential score：

```text
MQP_score =
relevance
× novelty
× uncertainty_reduction
× evidence_potential
× source_reliability
÷ expected_cost
```

每轮只保留 top-K MQP。

---

### 10.6 Diminishing Return Stop

每轮计算新增信息收益：

```text
gain_round_t = new_useful_information / cost
```

如果连续两轮收益下降，或收益低于阈值，则停止。

---

### 10.7 Question Coverage Checklist

BALTHASAR 可以将用户问题拆成若干 answer slots，例如：

```text
事实依据是否足够？
关系分析是否足够？
用户偏好是否足够？
实现路径是否足够？
风险边界是否足够？
```

当主要 answer slots 的 coverage 达到阈值后，可以停止。

---

### 10.8 Contradiction Queue

系统不应该为所有不确定性继续查询。

只将真正影响回答的冲突放入 contradiction queue。

例如：

```text
重要冲突：
用户到底是想用 LoRA 替代 Global.md，还是作为备选？

不重要冲突：
某个历史项目名称大小写不一致。
```

只解决高影响冲突。

---

### 10.9 MQP TTL

每个 MQP 都应有 TTL，限制其传播深度。

例如：

```json
{
  "ttl": 2
}
```

这样可以避免某个 query family 无限扩散。

---

### 10.10 Query Family 去重

不仅要对 query string 去重，还要对 query intention 去重。

例如：

```text
查 MAGI Memo 和 GraphRAG 的关系
查 GraphRAG 是否属于 MAGI Memo
查用户是否把 GraphRAG 放进 MAGI Memo
```

这些都属于同一个 query family：

```text
family: magi_graph_relation
```

同一 family 每轮最多执行一次。

---

### 10.11 Answer Draft Probe

每轮后，BALTHASAR 可以尝试生成一个短的 provisional answer outline：

```text
当前可以回答：
- ...
- ...

仍然缺少：
- ...
```

如果 outline 已经足够稳定，则停止。

这相当于：

> 能写出来就别再查了。

---

### 10.12 Stop Signal

当 BALTHASAR 判断系统已经足够回答，或达到预算上限时，它需要向 CASPER 和 MELCHIOR 发出 stop signal。

```json
{
  "type": "stop_signal",
  "from": "BALTHASAR",
  "reason": "answerability_reached",
  "final_round": 2
}
```

这可以避免其他模块继续异步发起无意义查询。

---

## 11. 架构中巧妙的点

### 11.1 图谱压力被释放

传统 GraphRAG 的压力来自：

```text
重型 community summary；
全局图谱压缩；
树状结构检索；
大规模关系总结；
图谱构建与更新成本高。
```

MAGI Memo 中，Graph 不再承担全局压缩任务，而是只负责：

```text
实体召回；
关系扩展；
轻量子图；
局部分析。
```

全局判断交给 BALTHASAR，原文事实交给 CASPER。

这使 MELCHIOR 可以保持轻量、高效、可增量更新。

---

### 11.2 多种记忆架构优势互补

MAGI Memo 将不同记忆范式拆分到不同认知功能上：

```text
CASPER：快，但浅。
MELCHIOR：结构强，但不负责全局压缩。
BALTHASAR：整体判断强，但不负责原文海量检索。
```

这避免了单一系统同时承担速度、深度、全局判断和事实存储的压力。

---

### 11.3 批量调用 LLM 做筛查 / 剪枝

MAGI Memo 不让 LLM 逐个判断每个扩展节点，而是：

```text
先 cheap expansion；
再 candidate pool；
再 batch judge。
```

这样 LLM 调用次数从：

```text
O(number of candidates)
```

降低为：

```text
O(number of batches)
```

这对时延和算力非常关键。

---

### 11.4 让具备 global 能力的模块做潜力打分

BALTHASAR 是最适合做 MQP potential scoring 的模块。

因为它拥有：

```text
用户长期画像；
当前问题意图；
已有证据；
全局上下文；
未解决不确定性；
最终回答目标。
```

普通 LLM 只能判断“这个 query 看起来是否相关”。

BALTHASAR 能判断：

```text
这个 query 对当前用户和当前回答是否重要？
它是否能减少关键不确定性？
它是否值得消耗下一轮预算？
它是否应进入最终 MCC？
```

因此，BALTHASAR 作为裁决器比裸 LLM 更合理。

---

### 11.5 不依赖人工写死分类

MAGI Memo 不依赖预定义的记忆类别、场景标签或固定映射逻辑。

它依赖的是：

```text
开放语义 query
结构化 MQP
多记忆系统协商
BALTHASAR 判断收敛
```

这使它更接近真正开放语义的个人记忆架构。

---

### 11.6 可接入未来记忆范式

新的 memory backend 只要能实现 MQP 接口，就可以接入 MAGI Memo。

例如：

```text
新的向量索引；
新的图谱算法；
新的 global summary 生成器；
新的 LoRA / Engram 参数记忆；
新的 Doc2LoRA-style adapter；
新的 episodic memory store。
```

这使 MAGI Memo 不是某个固定技术栈，而是一个异构记忆协调框架。

---

## 12. 阶段性实现建议

### 12.1 MVP 阶段

优先实现：

```text
CASPER·3:
Hybrid retrieval

MELCHIOR·1:
Lightweight entity-relation graph

BALTHASAR·2:
Global.md + small LLM judgment

MAGI Core:
Scheduler + Blackboard + MQP + MCC Builder
```

暂时不必实现复杂 LoRA / Engram。

---

### 12.2 第二阶段

加入：

```text
更完整 MQP schema；
query family 去重；
frontier budget；
batch judge；
answerability check；
MCC structured output。
```

---

### 12.3 第三阶段

再考虑：

```text
LoRA user model；
Engram concept anchor；
Doc2LoRA-style temporary adapter；
更复杂的 user world model。
```

这些属于更前沿的研究模块，不必短期强行推进。

---

## 13. 总结

MAGI Memo 的核心不是“做一个更大的记忆库”，而是：

> 让不同记忆范式通过结构化 query 报文进行讨论、互证、剪枝与收敛，最终形成可供强模型使用的个人化共识上下文。

它的三贤者可以被简洁定义为：

```text
CASPER·3：Intuition，负责快速发现候选。
MELCHIOR·1：Analysis，负责结构化解释候选。
BALTHASAR·2：Judgment，负责整体裁决候选。
```

最终系统形成：

```text
直觉提出可能性；
分析展开证据链；
判断决定是否采纳。
```

这正是 MAGI Memo 与 MAGI System 隐喻最契合的地方。

一句话概括：

> MAGI Memo 是一个由直觉、分析与判断组成的多轮协商式记忆召回引擎，通过 MQP 实现异构记忆系统之间的 query exchange，并通过 MCC 为上层 AI Agent 构造可用、可解释、可收敛的个人化上下文。

