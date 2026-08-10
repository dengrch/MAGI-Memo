# Atom Resolution 并发实验

日期：2026-08-10  
模型：GLM-5.1（当前 `.env` 配置的 OpenAI-compatible 服务）

## 目的

比较一次 Episode 中两种 Atom 语义去重方式：

1. **整轮单请求**：所有 owner 的新 Atom 与候选放入一个上下文。
2. **按 owner 并行**：每个 owner 一个上下文，最多 4 个请求并行。

实验只覆盖 Atom resolution LLM 阶段，不执行抽取、embedding、SQLite
写入或 Neo4j 投影。

## 样本

- 4 个 owner：3 个实体 owner、1 个关系 owner。
- 8 个新 Atom：每个 owner 2 个。
- 每个新 Atom 有 2 个历史候选。
- 两种方式使用相同的 2 个近期 Episode。
- 操作覆盖 `DUPLICATE`、`REFINEMENT`、`TEMPORAL_SUCCESSOR` 和
  `INDEPENDENT`。

## 实测结果

| 指标 | 整轮单请求 | Owner 四并发 |
|---|---:|---:|
| LLM 请求数 | 1 | 4 |
| 最大同时请求 | 1 | 4 |
| 墙钟时间 | 38.960 s | 33.899 s |
| Prompt token | 2,856 | 4,977 |
| Completion token | 1,035 | 910 |
| 总 token | 3,891 | 5,887 |

Owner 四并发相对结果：

- 墙钟时间减少 5.061 秒，约快 **13.0%**，速度比为 **1.149×**。
- Prompt token 增加 **74.3%**，原因是系统说明、近期 Episode 和 JSON
  外壳被重复发送四次。
- 总 token 增加 **51.3%**。
- 四个并行请求分别耗时 16.211、22.654、30.668、33.895 秒；整体延迟由
  最慢的 owner 请求决定。
- 8/8 个 decision label 一致，8/8 个实际生效操作一致。
- 严格比较 `(decision, matched_atom_id)` 时为 7/8；差异来自一个
  `INDEPENDENT` 结果附带了候选 ID。应用层对 `INDEPENDENT` 不读取 target，
  因此不影响实际写入。

完整原始结果保存于：

`mgc-test/artifacts/atom-resolution-benchmark/result.json`

可复现实验：

```bash
./scripts/test.sh \
  tests/memory/test_atom_resolution_batching_benchmark.py \
  -s --run-integration
```

## 时间成本分析

### 整轮单请求

优势是只承担一次网络往返和一次公共上下文输入，token 成本明显更低，也能让
模型同时观察本轮所有 owner。问题是输入和结构化输出会随 Episode 规模扩大，
首 token、JSON 截断风险和整批失败影响面也随之增加。

### 按 owner 并行

owner 是 Atom 判断的天然隔离边界，因此拆分没有破坏本次实验的实际操作一致性。
但并行延迟取决于最慢请求，本次最慢 owner 已耗时 33.895 秒，接近整轮请求的
38.924 秒，所以四倍请求只换来了有限的墙钟收益。

当前配置为：

```text
MAX_ASYNC_LLM=4
MAX_PARALLEL_INSERT=2
```

若两个 Episode 同时各自产生 4 个 owner 请求，会有 8 个请求竞争最多 4 个
`deduplicate` role 执行位；其余请求进入队列。不同 role 又可能共用同一模型
端点，因此还需要端点级总并发限制，不能只依赖每个 role 自己的队列。

## 结论与建议

本次单轮实验说明：按 owner 拆分在语义上可行，但“每个 owner 一个请求、四路全开”
不是无条件更优；它以 51.3% 的总 token 增量换取了 13.0% 的延迟下降。

建议采用自适应的 **owner 隔离 + token 装箱 + 有限并行**：

1. 完全相同文本和无候选 Atom 继续本地处理，不进入 LLM。
2. 同一 owner 的所有新 Atom 必须留在同一判断单元中。
3. 小 Episode 或总 prompt 未超过预算时，继续使用一次整轮请求。
4. 超过 token/Atom/owner 阈值时，按 owner 边界装入多个 batch，而不是固定每个
   owner 一个请求。
5. 第一版建议最多 **2 个 Atom-resolution batch 并行**；取得多轮数据后再决定
   是否提升到 4。
6. `INDEPENDENT` 应在解析后强制清空 `matched_atom_id`，消除无效输出差异。
7. 实施前后应继续记录墙钟时间、prompt/completion token、队列等待、失败率和
   decision 一致性。

本结果只有一次真实调用组合，受当时服务负载和样本规模影响，适合作为工程方向
依据，不应视为稳定的性能结论。正式默认参数应使用多轮真实 Episode 数据集测量
p50/p95 后确定。
