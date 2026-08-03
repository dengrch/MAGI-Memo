# LightRAG Core 接口测评

测试日期：2026-07-30

## 结论

当前 LightRAG Core 可以作为 MAGI-Memo 第一阶段的记忆图谱底座。

- 完整测评结果：**33/33 通过**
- 三份样本文档均成功写入并达到 `processed`
- Neo4j 图、NanoVectorDB 向量库和 JSON 状态存储协同正常
- 五种检索模式、图谱 CRUD、显式实体合并、导出和删除均可直接通过 Core SDK 使用
- 当前主要瓶颈不是本地存储，而是智谱免费模型池的 `1305` 拥塞

测试使用独立 workspace `magi_memo_core_test`，没有修改 WebUI 使用的
`magi_memo_dev`。

## 测试环境

| 组件          | 配置                                      |
| ------------- | ----------------------------------------- |
| 调用方式      | 直接实例化`LightRAG`，不经过 API Server |
| LLM           | `glm-4.7-flash`                         |
| Embedding     | `BAAI/bge-m3`，1024 维                  |
| 图存储        | Neo4j                                     |
| 向量存储      | NanoVectorDB                              |
| KV / 状态存储 | JSON                                      |
| 输入          | `asdf.txt`、`qwer.txt`、`zxcv.txt`  |

## 覆盖范围

| 分组     | 已验证接口                                                                                          |
| -------- | --------------------------------------------------------------------------------------------------- |
| 生命周期 | `initialize_storages`、`check_and_migrate_data`、`finalize_storages`                          |
| 文档写入 | `ainsert`、`ainsert_custom_chunks`、`ainsert_custom_kg`                                       |
| 文档状态 | `aget_docs_by_track_id`、`aget_docs_by_ids`、`get_docs_by_status`、`get_processing_status`  |
| 图读取   | `get_graph_labels`、`get_knowledge_graph`、`get_entity_info`、`get_relation_info`           |
| 检索     | `aquery_data` 的 naive/local/global/hybrid/mix、`aquery`、`aquery_llm`                        |
| 图修改   | `acreate_entity`、`acreate_relation`、`aedit_entity`、`aedit_relation`、`amerge_entities` |
| 删除     | `adelete_by_relation`、`adelete_by_entity`、`adelete_by_doc_id`                               |
| 运维     | `aexport_data`、`arollback_failed_custom_chunk_patches`、`aclear_cache`                       |
| 控制面   | role 配置读取与动态更新、LLM/Embedding/Rerank 队列状态                                              |

同步接口是对应异步实现的包装器，本次没有重复执行，以避免产生相同的远程调用和写入。
`ainsert` 内部已经覆盖 enqueue 和 process pipeline。REST、WebUI、文档解析器和多模态
接口不属于本次 Core SDK 测试范围。动态 role 更新在完整复测后单独补验，
`QUERY max_async` 成功更新为 `1`。

## 关键结果

### 写入与图谱

| 用例                   |                结果 |     耗时 |
| ---------------------- | ------------------: | -------: |
| 三文档`ainsert`      |                PASS | 592.60 s |
| 自定义 chunks 增量写入 |                PASS | 243.92 s |
| 自定义 KG 写入         |                PASS |   1.30 s |
| 图标签读取             |     PASS，62 个标签 |   0.20 s |
| `林晓` 两层子图      | PASS，6 节点 / 6 边 |   0.32 s |

`ainsert` 和 custom chunks 的长耗时包含多轮 GLM `1305` 等待与重试，不代表
Neo4j 或 embedding 的实际处理耗时。Custom KG 不需要 LLM 抽取，因此明显更快。

### 检索

同一问题下的结构化检索结果如下：

| 模式   | 实体 | 关系 | Chunks |    耗时 |
| ------ | ---: | ---: | -----: | ------: |
| naive  |    0 |    0 |      6 |  0.78 s |
| local  |   20 |   33 |      2 | 97.07 s |
| global |   19 |   20 |      1 | 49.63 s |
| hybrid |   28 |   33 |      2 | 62.15 s |
| mix    |   31 |   34 |      6 |  5.92 s |

图检索模式需要先调用 LLM 提取关键词，因此耗时受免费模型池影响。`naive` 主要是
向量检索，能够更接近本地存储与 embedding 查询本身的延迟。

生成式结果额外做了事实断言：

- `aquery` 正确返回“王工在上海担任系统架构师”，重试后耗时 206.42 秒。
- `aquery_llm` 正确返回“华星机器人向复旦大学捐赠人形机器人”，耗时 14.18 秒。

### 图修改

实体与关系的创建、重命名、描述更新、显式合并、关系删除和实体删除全部通过。
单次操作通常为 0.06–2.27 秒，其中包含对应向量的重新生成。

这证明 LightRAG 已提供可供后续 commit service 封装的基础图修改原语。

## 发现与建议

### 1. 自动 merge 不是语义实体消歧

文档写入阶段的 merge 以规范化后的实体名称为键。名称完全一致时会合并描述和
来源，但不会自动判断以下名称是否指向同一实体：

- `Paul Thomas Anderson` 与 `保罗·托马斯·安德森`
- `AI音频生成` 与 `AI音频生成技术`
- `中关村` 与 `北京中关村`

显式 `amerge_entities` 已验证可用。因此 MAGI Core 后续仍需要单独的实体解析、
alias/canonical-name 或 graph patch 机制，不能把语义去重完全交给默认写入流程。

### 2. 免费 LLM 池需要更强的调用级重试

测试期间多次出现：

```text
429 / 1305: 该模型当前访问量过大，请您稍后再试
```

测试器采用以下策略后完整通过：

- LLM 调用串行化
- 调用之间至少间隔 2 秒
- LightRAG 内置重试耗尽后，最多再开启 5 个调用级重试周期

这些限制只存在于测试适配层，没有修改正式 `.env`。生产架构中建议让 commit
service 或模型网关统一处理退避、抖动、限流和 provider fallback。

### 3. Neo4j 当前未安装 APOC

`get_knowledge_graph` 尝试调用 `apoc.path.subgraphAll` 时收到
`ProcedureNotFound`，随后自动回退到基础 Cypher，功能测试仍然通过。

小规模图谱可以继续使用当前配置；进入大图和深层遍历测试前，建议安装与 Neo4j
版本匹配的 APOC 插件，再比较两种路径的性能。

### 4. `merge_strategy` 正在弃用

测试中 LightRAG 提示 `amerge_entities` 的 `merge_strategy` 参数将被弃用。
后续 MAGI graph patch 不应长期依赖这个参数的字段级行为，应由 commit service
明确生成目标实体数据。

## 复现

在仓库根目录运行：

```bash
LightRAG/.venv/bin/python test/lightrag_core_test.py
```

测试每次会清空并重建 `magi_memo_core_test`，不会清理 `magi_memo_dev`。

产物：

- `test/artifacts/core_test_results.json`：机器可读的逐项结果和耗时
- `test/artifacts/core_graph_export.md`：Core 导出接口生成的图谱快照
- `test/artifacts/rag_storage/`：独立测试 workspace 的本地存储

完整复测累计接口耗时约 1303.51 秒，即 21 分 43.51 秒。
