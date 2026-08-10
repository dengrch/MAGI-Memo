# 二期第二阶段：Runtime 与公开接口

## 分层

```text
interface.MagiAPI
  └─ init / open / close / finalize / workspace
       ↓
magi_runtime.MagiRuntime
  └─ instance_init / instance_destroy / handle drain
       ↓
magi_core.MagiCore
  └─ Episode / Atom / disambiguation / retrieval / storage
```

同一进程只有一个活动 Core 实例，可以打开多个轻量句柄。关闭句柄只停止该调用方；
Runtime 销毁实例时先拒绝新调用并等待在途调用结束，再关闭 Core 和 SQLite。

三层分别稳定不同变化速度：`MagiAPI` 只定义 Agent/应用可依赖的公开协议；Runtime 管理
实例、句柄、在途调用和工作区切换；Core 专注 Episode/Atom 算法与存储。这样 Core 的
抽取与检索实现可以演进而不破坏上层 Extension，生命周期并发规则也不会继续渗入算法代码。

## 当前公开能力

```python
await api.init(workspace_id=...)
handle = await api.open(owner=...)

await handle.ingest(episode)              # 原始 Episode，执行 LLM 抽取
await handle.ingest_file(path)             # 文件只是 Episode 输入载体
await handle.ingest_extracted(memory)      # 跳过第一次抽取 LLM
await handle.search(text, mode="mix")      # 复用 LightRAG 检索
await handle.status()

await handle.close()
await api.finalize()
```

`ingest_extracted` 不调用 `ainsert_custom_kg`。它仍进入 retained durable pipeline，
保存原 Episode 和 chunk，然后从实体/关系抽取完成后的切口进入严格提交。因此实体消歧、
Atom 判定、双时间、Evidence、图/向量投影、状态跟踪和按 Episode 硬删除仍然有效。

HTTP 对应接口：

- 原始文本/文件：保留 `/documents/text`、`/documents/upload` 等接口；
- 已抽取输入：`POST /memory/ingest/extracted`；
- 检索：保留 `/query`、`/query/data`、流式 query；
- 系统状态：`GET /runtime/status`；
- 近期运行日志：`GET /runtime/logs`；
- 工作区：`GET/POST /workspaces`、`POST /workspaces/{id}/activate`。

## Workspace

`WORKSPACE_HOME`（默认建议 `./mgc-test`）是个人记忆系统的固定根目录。现有工作区
不做隐式迁移：

```text
mgc-test/                         # 默认工作区，兼容已有数据
├── ragstore/
├── inputs/
├── workspaces.json              # 本地工作区注册表
└── workspaces/
    └── <workspace-id>/
        ├── inputs/
        ├── logs/
        ├── artifacts/
        └── ragstore/
```

切换工作区会销毁当前实例并以目标目录重新初始化 Core。为了避免跨工作区写入，存在公开
句柄、后台任务或活动 pipeline 时会拒绝切换。

`workspaces.json` 使用 `active_workspace_id` 持久保存当前选择，因此从 WebUI 切换后，
服务重启仍会打开同一个工作区。配置项 `WORKSPACE` 仅在注册表尚不存在时作为旧数据的
bootstrap ID，不再锁死运行中的工作区。公开 Python API 和鉴权 REST API 均支持列出、
创建和激活工作区；创建本身不会偷偷迁移已有 Agent 句柄。

人类用户创建工作区时只需提供名称，Runtime 会生成可读且不冲突的内部 ID（例如
`Research Notes` 对应 `research-notes`，重名时追加 `-2`）。`workspace_id` 在 Python
与 REST 创建接口中均为可选参数，只留给需要稳定外部标识的 Agent 或集成使用。

```python
api.create_workspace("Research Notes")
api.create_workspace("Agent Memory", workspace_id="agent-a")
```

REST 请求对应为 `{ "name": "Research Notes", "workspace_id": "agent-a" }`；第二个字段
可以省略。

## 三期接入边界

Pi Agent 的正式接入形态是 Pi extension，不以 skill 作为运行模式。三期 extension 将
调用本阶段稳定下来的公开 API；`reflect`、主动图探索和 `update` 只保留后续设计位置，
本阶段不提前固化协议或行为。
