# MAGI Memo 一期工程完成报告

完成日期：2026-07-30  
补档日期：2026-08-11  
文档状态：一期历史总结；详细数据以接口测评与测试产物为准

## 1. 完成结论

MAGI Memo 一期工程已经完成。

本期没有直接构建 Agent 协商和记忆语义层，而是先验证最关键的技术前提：LightRAG 能否作为
MAGI Memo 的图谱检索底座，并以 Neo4j 作为实际图存储，在真实模型、Embedding 和持久化后端上
完成从文档输入到图谱检索、修改和删除的完整链路。

最终结果如下：

- LightRAG Core 成功复现并可稳定初始化；
- Neo4j graph storage、NanoVectorDB 和 JSON/KV 状态存储协同工作；
- 三份样本文档完成解析、切块、实体关系抽取、图谱写入和向量写入；
- 五种检索模式和主要 Core SDK 接口完成真实端到端测试；
- 完整接口测评 **33/33 通过**；
- 工作区隔离、图谱更新边界、模型性能瓶颈和默认实体合并能力得到明确结论。

因此，一期目标“得到一条可运行、可观察、可验证的 LightRAG + Neo4j 记忆基座链路”已经达成。

## 2. 本期完成的工作

### 2.1 复现 LightRAG 运行环境

- 拉取并初始化 LightRAG；
- 使用 `uv sync --extra api` 安装 Core、API Server 和 WebUI 所需依赖；
- 跑通存储初始化、迁移检查、服务启动和资源释放；
- 保留 LightRAG Core 的直接调用方式，同时保留 API/WebUI 作为观察和管理入口；
- 梳理 `.env` 中 LLM、Embedding、存储、并发和 workspace 等关键配置。

### 2.2 接入 Neo4j 图存储

- 将 `Neo4JStorage` 配置为 LightRAG graph storage；
- 验证实体节点、语义关系边的创建、查询、更新、合并和删除；
- 确认 LightRAG workspace 在 Neo4j 中通过 workspace Label 实现逻辑隔离；
- 使用独立测试空间 `magi_memo_core_test`，避免影响开发空间 `magi_memo_dev`；
- 确认测试空间和开发空间可以位于同一 Neo4j instance/database，但查询、关系和清理均按
  workspace Label 约束。

一期首次完整测评时 APOC 尚未安装，`get_knowledge_graph` 自动回退到基础 Cypher BFS，功能仍然
通过。后续安装 APOC `2026.06.0-core` 后，已经单独验证 `apoc.path.subgraphAll` 可用，并能在
`magi_memo_dev` 中正常展开子图。GDS 同期完成安装，但不属于一期接口验收范围。

### 2.3 跑通真实文档写入链路

使用 `asdf.txt`、`qwer.txt` 和 `zxcv.txt` 三份样本文档，验证了以下流程：

```text
Document input
  → parse
  → chunk
  → LLM entity/relation extraction
  → entity/relation merge
  → Neo4j graph upsert
  → entity/relation/chunk vector upsert
  → document status processed
```

除普通文档写入外，还验证了：

- 自定义 chunks 增量写入；
- 自定义 Knowledge Graph 写入；
- track ID 和文档状态查询；
- 失败 patch 回滚、缓存清理和图谱导出。

这轮排查也厘清了此前“插入后持续出现 query 任务”的来源：写入过程不仅包含一次实体关系抽取，
还可能包含补抽取、描述合并/总结、向量生成和持久化 flush；这些内部模型调用共同决定整次写入耗时。

### 2.4 完成模型与 Embedding 性能定位

初期使用本地 Ollama 的 `qwen3:8b` 和 `qwen3-embedding`。实际运行发现：

- 本地大 Embedding 模型生成 4096 维向量时吞吐较低；
- 并发 Embedding 请求容易触发 worker timeout；
- 强模型抽取、补抽取和总结会显著拉长整次写入时间；
- 修改 Embedding 维度后，旧 NanoVectorDB 数据不能继续使用，必须清理或重建向量存储。

随后完成以下调整：

- Embedding 切换为 SiliconFlow `BAAI/bge-m3`，维度固定为 1024；
- LLM 改为外部 OpenAI-compatible API，解除本机推理资源竞争；
- 根据后端能力重新开放有限并发；
- 编写 `speedtest.py`，使用 LightRAG 真实实体关系抽取 prompt 比较模型，而不是使用简单短问答；
- 分别测量首输出、首正文、总耗时、生成 token 速度和 reasoning token，以区分网络/排队、
  prompt prefill、thinking 和正文生成耗时。

完整接口验收使用 `glm-4.7-flash`。后续性能收尾比较公司节点的 GLM-5、GLM-5.1、GLM-5.2
以及官方 `glm-4.7-flash` 后，最终运行配置选择公司节点 `GLM-5.1`：它在 GLM-5 系列中 thinking
更短、生成吞吐更高，并且没有官方免费节点频繁出现的 `429 / 1305` 拥塞问题。

### 2.5 完成 LightRAG Core 全接口测评

测评以直接实例化 Core 的方式执行，不经过 REST API Server。覆盖范围如下：

| 分组 | 验证内容 |
|---|---|
| 生命周期 | 初始化、迁移检查、最终释放 |
| 文档写入 | 普通文档、自定义 chunks、自定义 KG |
| 状态 | track ID、文档 ID、处理状态与状态计数 |
| 图读取 | labels、子图、实体信息、关系信息 |
| 检索 | `naive`、`local`、`global`、`hybrid`、`mix`、生成式查询 |
| 图修改 | 实体/关系创建、编辑、显式实体合并 |
| 删除 | 关系、实体和文档删除 |
| 运维 | 导出、失败 patch 回滚、缓存清理 |
| 控制面 | Role 配置读取/更新和模型队列状态 |

主要结果：

- 33 项 Core 接口全部通过；
- 三份样本文档均达到 `processed`；
- `林晓` 两层子图返回 6 个节点和 6 条边；
- 五种检索模式均返回符合各自设计的实体、关系或 chunk 上下文；
- 实体/关系 CRUD、显式 merge、导出和删除均完成图存储与向量存储联动。

更详细的逐项耗时、断言和发现见
[LightRAG Core 接口测评](./phase1-lightrag-core-interface-evaluation.md)。

## 3. 一期形成的关键架构结论

### 3.1 LightRAG 可以作为图谱检索底座

LightRAG 已经提供成熟的文档管线、实体关系抽取、图谱读写、向量索引和多模式检索。继续复用它
比重新实现一套 GraphRAG 基础设施更实际，一期技术选型没有偏离预期。

### 3.2 Neo4j 是图存储，但不是 LightRAG 的全部状态

一次 LightRAG 写入可能同时修改 Neo4j、实体/关系/chunk 向量、KV、文档状态和缓存。因此：

- Neo4j 原生只读查询可以直接使用；
- APOC/GDS 的只读分析或可重建派生结果可以由上层受控调用；
- 实体、关系、来源和文档等核心语义修改不能只写 Neo4j；
- 核心修改必须经过能同步维护图、向量和状态的统一写入路径。

这个结论为后续 GraphPatch、Commit Service 和 MAGI Core 的写入边界提供了依据。

### 3.3 默认 merge 不是语义实体消歧

LightRAG 默认按照规范化后的实体名称合并。同名实体能够累计描述和来源，但不同名称不会被自动判断
为同一对象，例如英文名与中文译名、简称与全称。虽然显式 `amerge_entities` 已验证可用，但真正的
alias、canonical identity 和语义去重仍需由上层记忆系统负责。

### 3.4 Workspace 是逻辑隔离，不是物理数据库隔离

`magi_memo_dev` 和 `magi_memo_core_test` 使用同一 Neo4j 服务和数据库，但通过不同 Label 构成独立
命名空间。相同实体可在两个 workspace 各有一份节点，这属于有意的测试副本，不是单个 workspace
内部的重复写入。若未来需要更强的故障隔离，可以改用独立 database 或独立 Neo4j instance。

## 4. 一期产物

- 本完成报告：`doc/phase1-completion.md`
- 详细测评：`doc/phase1-lightrag-core-interface-evaluation.md`
- 真实 Core smoke test：`scripts/legacy_live_smoke.py`
- 模型延迟诊断：`speedtest.py`
- 机器可读结果：`mgc-test/artifacts/legacy-core-test/core_test_results.json`
- 图谱导出快照：`mgc-test/artifacts/legacy-core-test/core_graph_export.md`
- 独立测试存储：`mgc-test/artifacts/legacy-core-test/ragstore/`

## 5. 阶段边界

一期只回答了一个问题：**LightRAG + Neo4j 是否足以成为 MAGI Memo 可继续演化的记忆基座？**

答案是肯定的。

Episode、Atom、Evidence、实体消歧、记忆时间、受控提交、多 Workspace Runtime 和对外接口不属于
本期实现范围。它们建立在一期已经验证的抽取、图谱、向量和检索能力之上，由后续工程继续完成。
